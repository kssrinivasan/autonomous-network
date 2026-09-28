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
    'get_active_flow': "SELECT phase FROM TrafficFlow WHERE id = @id AND valid_end_ts IS NULL",
    'close_flow': "UPDATE TrafficFlow SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
    'insert_flow': (
        "INSERT TrafficFlow (id, name, src_device_id, dst_device_id, phase, config, "
        "valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @src_device_id, @dst_device_id, @phase, @config, "
        "PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_flow': "UPDATE TrafficFlow SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
}

DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))


def spanner_connect():
    spanner_client = spanner.Client(disable_builtin_metrics=True)
    instance = spanner_client.instance('networktopology-instance')
    db_name = os.environ.get('GOOGLE_SPANNER_SERVICE_DATABASE', 'servicetopology-db')
    return instance.database(db_name)


lock = threading.Lock()
db_container = {'db': spanner_connect()}


# ------------------------------------------
# Sync TrafficFlow (SCD Type 2)
# ------------------------------------------
async def sync_traffic_flow(body, spec, name, uid, logger):
    """Sync a TrafficTest CRD to the TrafficFlow Spanner table (SCD Type 2).

    A new SCD row is written whenever the phase changes.
    Device IDs use the host:{name} convention matching ComputeHost.
    """
    logger.debug(f"Syncing TrafficFlow for TrafficTest {name}")

    flow_id = f"flow:{name}"
    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown').lower()

    source_devices = spec.get('source_devices', [])
    destination_device = spec.get('destination_device', '')

    src_device_id = f"host:{source_devices[0]}" if source_devices else None
    dst_device_id = f"host:{destination_device}" if destination_device else None

    body_dict = dict(body) if not isinstance(body, dict) else body
    config_json = json.dumps(sanitize_k8s_body(body_dict))

    def sql_upsert_flow(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_flow'],
            params={'id': flow_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            if row[0] == phase:
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_flow'],
                    params={'id': flow_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_flow'],
                params={
                    'id': flow_id,
                    'name': name,
                    'src_device_id': src_device_id,
                    'dst_device_id': dst_device_id,
                    'phase': phase,
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'src_device_id': spanner.param_types.STRING,
                    'dst_device_id': spanner.param_types.STRING,
                    'phase': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert_flow)
        logger.info(f"Synced TrafficFlow {flow_id} (phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync TrafficFlow {flow_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def delete_traffic_flow(name=None, uid=None):
    """Close the active TrafficFlow row in Spanner (SCD Type 2)."""
    flow_id = f"flow:{name}" if name else uid
    logger.debug(f"Deleting TrafficFlow {flow_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_flow'],
            params={'id': flow_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed TrafficFlow {flow_id}")
    except Exception as e:
        error_msg = f"Failed to delete TrafficFlow {flow_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)
