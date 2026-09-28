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

logger = logging.getLogger(__name__)

SQL_TEMPLATES = {
    'get_active_fault_event': (
        "SELECT phase, failure_type, injection_mode FROM FaultEvent "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_fault_event': (
        "UPDATE FaultEvent SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_fault_event': (
        "INSERT FaultEvent "
        "(id, name, failure_type, injection_mode, "
        "target_domain, target_id, target_type, "
        "target_router, target_interface, target_vrf, "
        "phase, injected_at, config, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @failure_type, @injection_mode, "
        "@target_domain, @target_id, @target_type, "
        "@target_router, @target_interface, @target_vrf, "
        "@phase, @injected_at, @config, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
}

DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))


def spanner_connect():
    spanner_client = spanner.Client(disable_builtin_metrics=True)
    instance = spanner_client.instance('networktopology-instance')
    db_name = os.environ.get('GOOGLE_SPANNER_OPS_DATABASE', 'agentops-db')
    return instance.database(db_name)


lock = threading.Lock()
db_container = {'db': spanner_connect()}


# ------------------------------------------
# Sync FaultEvent (SCD Type 2)
# Tracks NetworkFailure injection lifecycle.
# Each phase transition (Injecting → Active → Restored) creates a new SCD row.
# ------------------------------------------
async def sync_fault_event(name: str, spec: dict, phase: str, injected_at=None):
    """Write or update a FaultEvent row in agentops-db for a NetworkFailure resource."""
    event_id = f"fault:{name}"
    failure_type = spec.get('failureType', 'UNKNOWN')
    injection_mode = spec.get('injectionMode', 'direct')
    target = spec.get('target', {})

    # Transport-specific target fields
    target_router = target.get('router', '')
    target_interface = target.get('interface', '')
    target_vrf = target.get('vrf', '')

    # Unified cross-domain fields — NetworkFailure is always transport domain
    target_domain = 'transport'
    target_id = target_router or ''
    target_type = 'router' if target_router else ''

    config_json = json.dumps({
        'failureType': failure_type,
        'injectionMode': injection_mode,
        'target': dict(target),
        'parameters': dict(spec.get('parameters', {})),
    })

    def sql_upsert(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_fault_event'],
            params={'id': event_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            if row[0] == phase:
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_fault_event'],
                    params={'id': event_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_fault_event'],
                params={
                    'id': event_id,
                    'name': name,
                    'failure_type': failure_type,
                    'injection_mode': injection_mode,
                    'target_domain': target_domain,
                    'target_id': target_id,
                    'target_type': target_type,
                    'target_router': target_router,
                    'target_interface': target_interface,
                    'target_vrf': target_vrf,
                    'phase': phase,
                    'injected_at': injected_at or '',
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'failure_type': spanner.param_types.STRING,
                    'injection_mode': spanner.param_types.STRING,
                    'target_domain': spanner.param_types.STRING,
                    'target_id': spanner.param_types.STRING,
                    'target_type': spanner.param_types.STRING,
                    'target_router': spanner.param_types.STRING,
                    'target_interface': spanner.param_types.STRING,
                    'target_vrf': spanner.param_types.STRING,
                    'phase': spanner.param_types.STRING,
                    'injected_at': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced FaultEvent {event_id} (phase={phase}, type={failure_type})")
    except Exception as e:
        error_msg = f"Failed to sync FaultEvent {event_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def close_fault_event(name: str):
    """Close the active FaultEvent row in Spanner (SCD Type 2)."""
    event_id = f"fault:{name}"
    logger.debug(f"Closing FaultEvent {event_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['close_fault_event'],
            params={'id': event_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed FaultEvent {event_id}")
    except Exception as e:
        error_msg = f"Failed to close FaultEvent {event_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)
