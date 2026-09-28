# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import kopf
import logging
import json
import os
import threading
from google.cloud import spanner
from utils.compute import sanitize_k8s_body

logger = logging.getLogger(__name__)

SQL_TEMPLATES = {
    # CellSite SCD
    'get_active_cellsite': (
        "SELECT phase, config FROM CellSite WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_cellsite': (
        "UPDATE CellSite SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_cellsite': (
        "INSERT CellSite "
        "(id, name, container_name, gnb_id, nci, gnb_id_length, mcc, mnc, tac, "
        "phase, config, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @container_name, @gnb_id, @nci, @gnb_id_length, "
        "@mcc, @mnc, @tac, @phase, @config, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_cellsite': (
        "UPDATE CellSite SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),

    # UE SCD
    'get_active_ue': (
        "SELECT apn, sst, sd FROM UE "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_ue': (
        "UPDATE UE SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_ue': (
        "INSERT UE "
        "(id, cellsite_id, imsi, apn, sst, sd, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @cellsite_id, @imsi, @apn, @sst, @sd, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'close_ues_by_cellsite': (
        "UPDATE UE SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE cellsite_id = @cellsite_id AND valid_end_ts IS NULL"
    ),
}

DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))


def spanner_connect():
    spanner_client = spanner.Client(disable_builtin_metrics=True)
    instance = spanner_client.instance('networktopology-instance')
    db_name = os.environ.get('GOOGLE_SPANNER_RADIO_DATABASE', 'radiotopology-db')
    return instance.database(db_name)


lock = threading.Lock()
db_container = {'db': spanner_connect()}


# ------------------------------------------
# Sync CellSite (SCD Type 2) + UEs
# ------------------------------------------
async def sync_cellsite(body, spec, name, uid, logger):
    """Sync a CellSite CRD to Spanner including UEs."""
    logger.debug(f"Syncing CellSite {name}")

    cellsite_id = f"cellsite:{name}"
    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown')

    container_name = spec.get('container_name', '')

    gnb_spec = spec.get('gnb', {})
    gnb_id = str(gnb_spec.get('gnb_id', ''))
    nci = str(gnb_spec.get('nci', ''))
    gnb_id_length = gnb_spec.get('gnb_id_length')
    mcc = str(gnb_spec.get('mcc', ''))
    mnc = str(gnb_spec.get('mnc', ''))
    tac_raw = gnb_spec.get('tac')
    tac = int(tac_raw) if tac_raw is not None else None

    body_dict = dict(body) if not isinstance(body, dict) else body
    sanitized_body = sanitize_k8s_body(body_dict)
    config_json = json.dumps(sanitized_body)

    def sql_upsert(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_cellsite'],
            params={'id': cellsite_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            ex_phase, ex_config = row
            # Only insert if phase OR spec changed (ignore status.message changes)
            ex_spec = ex_config.get('spec', {})
            new_spec = sanitized_body.get('spec', {})
            if ex_phase == phase and ex_spec == new_spec:
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_cellsite'],
                    params={'id': cellsite_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_cellsite'],
                params={
                    'id': cellsite_id,
                    'name': name,
                    'container_name': container_name,
                    'gnb_id': gnb_id,
                    'nci': nci,
                    'gnb_id_length': gnb_id_length,
                    'mcc': mcc,
                    'mnc': mnc,
                    'tac': tac,
                    'phase': phase,
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'container_name': spanner.param_types.STRING,
                    'gnb_id': spanner.param_types.STRING,
                    'nci': spanner.param_types.STRING,
                    'gnb_id_length': spanner.param_types.INT64,
                    'mcc': spanner.param_types.STRING,
                    'mnc': spanner.param_types.STRING,
                    'tac': spanner.param_types.INT64,
                    'phase': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced CellSite {cellsite_id} (phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync CellSite {cellsite_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)

    # Sync UEs
    ues = spec.get('ues', [])
    if ues:
        await _sync_ues(cellsite_id, name, ues, logger)


async def _sync_ues(cellsite_id, cellsite_name, ues, logger):
    """Write UE rows for all UEs declared in this CellSite spec."""
    def sql_upsert_ues(transaction):
        for ue_spec in ues:
            imsi = str(ue_spec.get('imsi', ''))
            if not imsi:
                continue

            ue_id = f"ue:{cellsite_name}:{imsi}"
            apn = ue_spec.get('apn', '')
            sst = ue_spec.get('sst')
            sd = ue_spec.get('sd', '')

            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_ue'],
                params={'id': ue_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                ex_apn, ex_sst, ex_sd = row
                # UE rows only change when spec fields change
                if ex_apn == apn and ex_sst == sst and ex_sd == sd:
                    need_insert = False
                else:
                    transaction.execute_update(
                        SQL_TEMPLATES['close_ue'],
                        params={'id': ue_id},
                        param_types={'id': spanner.param_types.STRING}
                    )

            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_ue'],
                    params={
                        'id': ue_id,
                        'cellsite_id': cellsite_id,
                        'imsi': imsi,
                        'apn': apn,
                        'sst': sst,
                        'sd': sd,
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'cellsite_id': spanner.param_types.STRING,
                        'imsi': spanner.param_types.STRING,
                        'apn': spanner.param_types.STRING,
                        'sst': spanner.param_types.INT64,
                        'sd': spanner.param_types.STRING,
                    }
                )

    try:
        db_container['db'].run_in_transaction(sql_upsert_ues)
        logger.debug(f"Synced {len(ues)} UE(s) for {cellsite_id}")
    except Exception as e:
        logger.warning(f"Failed to sync UEs for {cellsite_id}: {e}")


async def delete_cellsite(uid, name=None):
    """Close CellSite and its UEs."""
    cellsite_id = f"cellsite:{name}" if name else uid
    logger.debug(f"Deleting CellSite {cellsite_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_cellsite'],
            params={'id': cellsite_id},
            param_types={'id': spanner.param_types.STRING}
        )
        transaction.execute_update(
            SQL_TEMPLATES['close_ues_by_cellsite'],
            params={'cellsite_id': cellsite_id},
            param_types={'cellsite_id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed CellSite {cellsite_id}")
    except Exception as e:
        error_msg = f"Failed to delete CellSite {cellsite_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)
