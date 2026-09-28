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
from mcp.types import ToolAnnotations
from utils.globals import callable_tool, networkagent_mcp
from google.cloud import spanner
from utils.database import get_database, graph_name
from utils.flows import (
    fetch_flow_measurements,
    fetch_traffic_flows,
    metric_flow_id,
    summarize_measurement,
)

logger = logging.getLogger(__name__)

@networkagent_mcp.tool()
def query_router_metadata(
    node_name: Annotated[str, "The host name of the transport router (e.g. p1, pe1)"]
) -> str:
    """Fetch real-time VyOS transport router system metadata, vendor, role, location, and active interface IPs."""
    logger.info(f"query_router_metadata: {node_name}")
    database = get_database()
    with database.snapshot(multi_use=True) as snapshot:
        try:
            # Dynamically discover exact column names to guarantee 100% schema immunity
            sql_cols = "SELECT column_name FROM information_schema.columns WHERE table_name = 'PhysicalRouter' ORDER BY ordinal_position"
            cols = [r[0] for r in snapshot.execute_sql(sql_cols)]
            
            sql = """
                SELECT *
                FROM PhysicalRouter
                WHERE LOWER(name) = LOWER(@name)
                ORDER BY valid_start_ts DESC LIMIT 1
            """
            results = list(snapshot.execute_sql(sql, params={"name": node_name.strip()}, param_types={"name": spanner.param_types.STRING}))
            if not results:
                return f"Router '{node_name}' not found in Spanner PhysicalRouter table."
            
            row = results[0]
            router_dict = dict(zip(cols, row))
            router_id = router_dict.get("id") or row[0]
            
            # Fetch interfaces dynamically
            sql_iface_cols = "SELECT column_name FROM information_schema.columns WHERE table_name = 'PhysicalInterface' ORDER BY ordinal_position"
            iface_cols = [r[0] for r in snapshot.execute_sql(sql_iface_cols)]
            
            sql_ifaces = """
                SELECT *
                FROM PhysicalInterface
                WHERE router_id = @router_id
            """
            ifaces = []
            for ir in snapshot.execute_sql(sql_ifaces, params={"router_id": router_id}, param_types={"router_id": spanner.param_types.STRING}):
                ifaces.append(dict(zip(iface_cols, ir)))
                
            metadata = {
                "router_details": router_dict,
                "interfaces": ifaces
            }
            # Serialize cleanly with timestamp handling
            return json.dumps(metadata, indent=2, default=str)
        except Exception as e:
            logger.error(f"SQL error in query_router_metadata: {e}")
            return f"Error querying Spanner PhysicalRouter: {e}"

# The single 'networkGraph' no longer exists: the six-database schema defines
# one property graph per domain. Both graph queries below are transport-only.
GRAPH_NAME = graph_name('transport')

# Standard transport link speeds, ascending, spelled the way PhysicalLink.bandwidth
# spells them. Lives here rather than in tools/capacity.py because both that
# module and tools/simulations.py size upgrades against it, and neither should
# import the other.
CAPACITY_TIERS = (
    (100_000_000, "100mbit"),
    (1_000_000_000, "1gbit"),
    (10_000_000_000, "10gbit"),
    (40_000_000_000, "40gbit"),
    (100_000_000_000, "100gbit"),
)

def calculate_bandwidth_bps(bw_str: str) -> int:
    """Convert bandwidth string to bps (e.g., '100Mbps' -> 100000000)"""
    if not bw_str:
        return 0
    bw_str = bw_str.lower().strip()
    try:
        if 'gbps' in bw_str:
            return int(float(bw_str.replace('gbps', '')) * 1_000_000_000)
        elif 'mbps' in bw_str:
            return int(float(bw_str.replace('mbps', '')) * 1_000_000)
        elif 'kbps' in bw_str:
            return int(float(bw_str.replace('kbps', '')) * 1_000)
    except:
        pass
    return 0

# callable_tool, not networkagent_mcp.tool: tools/simulations.py calls this
# in-process to find the path between two PEs.
@callable_tool()
def query_ospf_paths(
    source_router: Annotated[str, "Source router name (e.g., 'pe1')"],
    destination_router: Annotated[str, "Destination router name (e.g., 'pe2')"],
    exclude_link: Annotated[str, "Optional link to exclude for simulation (e.g., 'p1-p2'). Set to empty string or None to include all links."] = ""
) -> str:
    """
    Query OSPF routing paths between two routers using BFS (shortest hop count).
    Can exclude a specific link to simulate link failure.
    """
    logger.info(f"query_ospf_paths: {source_router} -> {destination_router}, exclude={exclude_link}")
    database = get_database()
    with database.snapshot() as snapshot:
        try:
            gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH (r1:PhysicalRouter)-[:HasInterface]->(i1:PhysicalInterface)
                      -[:ConnectsTo]->(link:PhysicalLink)
                      -[:LinkedTo]->(i2:PhysicalInterface)
                      <-[:HasInterface]-(r2:PhysicalRouter)
                WHERE r1.valid_end_ts IS NULL
                  AND r2.valid_end_ts IS NULL
                  AND i1.valid_end_ts IS NULL
                  AND i2.valid_end_ts IS NULL
                  AND link.valid_end_ts IS NULL
                  AND i1.name != 'eth0'
                  AND i2.name != 'eth0'
                RETURN r1.name AS router_a,
                       r2.name AS router_b,
                       link.name AS link_name,
                       link.id AS link_id,
                       link.bandwidth AS bandwidth
            """
            results = snapshot.execute_sql(gql_query)

            graph = {}
            link_bandwidths = {}

            for row in results:
                router_a, router_b, link_name, link_id, bandwidth = row

                # Skip excluded link if specified
                if exclude_link and (link_name == exclude_link or
                                      link_id == f"link:{exclude_link}" or
                                      exclude_link in link_name):
                    logger.info(f"Excluding link: {link_name}")
                    continue

                link_bandwidths[link_name] = bandwidth if bandwidth else "1gbit"

                if router_a not in graph:
                    graph[router_a] = []
                if router_b not in graph:
                    graph[router_b] = []

                # Bidirectional link
                graph[router_a].append((router_b, link_name))
                graph[router_b].append((router_a, link_name))

            # BFS to find shortest path (by hop count)
            from collections import deque

            def bfs_shortest_path(graph, start, end):
                if start not in graph or end not in graph:
                    return None, []

                queue = deque([(start, [start], [])])
                visited = {start}

                while queue:
                    current, path, links = queue.popleft()

                    if current == end:
                        return path, links

                    for neighbor, link_name in graph.get(current, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append((neighbor, path + [neighbor], links + [link_name]))

                return None, []

            # Find shortest path
            path, links = bfs_shortest_path(graph, source_router, destination_router)

            # Calculate minimum bandwidth on path
            min_bandwidth_bps = float('inf')
            if links:
                for link in links:
                    bw_str = link_bandwidths.get(link, "1gbit")
                    bw_bps = calculate_bandwidth_bps(bw_str)
                    if bw_bps > 0 and bw_bps < min_bandwidth_bps:
                        min_bandwidth_bps = bw_bps

            result = {
                "source": source_router,
                "destination": destination_router,
                "excluded_link": exclude_link if exclude_link else None,
                "path": path if path else [],
                "links": links,
                "hop_count": len(path) - 1 if path else 0,
                "path_available": path is not None,
                "bottleneck_bandwidth_bps": int(min_bandwidth_bps) if min_bandwidth_bps != float('inf') else None
            }
            return json.dumps(result, indent=2)

        except Exception as e:
            logger.error(f"Error in query_ospf_paths: {e}")
            return json.dumps({"error": str(e)})

# Also called in-process by tools/simulations.py -- see query_ospf_paths above.
@callable_tool()
def query_link_utilization(
    link_names: Annotated[str, "Comma-separated list of link names to query (e.g., 'p1-p2,p1-p3')"],
    time_window_minutes: Annotated[int, "Time window in minutes for metrics (default: 6)"] = 6
) -> str:
    """
    Query peak link utilization and capacity for specified links over a time window.
    Returns the maximum observed utilization, critical for capacity planning to reveal true load peaks.
    """
    logger.info(f"query_link_utilization: links={link_names}, window={time_window_minutes}min")
    link_list = [l.strip() for l in link_names.split(',') if l.strip()]

    import time as time_mod
    database = get_database()
    with database.snapshot(multi_use=True) as snapshot:
        try:
            # Step 1: Query all links' metadata in one batch
            links_query = """
                SELECT
                    l.name AS link_name,
                    l.bandwidth,
                    l.id AS link_id
                FROM PhysicalLink l
                WHERE l.valid_end_ts IS NULL
                  AND l.name IN UNNEST(@link_names)
            """
            links_results = snapshot.execute_sql(
                links_query,
                params={"link_names": link_list},
                param_types={"link_names": spanner.param_types.Array(spanner.param_types.STRING)}
            )

            link_metadata = {}
            for row in links_results:
                link_name, bandwidth_str, link_id = row
                capacity_bps = 1_000_000_000
                if bandwidth_str:
                    bw_lower = bandwidth_str.lower()
                    try:
                        if 'gbit' in bw_lower:
                            capacity_bps = int(bw_lower.replace('gbit', '')) * 1_000_000_000
                        elif 'mbit' in bw_lower:
                            capacity_bps = int(bw_lower.replace('mbit', '')) * 1_000_000
                    except:
                        pass
                link_metadata[link_name] = {"id": link_id, "capacity_bps": capacity_bps, "bandwidth_str": bandwidth_str}

            # Step 2: Query all links' endpoints in one batch
            link_ids = [meta["id"] for meta in link_metadata.values()]
            if not link_ids:
                return json.dumps({"links": []})

            endpoints_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH (link:PhysicalLink)-[:LinkedTo]->(i:PhysicalInterface)<-[:HasInterface]-(r:PhysicalRouter)
                WHERE link.id IN UNNEST(@link_ids)
                  AND link.valid_end_ts IS NULL
                  AND i.valid_end_ts IS NULL
                  AND r.valid_end_ts IS NULL
                RETURN link.name AS link_name, r.name AS router_name, i.name AS interface_name
            """
            endpoints_results = snapshot.execute_sql(
                endpoints_query,
                params={"link_ids": link_ids},
                param_types={"link_ids": spanner.param_types.Array(spanner.param_types.STRING)}
            )

            link_endpoints = {}
            for row in endpoints_results:
                link_name, router_name, interface_name = row
                if link_name not in link_endpoints:
                    link_endpoints[link_name] = []
                link_endpoints[link_name].append((router_name, interface_name))

            # Step 3: Collect all routers and interfaces for ONE metrics query
            all_routers = set()
            all_interfaces = set()
            for link_name, endpoint_pairs in link_endpoints.items():
                for router, interface in endpoint_pairs:
                    all_routers.add(router)
                    all_interfaces.add(interface)

            if not all_routers or not all_interfaces:
                return json.dumps({"links": []})

            # Step 4: Query ALL metrics in one shot
            # NetworkMetrics was split into RouterSystemMetrics (per interface)
            # and RouterRoutingSeries (per vrf/afi/safi/peer/iface). Interface byte
            # counters are system metrics, so only the system half is needed
            # here; the routing half has no interface column at all.
            # 'node_name' was also renamed to 'router_name'.
            metrics_query = """
                SELECT
                    router_name,
                    interface,
                    metric_name,
                    timestamp,
                    value
                  FROM RouterSystemMetrics
                  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @minutes MINUTE)
                    AND router_name IN UNNEST(@routers)
                    AND interface IN UNNEST(@interfaces)
                    AND metric_name IN ('node_network_transmit_bytes_total', 'node_network_receive_bytes_total')
                  ORDER BY router_name, interface, metric_name, timestamp
            """
            metrics_results = snapshot.execute_sql(
                metrics_query,
                params={
                    "minutes": time_window_minutes,
                    "routers": list(all_routers),
                    "interfaces": list(all_interfaces)
                },
                param_types={
                    "minutes": spanner.param_types.INT64,
                    "routers": spanner.param_types.Array(spanner.param_types.STRING),
                    "interfaces": spanner.param_types.Array(spanner.param_types.STRING)
                }
            )

            # Step 5: Process ALL metrics into series
            from collections import defaultdict
            series_data = defaultdict(list)
            for row in metrics_results:
                node, iface, metric, ts, value = row
                key = (node, iface, metric)
                series_data[key].append((ts, value))

            # Step 6: Find peak rate for each series
            series_peak_rates = {}
            for key, points in series_data.items():
                if not points:
                    continue
                max_rate = max(value for ts, value in points)
                series_peak_rates[key] = max_rate

            # Step 7: Aggregate metrics by link
            result_links = []
            for link_name in link_list:
                if link_name not in link_metadata:
                    continue

                capacity_bps = link_metadata[link_name]["capacity_bps"]
                endpoint_pairs = link_endpoints.get(link_name, [])

                routers = [r for r, i in endpoint_pairs]
                interfaces = [i for r, i in endpoint_pairs]

                peak_bytes_per_sec = 0
                if len(endpoint_pairs) >= 2:
                    router_a, iface_a = endpoint_pairs[0]
                    router_b, iface_b = endpoint_pairs[1]

                    tx_a = series_peak_rates.get((router_a, iface_a, 'node_network_transmit_bytes_total'), 0)
                    rx_b = series_peak_rates.get((router_b, iface_b, 'node_network_receive_bytes_total'), 0)
                    direction_a_to_b = max(tx_a, rx_b)

                    tx_b = series_peak_rates.get((router_b, iface_b, 'node_network_transmit_bytes_total'), 0)
                    rx_a = series_peak_rates.get((router_a, iface_a, 'node_network_receive_bytes_total'), 0)
                    direction_b_to_a = max(tx_b, rx_a)

                    peak_bytes_per_sec = max(direction_a_to_b, direction_b_to_a)

                current_throughput = int(peak_bytes_per_sec * 8)
                utilization_pct = (current_throughput / capacity_bps * 100) if capacity_bps > 0 else 0
                available_bps = max(0, capacity_bps - current_throughput)

                result_links.append({
                    "name": link_name,
                    "router_a": routers[0] if len(routers) > 0 else None,
                    "router_b": routers[1] if len(routers) > 1 else None,
                    "interface_a": interfaces[0] if len(interfaces) > 0 else None,
                    "interface_b": interfaces[1] if len(interfaces) > 1 else None,
                    "capacity_bps": capacity_bps,
                    "current_utilization_bps": current_throughput,
                    "utilization_pct": round(utilization_pct, 2),
                    "available_bps": available_bps,
                    "time_window_minutes": time_window_minutes
                })
            return json.dumps({"links": result_links}, indent=2)

        except Exception as e:
            logger.error(f"Error in query_link_utilization: {e}")
            return json.dumps({"error": str(e)})


def _device_attachment_map() -> dict:
    """Map each Device to the router that terminates its LAN.

    This is a single read of ``ComputeHost`` in ``infratopology-db``. The
    controller denormalizes the attribution onto the row as it reconciles
    (migration plan 7.1): a Device declares the LinuxNetwork it sits on, and the
    CE router serving that LAN has an interface on the same bridge, so the join
    is exact -- no address arithmetic and no guessing.

    It used to rebuild that join here in Python from the VyOSRouter and Device
    custom resources, because Spanner was the only place the relationship did
    *not* exist in one piece. It does now.
    """
    sql = (
        "SELECT name, network_name, ip_address, attached_router "
        "FROM ComputeHost "
        "WHERE valid_end_ts IS NULL AND attached_router IS NOT NULL "
        "AND attached_router != ''"
    )
    with get_database('infra').snapshot() as snap:
        rows = list(snap.execute_sql(sql))

    return {
        name: {
            "router": router,
            "network": network_name,
            "ip_address": ip_address,
        }
        for name, network_name, ip_address, router in rows
    }


@networkagent_mcp.tool()
def query_affected_flows(
    router_names: Annotated[str, "Comma-separated list of router host names"] = None
) -> str:
    """Identify all active user traffic flows traversing specific transport routers, with what each one is actually measuring.

    Flows come from the TrafficFlow table in the service topology database, and
    each is resolved end to end: source and destination devices to the CE
    routers terminating their LANs, then CE to CE through the live topology
    graph, using the same shortest-path walk as query_ospf_paths. A flow is
    reported as affected when one of the named routers sits on that path --
    endpoint or transit.

    Every flow carries both its requested bandwidth and its measured
    throughput, loss, latency and jitter, so a degraded flow is visible here
    rather than only in the intent it was created from.
    """
    logger.info(f"query_affected_flows: {router_names}")
    targets = [r.strip() for r in (router_names or "").split(',') if r.strip()]

    # Flows are read from Spanner, not from the TrafficTest custom resources.
    # The controller mirrors the whole CR into TrafficFlow.config as it
    # reconciles, so nothing is lost by not going to Kubernetes -- and it puts
    # the flows in the same database as their measurements, which is what lets
    # the two be reported together.
    try:
        flows = fetch_traffic_flows()
    except Exception as e:
        logger.error(f"Error reading TrafficFlow in query_affected_flows: {e}")
        return json.dumps({"error": str(e)})

    if not flows:
        return json.dumps({
            "routers_queried": targets,
            "affected_flows": [],
            "message": "No traffic flow is recorded in the service topology "
                       "database, so no user traffic is being generated. This is "
                       "an empty network, not an unaffected one."
        }, indent=2)

    active = [f for f in flows if f["active"]]
    inactive = len(flows) - len(active)

    try:
        measurements = fetch_flow_measurements([f["name"] for f in active]) if active else {}
    except Exception as e:
        # A flow with no reading is still a flow worth reporting, so a failed
        # metrics read degrades the answer rather than replacing it.
        logger.error(f"Error reading TrafficFlowSeries in query_affected_flows: {e}")
        measurements = {}

    attachment = _device_attachment_map()
    # One BFS per CE pair, not per flow -- the mesh tests reuse the same pairs.
    path_cache = {}

    def path_between(src_router, dst_router):
        key = (src_router, dst_router)
        if key not in path_cache:
            try:
                path_cache[key] = json.loads(
                    query_ospf_paths(src_router, dst_router)
                ).get("path", [])
            except Exception as e:
                logger.error(f"Path lookup {src_router}->{dst_router} failed: {e}")
                path_cache[key] = []
        return path_cache[key]

    affected = []
    unresolved = []

    for flow in active:
        spec = flow["spec"]
        name = flow["name"]

        # The metric series are per source device and per direction; index them
        # so each source picks up its own forward and reverse readings.
        series_by_source = {}
        for series in measurements.get(name, []):
            series_by_source.setdefault(series["source_device"], []).append(series)

        destination = spec.get("destination_device")
        dst = attachment.get(destination)
        if not dst:
            unresolved.append(f"{name}: destination device '{destination}' has no known router attachment")
            continue

        for source in spec.get("source_devices", []) or []:
            src = attachment.get(source)
            if not src:
                unresolved.append(f"{name}: source device '{source}' has no known router attachment")
                continue

            path = path_between(src["router"], dst["router"])
            if targets and not any(t in path for t in targets):
                continue

            # Each source device gets its own metric series, so the flow id is
            # qualified the same way -- this is the exact TrafficFlowSeries
            # flow_id, which makes the reading here traceable to its rows.
            series = series_by_source.get(source, [])
            affected.append({
                "flow_id": metric_flow_id(name, source),
                "test_name": name,
                "source_device": source,
                "destination_device": destination,
                "source_router": src["router"],
                "destination_router": dst["router"],
                "path": path,
                "path_available": bool(path),
                "matched_routers": [t for t in targets if t in path],
                "protocol": spec.get("protocol"),
                "bandwidth": spec.get("bandwidth"),
                "reverse_bandwidth": spec.get("reverse_bandwidth") if spec.get("bidirectional") else None,
                "bidirectional": bool(spec.get("bidirectional")),
                "vpn": spec.get("vpnRef"),
                "status": "ACTIVE",
                # None when nothing reported recently, which is itself a
                # finding: a running flow whose agents have gone quiet.
                "measured": summarize_measurement(series),
            })

    silent = [f["flow_id"] for f in affected if not f["measured"]]

    result = {
        "routers_queried": targets,
        "affected_flow_count": len(affected),
        "affected_flows": affected,
    }
    if inactive:
        result["inactive_flow_count"] = inactive
    if unresolved:
        result["unresolved"] = unresolved
    if silent:
        result["not_reporting"] = silent
        result["not_reporting_note"] = (
            "These flows are in a running phase but no traffic-agent metric has "
            "arrived in the last 10 minutes. Their agents are down, or the flow "
            "is running but carrying nothing."
        )
    if not affected:
        if targets:
            result["message"] = (
                f"No active traffic flow traverses {', '.join(targets)}. "
                f"{inactive} traffic flow(s) exist but are not in a running phase."
                if inactive else
                f"No active traffic flow traverses {', '.join(targets)}."
            )
        else:
            result["message"] = "No traffic flow is currently in a running phase."
    return json.dumps(result, indent=2)


@networkagent_mcp.tool()
def query_flow_metrics(
    flow_names: Annotated[str, "Comma-separated TrafficTest names, e.g. 'd1-red-d2-red-bidir'. Omit for every flow."] = None,
    window_minutes: Annotated[int, "How far back to look for readings. Default 10."] = 10
) -> str:
    """Report the measured throughput, packet loss, latency and jitter of user traffic flows.

    These are the readings the traffic agents export and the metrics collector
    stores in the service topology database -- what the network is actually
    carrying, as opposed to the bandwidth the TrafficTest asked for. Use this to
    tell a degraded flow from a healthy one, and to confirm whether a fault or a
    change moved real traffic.

    Every flow is reported per direction: a bidirectional test yields a forward
    and a reverse series, each measured by the agent at one end.
    """
    logger.info(f"query_flow_metrics: {flow_names} window={window_minutes}")
    names = [n.strip() for n in (flow_names or "").split(',') if n.strip()]

    try:
        flows = fetch_traffic_flows(names=names or None)
        measurements = fetch_flow_measurements(
            names or None, window_minutes=max(1, int(window_minutes or 10))
        )
    except Exception as e:
        logger.error(f"Error in query_flow_metrics: {e}")
        return json.dumps({"error": str(e)})

    if names and not flows:
        return json.dumps({
            "flows": [],
            "message": f"No traffic flow named {', '.join(names)} exists in the "
                       "service topology database."
        }, indent=2)

    reported = []
    for flow in flows:
        spec = flow["spec"]
        series = measurements.get(flow["name"], [])
        reported.append({
            "test_name": flow["name"],
            "flow_id": flow["id"],
            "phase": flow["phase"],
            "source_devices": spec.get("source_devices", []),
            "destination_device": spec.get("destination_device"),
            "protocol": spec.get("protocol"),
            "requested_bandwidth": spec.get("bandwidth"),
            "requested_reverse_bandwidth": spec.get("reverse_bandwidth") if spec.get("bidirectional") else None,
            "bidirectional": bool(spec.get("bidirectional")),
            "vpn": spec.get("vpnRef"),
            "measured": summarize_measurement(series),
            "directions": [
                {
                    "flow_id": s["flow_id"],
                    "direction": s["direction"],
                    "source_device": s["source_device"],
                    "measured_by": s["node_name"],
                    "roles": s["roles"],
                    "last_seen": s["timestamp"],
                    "metrics": s["metrics"],
                }
                for s in series
            ],
        })

    # Series whose test has no TrafficFlow row: the test was deleted while its
    # metrics were still inside the one-day retention window.
    known = {f["name"] for f in flows}
    orphaned = sorted(set(measurements) - known)

    result = {
        "window_minutes": window_minutes,
        "flow_count": len(reported),
        "flows": reported,
    }
    if orphaned and not names:
        result["metrics_without_flow"] = orphaned
    if not reported:
        result["message"] = "No traffic flow is recorded in the service topology database."
    return json.dumps(result, indent=2)
