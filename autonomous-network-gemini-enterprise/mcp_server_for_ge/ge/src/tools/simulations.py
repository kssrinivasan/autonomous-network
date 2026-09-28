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
from utils.hosts import routers_for_hosts

# Import topology helpers
from tools.topology import (
    CAPACITY_TIERS,
    GRAPH_NAME,
    query_ospf_paths,
    query_link_utilization,
    calculate_bandwidth_bps,
)

logger = logging.getLogger(__name__)

# Action vocabulary. The action argument is free text -- the agents and the
# scenario docs use everything from 'UPGRADE_LINK' to 'restore_ospf_cost' -- so
# it is classified by keyword into the three things this tool can actually
# compute from live data. Anything unrecognised gets the observed state and an
# explicit refusal to forecast, rather than a number.
_ACTION_CLASSES = (
    ('CAPACITY_UPGRADE', ('upgrade', 'capacity', 'bandwidth', 'augment')),
    ('TRAFFIC_REROUTE', ('reroute', 'divert', 'shift', 'failover', 'drain',
                         'cost', 'metric', 'bypass')),
    ('RESTORE', ('restore', 'bring_up', 'bring-up', 'bringup', 'repair',
                 'recover', 'enable', 'reset', 'undo')),
)


def _classify(action):
    lowered = (action or "").lower()
    for name, keywords in _ACTION_CLASSES:
        if any(kw in lowered for kw in keywords):
            return name
    return 'UNCLASSIFIED'


def _links_on_router(router_name):
    """Return the names of the current physical links attached to a router."""
    sql = f"""
        GRAPH {GRAPH_NAME}
        MATCH (l:PhysicalLink)-[:LinkedTo]->(i:PhysicalInterface)<-[:HasInterface]-(r:PhysicalRouter)
        WHERE r.name = @router
          AND l.valid_end_ts IS NULL
          AND i.valid_end_ts IS NULL
          AND r.valid_end_ts IS NULL
        RETURN DISTINCT l.name AS link_name
    """
    with get_database().snapshot(multi_use=True) as snapshot:
        rows = snapshot.execute_sql(
            sql,
            params={"router": router_name},
            param_types={"router": spanner.param_types.STRING},
        )
        return sorted({row[0] for row in rows if row[0]})


def _resolve_target(target_element):
    """Work out whether the target names a link or a router.

    Returns ``(kind, affected_link_names)``; kind is 'link', 'router' or
    'unknown'.
    """
    with get_database().snapshot(multi_use=True) as snapshot:
        link_rows = list(snapshot.execute_sql(
            "SELECT name FROM PhysicalLink WHERE name = @n AND valid_end_ts IS NULL",
            params={"n": target_element},
            param_types={"n": spanner.param_types.STRING},
        ))
        if link_rows:
            return 'link', [target_element]

        router_rows = list(snapshot.execute_sql(
            "SELECT name FROM PhysicalRouter WHERE name = @n AND valid_end_ts IS NULL",
            params={"n": target_element},
            param_types={"n": spanner.param_types.STRING},
        ))
    if router_rows:
        return 'router', _links_on_router(target_element)
    return 'unknown', []


def _next_tier(capacity_bps):
    return next(((bps, label) for bps, label in CAPACITY_TIERS if bps > capacity_bps), None)


@networkagent_mcp.tool()
def simulate_remediation_impact(
    remediation_action: Annotated[str, "The specific remediation action (e.g. UPGRADE_LINK, REROUTE_TRAFFIC, restore_ospf_cost)"],
    target_element: Annotated[str, "The target link or router host name"]
) -> str:
    """Forecast the utilization and path effects of a remediation action, computed from live link capacity and measured interface throughput."""
    logger.info(f"simulate_remediation_impact: {remediation_action} on {target_element}")

    # This tool previously returned three constants (38.4%, 4.2ms, 0.94
    # confidence) behind a docstring claiming a predictive GNN. There is no GNN
    # here. Every number below comes from PhysicalLink.bandwidth and the
    # RouterSystemMetrics byte counters, via query_link_utilization.
    try:
        kind, link_names = _resolve_target(target_element)
        if kind == 'unknown':
            return json.dumps({
                "error": (
                    f"'{target_element}' is not a current PhysicalLink or "
                    "PhysicalRouter in the transport topology. Pass a link name "
                    "such as 'p1-p2' or a router host name such as 'pe1'."
                )
            }, indent=2)
        if not link_names:
            return json.dumps({
                "error": (
                    f"Router '{target_element}' has no physical links recorded, so "
                    "there is nothing to compute an impact against."
                )
            }, indent=2)

        util = json.loads(query_link_utilization(",".join(link_names)))
        if util.get("error"):
            return json.dumps({"error": util["error"]}, indent=2)
        links = {l["name"]: l for l in util.get("links", [])}

        action_class = _classify(remediation_action)
        observed = [
            {
                "link": name,
                "capacity_bps": links[name].get("capacity_bps"),
                "measured_traffic_bps": links[name].get("current_utilization_bps"),
                "utilization_pct": links[name].get("utilization_pct"),
            }
            for name in link_names if name in links
        ]

        result = {
            "action": remediation_action,
            "action_class": action_class,
            "target": {"name": target_element, "kind": kind, "links": link_names},
            "observed": observed,
            "basis": (
                "Link capacity from PhysicalLink.bandwidth; traffic from the peak "
                "node_network_{transmit,receive}_bytes_total counters on both "
                "endpoints over the last 6 minutes. No predictive model is involved."
            ),
        }

        if action_class == 'CAPACITY_UPGRADE':
            projected = []
            for entry in observed:
                capacity = entry["capacity_bps"] or 0
                tier = _next_tier(capacity)
                if tier is None:
                    projected.append({
                        "link": entry["link"],
                        "note": "already at the largest standard link speed",
                    })
                    continue
                tier_bps, tier_label = tier
                traffic = entry["measured_traffic_bps"] or 0
                projected.append({
                    "link": entry["link"],
                    "upgraded_to": tier_label,
                    "upgraded_capacity_bps": tier_bps,
                    "predicted_utilization_pct": round(traffic / tier_bps * 100, 2),
                })
            result["predicted"] = projected
            result["caveats"] = [
                "Assumes the measured traffic is unchanged by the upgrade; a link "
                "that is currently saturated may be shaping traffic that would grow "
                "once the ceiling is raised."
            ]

        elif action_class in ('TRAFFIC_REROUTE', 'RESTORE'):
            # Both need the same computation: what the path looks like with the
            # target link taken out, and what the traffic currently on it would
            # do to the links that would carry it.
            reroutes = []
            for name in link_names:
                link = links.get(name)
                if not link or not link.get("router_a") or not link.get("router_b"):
                    continue
                path_json = json.loads(
                    query_ospf_paths(link["router_a"], link["router_b"], name)
                )
                if not path_json.get("path_available"):
                    reroutes.append({
                        "link": name,
                        "alternate_path": None,
                        "note": (
                            f"No alternate path exists between {link['router_a']} and "
                            f"{link['router_b']} without this link; it is a single "
                            "point of failure for that pair."
                        ),
                    })
                    continue

                displaced_bps = link.get("current_utilization_bps") or 0
                alt_links = path_json.get("links", [])
                alt_util = json.loads(query_link_utilization(",".join(alt_links))) if alt_links else {"links": []}
                per_link = []
                for alt in alt_util.get("links", []):
                    capacity = alt.get("capacity_bps") or 0
                    combined = (alt.get("current_utilization_bps") or 0) + displaced_bps
                    per_link.append({
                        "link": alt.get("name"),
                        "current_utilization_pct": alt.get("utilization_pct"),
                        "predicted_utilization_pct": round(combined / capacity * 100, 2) if capacity else None,
                        "would_be_congested": bool(capacity) and combined / capacity > 0.8,
                    })
                reroutes.append({
                    "link": name,
                    "displaced_traffic_bps": displaced_bps,
                    "alternate_path": path_json.get("path"),
                    "alternate_hop_count": len(path_json.get("path", [])) - 1,
                    "alternate_links": per_link,
                })
            result["predicted"] = reroutes
            result["caveats"] = [
                "Path selection is BFS on hop count, not the OSPF cost the routers "
                "actually use, so the alternate path is the shortest topological one.",
                "Assumes all traffic on the target link moves wholesale onto the "
                "single alternate path; real ECMP would spread it.",
            ]

        else:
            result["predicted"] = None
            result["caveats"] = [
                f"'{remediation_action}' does not match any action this tool can "
                "compute. The observed state above is real; no forecast was produced. "
                "Supported action classes are capacity upgrades, traffic reroutes and "
                "link/router restoration.",
            ]

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"Error in simulate_remediation_impact: {e}")
        return json.dumps({"error": str(e)}, indent=2)

@networkagent_mcp.tool()
def simulate_traffic_addition(
    source_device: Annotated[str, "Source device name (e.g., 'ce1-spoke')"],
    destination_device: Annotated[str, "Destination device name (e.g., 'ce1-hub')"],
    bandwidth: Annotated[str, "Bandwidth with unit (e.g., '100Mbps', '1Gbps', '500Mbps'). Accepts Kbps, Mbps, or Gbps."],
    protocol: Annotated[str, "Protocol (TCP or UDP)"] = "TCP"
) -> str:
    """
    Simulate the impact of adding new traffic between two devices.
    The tool automatically converts bandwidth strings to bits per second.
    Returns path, current link utilization, predicted utilization after traffic addition, and risk assessment.
    """
    bandwidth_bps = calculate_bandwidth_bps(bandwidth)
    if bandwidth_bps == 0:
        return json.dumps({"error": f"Invalid bandwidth format: {bandwidth}. Use format like '100Mbps', '1Gbps', '500Mbps'"})

    logger.info(f"simulate_traffic_addition: {source_device} -> {destination_device}, {bandwidth} ({bandwidth_bps} bps)")
    try:
        # Step 1: Resolve devices to their PE routers.
        # This was a Device -> PhysicalInterface -> PhysicalRouter join.
        # Device is now ComputeHost in infratopology-db while the other two
        # stayed in transporttopology-db, so the join is resolved in infra
        # via LinuxNetwork.connected_routers instead. See utils/hosts.py.
        host_routers = routers_for_hosts([source_device, destination_device])

        # A host on a bridge with several routers attaches to all of them.
        # The old join returned one row per interface and callers took the
        # first; keep that behaviour and log when the choice is ambiguous.
        device_map = {}
        for host, routers in host_routers.items():
            if len(routers) > 1:
                logger.info("%s attaches to %s; using %s", host, routers, routers[0])
            device_map[host] = routers[0]

        if source_device not in device_map:
            return json.dumps({"error": f"Source device {source_device} not found"})
        if destination_device not in device_map:
            return json.dumps({"error": f"Destination device {destination_device} not found"})

        source_pe = device_map[source_device]
        dest_pe = device_map[destination_device]

        logger.info(f"Resolved: {source_device} -> {source_pe}, {destination_device} -> {dest_pe}")

        # Step 2: Find path between PEs
        path_json = query_ospf_paths(source_pe, dest_pe, "")
        path_data = json.loads(path_json)

        if 'error' in path_data or not path_data.get('path_available'):
            return json.dumps({"error": "No path found between devices"})

        path_routers = path_data.get('path', [])
        path_links = path_data.get('links', [])

        # Step 3: Query current utilization on all links in the path (batch for performance)
        link_impacts = []
        max_risk = "low"

        if path_links:
            all_links = ','.join(path_links)
            link_util_json = query_link_utilization(all_links)
            link_util_data = json.loads(link_util_json)

            link_map = {}
            if link_util_data.get('links'):
                for link in link_util_data['links']:
                    link_map[link['name']] = link

            for link_name in path_links:
                if link_name in link_map:
                    link = link_map[link_name]
                    capacity_bps = link.get('capacity_bps', 1_000_000_000)
                    current_util_bps = link.get('current_utilization_bps', 0)
                    current_util_pct = link.get('utilization_pct', 0)

                    predicted_util_bps = current_util_bps + bandwidth_bps
                    predicted_util_pct = (predicted_util_bps / capacity_bps * 100) if capacity_bps > 0 else 0
                    headroom_pct = 100 - predicted_util_pct

                    if predicted_util_pct < 50:
                        risk = "low"
                    elif predicted_util_pct < 70:
                        risk = "medium"
                    else:
                        risk = "high"

                    if risk == "high":
                        max_risk = "high"
                    elif risk == "medium" and max_risk == "low":
                        max_risk = "medium"

                    impact_data = {
                        "link": link_name,
                        "current_utilization_pct": round(current_util_pct, 2),
                        "current_utilization_bps": current_util_bps,
                        "predicted_utilization_pct": round(predicted_util_pct, 2),
                        "predicted_utilization_bps": predicted_util_bps,
                        "capacity_bps": capacity_bps,
                        "capacity_mbps": round(capacity_bps / 1_000_000, 2),
                        "headroom_pct": round(headroom_pct, 2),
                        "risk_level": risk
                    }
                    link_impacts.append(impact_data)

        if max_risk == "high":
            recommendation = "CAPACITY_UPGRADE_NEEDED"
            message = "One or more links will exceed 80% utilization. Capacity upgrade recommended before adding traffic."
        elif max_risk == "medium":
            recommendation = "PROCEED_WITH_CAUTION"
            message = "Links will reach 60-80% utilization. Monitor closely after adding traffic."
        else:
            recommendation = "SAFE_TO_ADD"
            message = "All links have sufficient capacity (<60% utilization)."

        result = {
            "traffic_addition": {
                "source_device": source_device,
                "destination_device": destination_device,
                "bandwidth_bps": bandwidth_bps,
                "bandwidth_mbps": round(bandwidth_bps / 1_000_000, 2),
                "protocol": protocol
            },
            "routing": {
                "source_pe": source_pe,
                "destination_pe": dest_pe,
                "path": path_routers,
                "links": path_links,
                "hop_count": len(path_routers) - 1 if path_routers else 0
            },
            "link_impacts": link_impacts,
            "overall_risk": max_risk,
            "recommendation": recommendation,
            "message": message
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"Error in simulate_traffic_addition: {e}")
        return json.dumps({"error": str(e)})
