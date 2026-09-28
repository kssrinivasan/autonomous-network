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
    # FiveGCore SCD
    'get_active_fivegcore': (
        "SELECT phase, config FROM FiveGCore WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_fivegcore': (
        "UPDATE FiveGCore SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_fivegcore': (
        "INSERT FiveGCore "
        "(id, name, phase, mongodb_uri, config, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @phase, @mongodb_uri, @config, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_fivegcore': (
        "UPDATE FiveGCore SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),

    # NetworkFunction SCD
    'get_active_nf': (
        "SELECT nf_type, phase, ip_address, config FROM NetworkFunction "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_nf': (
        "UPDATE NetworkFunction SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_nf': (
        "INSERT NetworkFunction "
        "(id, name, nf_type, fivegcore_id, container_name, phase, ip_address, "
        "config, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @nf_type, @fivegcore_id, @container_name, @phase, "
        "@ip_address, @config, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_nf': (
        "UPDATE NetworkFunction SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'get_nfs_by_core': (
        "SELECT id, nf_type FROM NetworkFunction "
        "WHERE fivegcore_id = @fivegcore_id AND valid_end_ts IS NULL"
    ),
    'get_nrf_by_core': (
        "SELECT id FROM NetworkFunction "
        "WHERE fivegcore_id = @fivegcore_id AND nf_type = 'nrf' AND valid_end_ts IS NULL"
    ),
}

DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))


def spanner_connect():
    spanner_client = spanner.Client(disable_builtin_metrics=True)
    instance = spanner_client.instance('networktopology-instance')
    db_name = os.environ.get('GOOGLE_SPANNER_CORE_DATABASE', 'coretopology-db')
    return instance.database(db_name)


lock = threading.Lock()
db_container = {'db': spanner_connect()}


# ------------------------------------------
# Sync FiveGCore (SCD Type 2)
# ------------------------------------------
async def sync_fivegcore(body, spec, name, uid, logger):
    """Sync a FiveGCore CRD to the FiveGCore Spanner table (SCD Type 2)."""
    logger.debug(f"Syncing FiveGCore {name}")

    core_id = f"fivegcore:{name}"
    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown')

    mongodb_uri = spec.get('mongodbUri', '')

    body_dict = dict(body) if not isinstance(body, dict) else body
    sanitized_body = sanitize_k8s_body(body_dict)
    config_json = json.dumps(sanitized_body)

    def sql_upsert(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_fivegcore'],
            params={'id': core_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            ex_phase, ex_config = row
            # Only insert new row if phase OR spec changed (ignore status.message changes)
            ex_spec = ex_config.get('spec', {})
            new_spec = sanitized_body.get('spec', {})
            if ex_phase == phase and ex_spec == new_spec:
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_fivegcore'],
                    params={'id': core_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_fivegcore'],
                params={
                    'id': core_id,
                    'name': name,
                    'phase': phase,
                    'mongodb_uri': mongodb_uri,
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'phase': spanner.param_types.STRING,
                    'mongodb_uri': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced FiveGCore {core_id} (phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync FiveGCore {core_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def delete_fivegcore(uid, name=None):
    """Close the active FiveGCore row in Spanner (SCD Type 2)."""
    core_id = f"fivegcore:{name}" if name else uid
    logger.debug(f"Deleting FiveGCore {core_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_fivegcore'],
            params={'id': core_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed FiveGCore {core_id}")
    except Exception as e:
        error_msg = f"Failed to delete FiveGCore {core_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


# ------------------------------------------
# Sync NetworkFunction (SCD Type 2)
# ------------------------------------------
async def sync_network_function(body, spec, name, uid, logger):
    """Sync a NetworkFunction CRD to Spanner."""
    logger.debug(f"Syncing NetworkFunction {name}")

    nf_id = f"nf:{name}"
    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown')
    ip_address = status_obj.get('ip_address', '') or spec.get('ip_address', '')

    nf_type = spec.get('nf_type', '').lower()
    container_name = spec.get('container_name', '')

    # Derive fivegcore_id from owner reference (FiveGCore creates NF as child CR)
    owner_refs = body.get('metadata', {}).get('ownerReferences', [])
    fivegcore_name = next(
        (r['name'] for r in owner_refs if r.get('kind') == 'FiveGCore'), None
    )
    fivegcore_id = f"fivegcore:{fivegcore_name}" if fivegcore_name else None

    body_dict = dict(body) if not isinstance(body, dict) else body
    sanitized_body = sanitize_k8s_body(body_dict)
    config_json = json.dumps(sanitized_body)

    def sql_upsert(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_nf'],
            params={'id': nf_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            ex_type, ex_phase, ex_ip, ex_config = row
            # Only insert if phase, spec, or ip_address changed (ignore status.message changes)
            ex_spec = ex_config.get('spec', {})
            new_spec = sanitized_body.get('spec', {})
            if (ex_type == nf_type and ex_phase == phase and
                    ex_ip == ip_address and ex_spec == new_spec):
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_nf'],
                    params={'id': nf_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_nf'],
                params={
                    'id': nf_id,
                    'name': name,
                    'nf_type': nf_type,
                    'fivegcore_id': fivegcore_id,
                    'container_name': container_name,
                    'phase': phase,
                    'ip_address': ip_address,
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'nf_type': spanner.param_types.STRING,
                    'fivegcore_id': spanner.param_types.STRING,
                    'container_name': spanner.param_types.STRING,
                    'phase': spanner.param_types.STRING,
                    'ip_address': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced NetworkFunction {nf_id} (type={nf_type}, phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync NetworkFunction {nf_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def delete_network_function(uid, name=None):
    """Close the active NetworkFunction row."""
    nf_id = f"nf:{name}" if name else uid
    logger.debug(f"Deleting NetworkFunction {nf_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_nf'],
            params={'id': nf_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed NetworkFunction {nf_id}")
    except Exception as e:
        error_msg = f"Failed to delete NetworkFunction {nf_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)
