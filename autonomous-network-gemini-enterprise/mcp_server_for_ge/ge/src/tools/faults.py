# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
from typing import Annotated
from google.cloud import spanner
from mcp.types import ToolAnnotations
from utils.globals import networkagent_mcp
from utils.database import get_database

logger = logging.getLogger(__name__)

# FaultEvent lives in agentops-db and is written by the controller's
# NetworkFailure handler (controller/src/graph/ops_tasks.py) as SCD Type 2
# history: each phase transition closes the previous row by stamping
# valid_end_ts and inserts a new one. So the row with valid_end_ts IS NULL is
# the current state of that fault, and a fault whose rows are all closed has
# been restored and removed.
_COLUMNS = (
    "id, name, failure_type, injection_mode, target_domain, target_id, "
    "target_type, target_router, target_interface, target_vrf, phase, "
    "injected_at, config, valid_start_ts, valid_end_ts"
)

# The only phases the controller writes: Injecting while the playbook runs,
# Active once the fault is in place. Restoration closes the row rather than
# writing a third phase.
_ACTIVE_PHASES = ('Injecting', 'Active')


def _config_of(value):
    """`config` arrives as a str, a dict or None depending on the driver."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(ts):
    return ts.isoformat() if hasattr(ts, 'isoformat') else (str(ts) if ts else None)


@networkagent_mcp.tool()
def query_fault_events(
    target_node: Annotated[str, "The host name of the target node or router"] = None,
    limit: Annotated[int, "Maximum number of fault events to return"] = 20
) -> str:
    """Query active or historical chaos engineering fault injection events (packet loss, latency spikes, link failures)."""
    logger.info(f"query_fault_events: {target_node}, {limit}")

    params = {"limit": int(limit)}
    param_types = {"limit": spanner.param_types.INT64}
    where_sql = ""
    if target_node:
        # target_router is the transport-specific column and target_id the
        # cross-domain one; transport populates both, the other domains only
        # target_id. Match either so the filter works whatever injected it.
        where_sql = "WHERE (target_router = @target OR target_id = @target)"
        params["target"] = target_node
        param_types["target"] = spanner.param_types.STRING

    sql = (
        f"SELECT {_COLUMNS} FROM FaultEvent {where_sql} "
        "ORDER BY valid_start_ts DESC LIMIT @limit"
    )

    try:
        faults = []
        with get_database('ops').snapshot(multi_use=True) as snapshot:
            for row in snapshot.execute_sql(sql, params=params, param_types=param_types):
                (fault_id, fault_name, failure_type, injection_mode, target_domain,
                 target_id, target_type, target_router, target_interface,
                 target_vrf, phase, injected_at, config, start_ts, end_ts) = row

                # A closed row is history whatever phase it recorded, so both
                # conditions matter.
                active = end_ts is None and phase in _ACTIVE_PHASES
                faults.append({
                    "fault_id": fault_id,
                    "name": fault_name,
                    "type": failure_type,
                    "status": "ACTIVE" if active else "RESOLVED",
                    "phase": phase,
                    "injection_mode": injection_mode,
                    "target": target_router or target_id or "",
                    "target_domain": target_domain,
                    "target_type": target_type,
                    "target_interface": target_interface or None,
                    "target_vrf": target_vrf or None,
                    "injected_at": injected_at or _iso(start_ts),
                    "restored_at": _iso(end_ts),
                    "parameters": _config_of(config).get("parameters", {}),
                })

        if not faults:
            # Say so in words. This tool used to return two invented faults
            # unconditionally, which taught agents that a fault is always
            # present -- they reported 'chaos-inj-8831 on pe2' as a live
            # incident on a network that had never had a fault injected.
            scope = f" targeting '{target_node}'" if target_node else ""
            return (
                f"No fault injection events{scope} are recorded. The controller "
                "writes a FaultEvent row for every NetworkFailure resource it "
                "processes, so an empty result means no fault has been injected "
                "(not that the query failed)."
            )
        return json.dumps(faults, indent=2)
    except Exception as e:
        logger.error(f"Error in query_fault_events: {e}")
        return json.dumps({"error": str(e)})
