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

"""
MCP tools for Configuration Drift Detection & Remediation.

These tools enable graph-aware config comparison across network elements,
tracing cascading impacts through dependent elements (BGP peers, VRF members,
linked interfaces, L3VPN participants), and suggesting correct configurations.

Works with the SCD Type 2 temporal model — every config change in Spanner
creates a timestamped version, enabling precise before/after comparison.
"""

from typing import Annotated
import logging
import json
import datetime
import copy
import utils.globals as globals
from mcp.types import ToolAnnotations
from google.cloud import spanner
from utils.database import get_database
from utils.hosts import hosts_on_router

logger = logging.getLogger(__name__)


def _query(sql, params, param_types, domain='transport'):
    """Run a read-only query against ``domain``.

    Every table this module reads lives in transport, so the default is right
    for all current callers. The one infra read it needs -- compute hosts, in
    ``analyze_router_dependencies`` -- goes through ``utils.hosts`` instead,
    which does its own infra routing.

    It previously built its own Spanner client at import time, bypassing the
    shared factory and making the module unimportable without credentials.
    """
    with get_database(domain).snapshot() as snap:
        return list(snap.execute_sql(sql, params=params, param_types=param_types))


def _parse_ts(ts_str):
    """Parse an ISO-8601 timestamp string into a datetime object."""
    if ts_str.endswith('Z'):
        ts_str = ts_str[:-1] + '+00:00'
    return datetime.datetime.fromisoformat(ts_str)


def _deep_diff(old, new, path=""):
    """Recursively diff two JSON-like objects and return structured changes.

    Returns a list of dicts, each with:
      - path: dot-separated field path (e.g. "rt_import", "neighbors[0].peer")
      - type: "added", "removed", "changed"
      - old_value: value in old (None if added)
      - new_value: value in new (None if removed)
    """
    diffs = []

    if old is None and new is None:
        return diffs
    if old is None:
        diffs.append({"path": path or "(root)", "type": "added", "old_value": None, "new_value": new})
        return diffs
    if new is None:
        diffs.append({"path": path or "(root)", "type": "removed", "old_value": old, "new_value": None})
        return diffs

    if type(old) != type(new):
        diffs.append({"path": path or "(root)", "type": "changed", "old_value": old, "new_value": new})
        return diffs

    if isinstance(old, dict):
        all_keys = set(list(old.keys()) + list(new.keys()))
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            if key not in old:
                diffs.append({"path": child_path, "type": "added", "old_value": None, "new_value": new[key]})
            elif key not in new:
                diffs.append({"path": child_path, "type": "removed", "old_value": old[key], "new_value": None})
            else:
                diffs.extend(_deep_diff(old[key], new[key], child_path))
    elif isinstance(old, list):
        # For lists, compare element by element
        max_len = max(len(old), len(new))
        for i in range(max_len):
            child_path = f"{path}[{i}]"
            if i >= len(old):
                diffs.append({"path": child_path, "type": "added", "old_value": None, "new_value": new[i]})
            elif i >= len(new):
                diffs.append({"path": child_path, "type": "removed", "old_value": old[i], "new_value": None})
            else:
                diffs.extend(_deep_diff(old[i], new[i], child_path))
    else:
        # Scalar comparison
        if old != new:
            diffs.append({"path": path or "(root)", "type": "changed", "old_value": old, "new_value": new})

    return diffs


def _filter_config_noise(config):
    """Remove metadata/status/applied_config fields that bloat token usage."""
    if not isinstance(config, dict):
        return config
    filtered = copy.deepcopy(config)
    # Remove K8s metadata and status — we only care about spec
    for noise_key in ['metadata', 'status', 'apiVersion', 'kind']:
        filtered.pop(noise_key, None)
    # Strip VyOS applied_config from spec.status (huge, not useful for drift detection)
    if 'spec' in filtered and isinstance(filtered['spec'], dict):
        filtered['spec'].pop('status', None)
        filtered['spec'].pop('applied_config', None)
    # Also strip if at top level
    filtered.pop('applied_config', None)
    return filtered


# ──────────────────────────────────────────────────────────────────────────────
# Tool 1: getElementConfig
# ──────────────────────────────────────────────────────────────────────────────

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getElementConfig(
    element_type: Annotated[str, "Type of network element: 'router', 'interface', 'vrf', 'bgp_session', 'link', 'l3vpn_service'"],
    element_id: Annotated[str, "The element ID in Spanner (e.g. 'router:pe1', 'vrf:pe3:BLUE_SPOKE'). Use getRelatedElements to discover IDs."],
    stable_timestamp: Annotated[str, "ISO-8601 timestamp of the 'last known good' state to compare against. Use getLastStableTimestamp to find this. Pass 'none' to skip comparison and return only current config."] = "none"
) -> str:
    """
    Fetch the current configuration of a network element and optionally compare it
    against its configuration at a specific 'stable' timestamp.

    Uses SCD Type 2 versioning to retrieve exact config at both points in time.
    Returns the current config, the stable config (if requested), and a structured
    diff showing exactly what fields changed.

    This is the core tool for configuration drift detection. After identifying
    related elements with getRelatedElements, call this for each element to find
    what changed.

    Returns:
        JSON object with 'current_config', 'stable_config' (if timestamp provided),
        and 'diff' (list of field-level changes).
    """
    logger.info(f"getElementConfig: type={element_type}, id={element_id}, stable_ts={stable_timestamp}")

    # Map element types to their SQL queries and config column positions
    type_queries = {
        'router': {
            'current': "SELECT config, status FROM PhysicalRouter WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT config, status FROM PhysicalRouter WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': 0, 'status_col': 1
        },
        'interface': {
            'current': "SELECT speed, media_type, ip_address, mac_address, status FROM PhysicalInterface WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT speed, media_type, ip_address, mac_address, status FROM PhysicalInterface WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': -1  # special handling — fields are columns, not JSON
        },
        'vrf': {
            'current': "SELECT config, status, name, rd FROM VRF WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT config, status, name, rd FROM VRF WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': 0, 'status_col': 1
        },
        'bgp_session': {
            'current': "SELECT config, status, local_as, remote_as, peer_ip FROM BGPSession WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT config, status, local_as, remote_as, peer_ip FROM BGPSession WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': 0, 'status_col': 1
        },
        'link': {
            'current': "SELECT properties, status, name, bandwidth FROM PhysicalLink WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT properties, status, name, bandwidth FROM PhysicalLink WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': 0, 'status_col': 1
        },
        'l3vpn_service': {
            'current': "SELECT config, status, name, service_type, topology FROM L3VPNService WHERE id = @id AND valid_end_ts IS NULL",
            'at_time': "SELECT config, status, name, service_type, topology FROM L3VPNService WHERE id = @id AND valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)",
            'config_col': 0, 'status_col': 1
        }
    }

    if element_type not in type_queries:
        return json.dumps({"error": f"Unknown element type '{element_type}'. Valid types: {list(type_queries.keys())}"})

    q = type_queries[element_type]

    try:
        result = {"element_type": element_type, "element_id": element_id}

        # Fetch current config
        rows = _query(
                q['current'],
                params={'id': element_id},
                param_types={'id': spanner.param_types.STRING}
        )
        row = rows[0] if rows else None

        if not row:
            return json.dumps({"error": f"Element '{element_id}' not found (no active row)", "element_type": element_type})

        if element_type == 'interface':
            current_config = {
                "speed": row[0], "media_type": row[1], "ip_address": row[2],
                "mac_address": row[3], "status": row[4]
            }
        else:
            raw_config = row[q['config_col']]
            if isinstance(raw_config, str):
                try:
                    current_config = json.loads(raw_config)
                except json.JSONDecodeError:
                    current_config = raw_config
            else:
                current_config = raw_config
            current_config = _filter_config_noise(current_config) if isinstance(current_config, dict) else current_config

        result["current_config"] = current_config
        result["current_status"] = row[q.get('status_col', -1)] if q.get('status_col') is not None and element_type != 'interface' else current_config.get('status', 'unknown')

        # Fetch stable config if timestamp provided
        if stable_timestamp and stable_timestamp.lower() != 'none':
            ts = _parse_ts(stable_timestamp)

            rows = _query(
                q['at_time'],
                params={'id': element_id, 'ts': ts},
                param_types={'id': spanner.param_types.STRING, 'ts': spanner.param_types.TIMESTAMP}
            )
            stable_row = rows[0] if rows else None

            if stable_row:
                if element_type == 'interface':
                    stable_config = {
                        "speed": stable_row[0], "media_type": stable_row[1], "ip_address": stable_row[2],
                        "mac_address": stable_row[3], "status": stable_row[4]
                    }
                else:
                    raw_stable = stable_row[q['config_col']]
                    if isinstance(raw_stable, str):
                        try:
                            stable_config = json.loads(raw_stable)
                        except json.JSONDecodeError:
                            stable_config = raw_stable
                    else:
                        stable_config = raw_stable
                    stable_config = _filter_config_noise(stable_config) if isinstance(stable_config, dict) else stable_config

                result["stable_config"] = stable_config
                result["stable_timestamp"] = stable_timestamp

                # Compute diff
                diff = _deep_diff(stable_config, current_config)
                result["diff"] = diff
                result["has_changes"] = len(diff) > 0
                result["change_count"] = len(diff)
            else:
                result["stable_config"] = None
                result["stable_note"] = f"No version found at timestamp {stable_timestamp} — element may not have existed yet"

        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error in getElementConfig: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────────────────────────────────────
# Tool 2: getRelatedElements
# ──────────────────────────────────────────────────────────────────────────────

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getRelatedElements(
    element_id: Annotated[str, "Starting element ID (e.g. 'router:pe3', 'vrf:pe3:BLUE_SPOKE')"],
    element_type: Annotated[str, "Type: 'router', 'vrf', 'interface', 'l3vpn_service'"],
    depth: Annotated[int, "How many hops to traverse (1=direct neighbors, 2=neighbors of neighbors). Default 2."] = 2
) -> str:
    """
    Walk the Spanner property graph from a starting element and return all
    dependent/related elements within the specified depth.

    For a router, this finds: interfaces, linked peer routers, VRFs, BGP sessions,
    L3VPN services, and other routers in the same VPN.

    For a VRF, this finds: the parent router, the L3VPN service, other VRFs in
    the same VPN (on other routers), and their BGP sessions.

    Returns:
        JSON object with categorized lists of related element IDs and names,
        grouped by type, ready to be passed to getElementConfig for diffing.
    """
    logger.info(f"getRelatedElements: id={element_id}, type={element_type}, depth={depth}")

    try:
        related = {
            "starting_element": {"id": element_id, "type": element_type},
            "routers": [],
            "interfaces": [],
            "links": [],
            "vrfs": [],
            "bgp_sessions": [],
            "l3vpn_services": [],
            "devices": []
        }

        # Query all related elements

        # ── Router starting point ──
        if element_type == 'router':
            # 1. Get interfaces on this router
            iface_rows = _query(
                "SELECT id, name, ip_address, status FROM PhysicalInterface WHERE router_id = @rid AND valid_end_ts IS NULL AND name != 'eth0'",
                params={'rid': element_id},
                param_types={'rid': spanner.param_types.STRING}
            )
            for r in iface_rows:
                related["interfaces"].append({"id": r[0], "name": r[1], "ip_address": r[2], "status": r[3]})

            # 2. Get peer routers via links
            peer_query = """
                SELECT DISTINCT r2.id, r2.name, r2.role, r2.status, pl.id AS link_id, pl.name AS link_name
                FROM PhysicalInterface i1
                JOIN Interface_Link il1 ON il1.interface_id = i1.id AND il1.valid_end_ts IS NULL
                JOIN PhysicalLink pl ON pl.id = il1.link_id AND pl.valid_end_ts IS NULL
                JOIN Interface_Link il2 ON il2.link_id = pl.id AND il2.valid_end_ts IS NULL AND il2.interface_id != i1.id
                JOIN PhysicalInterface i2 ON i2.id = il2.interface_id AND i2.valid_end_ts IS NULL
                JOIN PhysicalRouter r2 ON r2.id = i2.router_id AND r2.valid_end_ts IS NULL
                WHERE i1.router_id = @rid AND i1.valid_end_ts IS NULL
            """
            peer_rows = _query(
                peer_query,
                params={'rid': element_id},
                param_types={'rid': spanner.param_types.STRING}
            )
            seen_links = set()
            for r in peer_rows:
                related["routers"].append({"id": r[0], "name": r[1], "role": r[2], "status": r[3]})
                if r[4] not in seen_links:
                    related["links"].append({"id": r[4], "name": r[5]})
                    seen_links.add(r[4])

            # 3. Get VRFs on this router
            vrf_rows = _query(
                "SELECT id, name, rd, vpn_id, status FROM VRF WHERE router_id = @rid AND valid_end_ts IS NULL",
                params={'rid': element_id},
                param_types={'rid': spanner.param_types.STRING}
            )
            vpn_ids = set()
            for r in vrf_rows:
                related["vrfs"].append({"id": r[0], "name": r[1], "rd": r[2], "vpn_id": r[3], "status": r[4]})
                vpn_ids.add(r[3])

            # 4. Get BGP sessions in those VRFs
            for vrf_entry in related["vrfs"]:
                bgp_rows = _query(
                    "SELECT id, peer_ip, local_as, remote_as, status FROM BGPSession WHERE vrf_id = @vid AND valid_end_ts IS NULL",
                    params={'vid': vrf_entry["id"]},
                    param_types={'vid': spanner.param_types.STRING}
                )
                for r in bgp_rows:
                    related["bgp_sessions"].append({
                        "id": r[0], "peer_ip": r[1], "local_as": r[2], "remote_as": r[3],
                        "status": r[4], "vrf_id": vrf_entry["id"]
                    })

            # 5. If depth >= 2, get L3VPN services and other routers in same VPN
            if depth >= 2 and vpn_ids:
                for vpn_id in vpn_ids:
                    # Get VPN service
                    vpn_rows = _query(
                        "SELECT id, name, service_type, topology, status FROM L3VPNService WHERE id = @vid AND valid_end_ts IS NULL",
                        params={'vid': vpn_id},
                        param_types={'vid': spanner.param_types.STRING}
                    )
                    for r in vpn_rows:
                        related["l3vpn_services"].append({"id": r[0], "name": r[1], "service_type": r[2], "topology": r[3], "status": r[4]})

                    # Get other VRFs in same VPN (on different routers)
                    other_vrf_rows = _query(
                        "SELECT v.id, v.name, v.rd, v.router_id, v.status, r.name AS router_name FROM VRF v JOIN PhysicalRouter r ON v.router_id = r.id AND r.valid_end_ts IS NULL WHERE v.vpn_id = @vid AND v.valid_end_ts IS NULL AND v.router_id != @rid",
                        params={'vid': vpn_id, 'rid': element_id},
                        param_types={'vid': spanner.param_types.STRING, 'rid': spanner.param_types.STRING}
                    )
                    for r in other_vrf_rows:
                        related["vrfs"].append({
                            "id": r[0], "name": r[1], "rd": r[2], "router_id": r[3],
                            "status": r[4], "router_name": r[5], "vpn_id": vpn_id
                        })

            # 6. Get compute hosts connected to this router.
            # Was a Device JOIN PhysicalInterface; those tables now live in
            # different databases. hosts_on_router() recovers the relation via
            # LinuxNetwork.connected_routers, entirely inside infra. See
            # utils/hosts.py for why that is equivalent.
            # connected_routers stores bare router names, so strip the 'router:' prefix.
            router_name = element_id.split(':', 1)[1] if element_id.startswith('router:') else element_id
            related["devices"].extend(hosts_on_router(router_name))

        # ── VRF starting point ──
        elif element_type == 'vrf':
            # Get VRF details
            vrf_rows = _query(
                "SELECT id, name, rd, vpn_id, router_id, status FROM VRF WHERE id = @vid AND valid_end_ts IS NULL",
                params={'vid': element_id},
                param_types={'vid': spanner.param_types.STRING}
            )
            vrf_row = vrf_rows[0] if vrf_rows else None
            if vrf_row:
                vpn_id = vrf_row[3]
                router_id = vrf_row[4]

                # Get parent router
                rtr_rows = _query(
                    "SELECT id, name, role, status FROM PhysicalRouter WHERE id = @rid AND valid_end_ts IS NULL",
                    params={'rid': router_id},
                    param_types={'rid': spanner.param_types.STRING}
                )
                for r in rtr_rows:
                    related["routers"].append({"id": r[0], "name": r[1], "role": r[2], "status": r[3]})

                # Get BGP sessions in this VRF
                bgp_rows = _query(
                    "SELECT id, peer_ip, local_as, remote_as, status FROM BGPSession WHERE vrf_id = @vid AND valid_end_ts IS NULL",
                    params={'vid': element_id},
                    param_types={'vid': spanner.param_types.STRING}
                )
                for r in bgp_rows:
                    related["bgp_sessions"].append({"id": r[0], "peer_ip": r[1], "local_as": r[2], "remote_as": r[3], "status": r[4]})

                # Get L3VPN service and other VRFs in same VPN
                if depth >= 2 and vpn_id:
                    vpn_rows = _query(
                        "SELECT id, name, service_type, topology, status FROM L3VPNService WHERE id = @vid AND valid_end_ts IS NULL",
                        params={'vid': vpn_id},
                        param_types={'vid': spanner.param_types.STRING}
                    )
                    for r in vpn_rows:
                        related["l3vpn_services"].append({"id": r[0], "name": r[1], "service_type": r[2], "topology": r[3], "status": r[4]})

                    other_vrf_rows = _query(
                        "SELECT v.id, v.name, v.rd, v.router_id, v.status, r.name AS router_name FROM VRF v JOIN PhysicalRouter r ON v.router_id = r.id AND r.valid_end_ts IS NULL WHERE v.vpn_id = @vid AND v.valid_end_ts IS NULL AND v.id != @self_id",
                        params={'vid': vpn_id, 'self_id': element_id},
                        param_types={'vid': spanner.param_types.STRING, 'self_id': spanner.param_types.STRING}
                    )
                    for r in other_vrf_rows:
                        related["vrfs"].append({"id": r[0], "name": r[1], "rd": r[2], "router_id": r[3], "status": r[4], "router_name": r[5], "vpn_id": vpn_id})

        # ── Interface starting point ──
        elif element_type == 'interface':
            # Parse router_id from interface id (format: router:<name>:interface:<iface>)
            parts = element_id.split(':interface:')
            if len(parts) == 2:
                router_id = parts[0]
                # Get parent router
                rtr_rows = _query(
                    "SELECT id, name, role, status FROM PhysicalRouter WHERE id = @rid AND valid_end_ts IS NULL",
                    params={'rid': router_id},
                    param_types={'rid': spanner.param_types.STRING}
                )
                for r in rtr_rows:
                    related["routers"].append({"id": r[0], "name": r[1], "role": r[2], "status": r[3]})

                # Get peer interface via link
                peer_query = """
                    SELECT i2.id, i2.name, r2.id AS router_id, r2.name AS router_name, pl.id AS link_id
                    FROM Interface_Link il1
                    JOIN PhysicalLink pl ON pl.id = il1.link_id AND pl.valid_end_ts IS NULL
                    JOIN Interface_Link il2 ON il2.link_id = pl.id AND il2.valid_end_ts IS NULL AND il2.interface_id != @iid
                    JOIN PhysicalInterface i2 ON i2.id = il2.interface_id AND i2.valid_end_ts IS NULL
                    JOIN PhysicalRouter r2 ON r2.id = i2.router_id AND r2.valid_end_ts IS NULL
                    WHERE il1.interface_id = @iid AND il1.valid_end_ts IS NULL
                """
                peer_rows = _query(
                    peer_query,
                    params={'iid': element_id},
                    param_types={'iid': spanner.param_types.STRING}
                )
                for r in peer_rows:
                    related["interfaces"].append({"id": r[0], "name": r[1], "router_id": r[2], "router_name": r[3]})
                    related["links"].append({"id": r[4]})
                    related["routers"].append({"id": r[2], "name": r[3]})

        # ── L3VPN Service starting point ──
        elif element_type == 'l3vpn_service':
            vrf_rows = _query(
                "SELECT v.id, v.name, v.rd, v.router_id, v.status, r.name AS router_name FROM VRF v JOIN PhysicalRouter r ON v.router_id = r.id AND r.valid_end_ts IS NULL WHERE v.vpn_id = @vid AND v.valid_end_ts IS NULL",
                params={'vid': element_id},
                param_types={'vid': spanner.param_types.STRING}
            )
            for r in vrf_rows:
                related["vrfs"].append({"id": r[0], "name": r[1], "rd": r[2], "router_id": r[3], "status": r[4], "router_name": r[5]})
                related["routers"].append({"id": r[3], "name": r[5]})

        # Deduplicate
        for key in ["routers", "interfaces", "links", "vrfs", "bgp_sessions", "l3vpn_services", "devices"]:
            seen_ids = set()
            deduped = []
            for item in related[key]:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    deduped.append(item)
            related[key] = deduped

        # Add summary counts
        related["summary"] = {k: len(v) for k, v in related.items() if isinstance(v, list)}
        related["total_related"] = sum(related["summary"].values())

        return json.dumps(related, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error in getRelatedElements: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────────────────────────────────────
# Tool 3: diffConfigs
# ──────────────────────────────────────────────────────────────────────────────

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def diffConfigs(
    old_config_json: Annotated[str, "JSON string of the old/stable configuration"],
    new_config_json: Annotated[str, "JSON string of the new/current configuration"]
) -> str:
    """
    Compare two configuration JSON objects field-by-field and return a structured diff.

    Performs deep recursive comparison including nested objects, arrays (VRF lists,
    BGP neighbor lists, route-target arrays), and scalar values. Useful when you
    already have two config snapshots and want to understand exactly what changed.

    For comparing configs across time, prefer getElementConfig with a stable_timestamp
    which does the fetch and diff in one call. Use this tool when you already have
    two configs from different sources.

    Returns:
        JSON object with 'changes' array (each with path, type, old_value, new_value),
        'has_changes' boolean, and 'change_count' integer.
    """
    logger.info("diffConfigs called")

    try:
        old = json.loads(old_config_json) if isinstance(old_config_json, str) else old_config_json
        new = json.loads(new_config_json) if isinstance(new_config_json, str) else new_config_json

        # Filter out metadata noise
        old = _filter_config_noise(old) if isinstance(old, dict) else old
        new = _filter_config_noise(new) if isinstance(new, dict) else new

        diffs = _deep_diff(old, new)

        return json.dumps({
            "changes": diffs,
            "has_changes": len(diffs) > 0,
            "change_count": len(diffs)
        }, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error in diffConfigs: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────────────────────────────────────
# Tool 4: getLastStableTimestamp
# ──────────────────────────────────────────────────────────────────────────────

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getLastStableTimestamp(
    node_id: Annotated[str, "The node ID to check (e.g. 'router:pe3'). If unknown, pass the router name and the tool will try 'router:<name>' format."],
    threshold: Annotated[float, "Maximum average anomaly score to consider 'stable' (default 0.15). Lower = stricter."] = 0.15,
    lookback_hours: Annotated[int, "How many hours to look back for a stable point (default 24)."] = 24
) -> str:
    """
    Find the most recent timestamp when a node's GNN anomaly score was below
    a given threshold — i.e., when the network was 'stable' for that node.

    This timestamp is used as the 'last known good' reference point for config
    comparison with getElementConfig.

    If the node has never had anomaly scores (GNN not deployed), falls back to
    the earliest SCD version of the element as the stable reference.

    Returns:
        JSON object with 'stable_timestamp' (ISO-8601), 'score_at_stable',
        and 'method' ('gnn_score' or 'earliest_version').
    """
    logger.info(f"getLastStableTimestamp: node_id={node_id}, threshold={threshold}")

    # Normalize node_id format
    if not node_id.startswith(('router:', 'vrf:', 'link:')):
        node_id = f"router:{node_id}"

    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=lookback_hours)

        # Try GNN scores first.
        # NodeEmbedding now exists in five databases, one per domain, but this
        # tool is transport-scoped by construction: node_id is normalised to a
        # router:/vrf:/link: id above and the fallback below reads
        # PhysicalRouter. So the transport copy is the only correct one to read
        # -- fanning out would return embeddings for unrelated domains' nodes.
        query = """
            SELECT timestamp,
                   COALESCE(hetgnn_score,0) AS avg_score
            FROM NodeEmbedding
            WHERE node_id = @nid
              AND timestamp >= @cutoff
              AND COALESCE(hetgnn_score,0) <= @threshold
            ORDER BY timestamp DESC
            LIMIT 1
        """

        rows = _query(
                query,
                params={'nid': node_id, 'cutoff': cutoff, 'threshold': threshold},
                param_types={
            'nid': spanner.param_types.STRING,
            'cutoff': spanner.param_types.TIMESTAMP,
            'threshold': spanner.param_types.FLOAT64
                }
        )
        row = rows[0] if rows else None

        if row:
            return json.dumps({
                "stable_timestamp": row[0].isoformat() if row[0] else None,
                "score_at_stable": round(row[1], 4) if row[1] else None,
                "node_id": node_id,
                "method": "gnn_score",
                "threshold_used": threshold
            }, default=str)

        # Fallback: find the earliest SCD version of the router
        fallback_query = "SELECT MIN(valid_start_ts) FROM PhysicalRouter WHERE id = @rid"
        rows = _query(
                fallback_query,
                params={'rid': node_id},
                param_types={'rid': spanner.param_types.STRING}
        )
        row = rows[0] if rows else None

        if row and row[0]:
            return json.dumps({
                "stable_timestamp": row[0].isoformat(),
                "score_at_stable": None,
                "node_id": node_id,
                "method": "earliest_version",
                "note": "No GNN scores found below threshold; using earliest config version as stable baseline"
            }, default=str)

        return json.dumps({
            "error": f"No stable timestamp found for {node_id}",
            "node_id": node_id,
            "method": "none",
            "note": "No GNN scores and no SCD versions found — element may not exist"
        })

    except Exception as e:
        logger.error(f"Error in getLastStableTimestamp: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────────────────────────────────────
# Tool 5: getConfigHistory
# ──────────────────────────────────────────────────────────────────────────────

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getConfigHistory(
    element_type: Annotated[str, "Type: 'router', 'vrf', 'bgp_session', 'link', 'l3vpn_service'"],
    element_id: Annotated[str, "The element ID in Spanner"],
    start_time: Annotated[str, "ISO-8601 start of time window (or 'all' for full history)"] = "all",
    end_time: Annotated[str, "ISO-8601 end of time window (or 'now' for current time)"] = "now",
    max_versions: Annotated[int, "Maximum number of versions to return (default 10, max 50). Use small values to avoid token overflow."] = 10
) -> str:
    """
    Fetch all SCD Type 2 versions of an element within a time window.

    Shows the complete configuration change history, including when each version
    became active and when it was superseded. Useful for audit trails and
    understanding the sequence of changes that led to the current state.

    Returns:
        JSON array of version records, each with valid_start_ts, valid_end_ts,
        config, and status, ordered chronologically.
    """
    logger.info(f"getConfigHistory: type={element_type}, id={element_id}")

    type_queries = {
        'router': "SELECT config, status, valid_start_ts, valid_end_ts FROM PhysicalRouter WHERE id = @id",
        'vrf': "SELECT config, status, valid_start_ts, valid_end_ts FROM VRF WHERE id = @id",
        'bgp_session': "SELECT config, status, valid_start_ts, valid_end_ts FROM BGPSession WHERE id = @id",
        'link': "SELECT properties, status, valid_start_ts, valid_end_ts FROM PhysicalLink WHERE id = @id",
        'l3vpn_service': "SELECT config, status, valid_start_ts, valid_end_ts FROM L3VPNService WHERE id = @id",
    }

    if element_type not in type_queries:
        return json.dumps({"error": f"Unknown element type '{element_type}'"})

    try:
        base_query = type_queries[element_type]
        params = {'id': element_id}
        param_types = {'id': spanner.param_types.STRING}

        where_clauses = []
        if start_time and start_time.lower() != 'all':
            ts_start = _parse_ts(start_time)
            where_clauses.append("(valid_end_ts > @ts_start OR valid_end_ts IS NULL)")
            params['ts_start'] = ts_start
            param_types['ts_start'] = spanner.param_types.TIMESTAMP
        if end_time and end_time.lower() != 'now':
            ts_end = _parse_ts(end_time)
            where_clauses.append("valid_start_ts <= @ts_end")
            params['ts_end'] = ts_end
            param_types['ts_end'] = spanner.param_types.TIMESTAMP

        query = base_query
        if where_clauses:
            query += " AND " + " AND ".join(where_clauses)
        # Clamp max_versions
        max_versions = min(max(max_versions, 1), 50)
        query += f" ORDER BY valid_start_ts DESC LIMIT {max_versions}"

        versions = []
        rows = _query(query, params=params, param_types=param_types)
        for row in rows:
            config = row[0]
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except json.JSONDecodeError:
                    pass
            config = _filter_config_noise(config) if isinstance(config, dict) else config

            versions.append({
                "config": config,
                "status": row[1],
                "valid_start_ts": row[2].isoformat() if row[2] else None,
                "valid_end_ts": row[3].isoformat() if row[3] else None,
                "is_current": row[3] is None
            })

        # Add diff between consecutive versions
        for i in range(1, len(versions)):
            prev_config = versions[i - 1]["config"]
            curr_config = versions[i]["config"]
            if isinstance(prev_config, dict) and isinstance(curr_config, dict):
                versions[i]["diff_from_previous"] = _deep_diff(prev_config, curr_config)

        # Reverse to chronological order (we queried DESC LIMIT for latest N)
        versions.reverse()

        # Recompute diffs after reversing
        for i in range(1, len(versions)):
            prev_config = versions[i - 1]["config"]
            curr_config = versions[i]["config"]
            if isinstance(prev_config, dict) and isinstance(curr_config, dict):
                versions[i]["diff_from_previous"] = _deep_diff(prev_config, curr_config)

        return json.dumps({
            "element_type": element_type,
            "element_id": element_id,
            "versions": versions,
            "version_count": len(versions),
            "limited_to": max_versions,
            "note": f"Showing latest {len(versions)} versions (limit {max_versions})"
        }, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error in getConfigHistory: {e}", exc_info=True)
        return json.dumps({"error": str(e)})
