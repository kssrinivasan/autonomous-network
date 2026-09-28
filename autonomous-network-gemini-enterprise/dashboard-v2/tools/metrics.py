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

import logging
import threading
import time
import json
from google.cloud import spanner

from utils.database import get_database, reset_database

# NetworkMetrics no longer exists. It split by metric family and by domain:
#
#   kind='SYSTEM'  (node_*) -> RouterSystemMetrics   in transporttopology-db
#   kind='ROUTING' (frr_*)  -> RouterRoutingSeries    in transporttopology-db
#   traffic-agent metrics   -> TrafficFlowSeries     in servicetopology-db
#
# 'kind' is gone (the table name carries it), 'node_name' became 'router_name'
# on the router tables, and the two router tables no longer share a column set:
# system rows have 'interface' and routing rows have vrf/afi/safi/peer_ip/
# iface/area/collector.
#
# RouterRoutingSeries replaced RouterRoutingMetrics, whose key was too narrow for
# the label set FRR emits -- see gcp/spanner.tpl.yaml. Nothing writes the old
# table any more; do not read it.
ROUTER_DOMAIN = 'transport'
TRAFFIC_DOMAIN = 'service'

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# How far back to look when fetching the "latest" value for each metric.
# The metrics collector writes every 20 s, so 120 s guarantees we always
# have at least one data point while keeping the scan window tiny compared
# to the full 3-hour retention period.
# ---------------------------------------------------------------------------
_LATEST_WINDOW_SECONDS = 120

# ---------------------------------------------------------------------------
# In-memory cache for fetch_all_last_metrics.
# Avoids rescanning the entire RouterSystemMetrics table on every call when the
# supervisor topology endpoint is hit concurrently (metrics change only every
# ~20 s so a 15-second TTL is safe).
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache_value: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL_SECONDS = 15.0

# ---------------------------------------------------------------------------
# Per-test-name result cache for fetch_traffic_test_metrics.
# The dashboard polls every 20 s and may fire one request per linked traffic
# test sequentially.  A 15-second TTL ensures only the first request within
# each poll cycle hits Spanner; subsequent requests for the same test_name
# are served from memory.  The cache is a dict keyed by test_name whose
# values are (result_list, monotonic_timestamp) tuples.
# ---------------------------------------------------------------------------
_traffic_cache_lock = threading.Lock()
_traffic_cache: dict = {}   # test_name → (list[dict], float)




# ---------------------------------------------------------------------------
# Spanner reconnection helpers
# ---------------------------------------------------------------------------

def _is_not_found(exc: Exception) -> bool:
    """Return True if *exc* looks like a Spanner 'database not found' error."""
    try:
        from google.api_core import exceptions as core_exceptions
        if isinstance(exc, core_exceptions.NotFound):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    return "not found" in msg or "cannot find" in msg


def _try_reconnect(exc: Exception, domain: str = ROUTER_DOMAIN) -> bool:
    """Reconnect the cached Spanner handle for *domain* if *exc* is not-found.

    Returns True if a reconnection was attempted (caller should retry the
    operation), False otherwise.
    """
    if _is_not_found(exc):
        logger.warning(
            "Spanner 'not found' error detected — attempting reconnect: %s", exc
        )
        try:
            reset_database(domain)
            logger.info("Spanner reconnection successful")
            return True
        except Exception as reconnect_exc:
            logger.error("Spanner reconnection failed: %s", reconnect_exc)
    return False

def _format_metrics_results(results):
    nodes_data = {}
    for row in results:
        node_name, interface, metric_name, value, timestamp = row
        if not node_name:
            continue
            
        if node_name not in nodes_data:
            nodes_data[node_name] = {
                'timestamp': int(timestamp.timestamp() * 1000) if timestamp else 0,
                'metrics': {
                    'interfaces': {},
                    'cpu': {}
                }
            }
            
        if interface:
            if interface not in nodes_data[node_name]['metrics']['interfaces']:
                nodes_data[node_name]['metrics']['interfaces'][interface] = {}
            
            short_metric_name = metric_name.replace('node_network_', '')
            nodes_data[node_name]['metrics']['interfaces'][interface][short_metric_name] = value
        else:
            nodes_data[node_name]['metrics']['cpu'][metric_name] = value

    last_metrics = {}
    for node_name, data in nodes_data.items():
        last_metrics[node_name] = [{
            'timestamp': data['timestamp'],
            'metrics': data['metrics']
        }]
    return last_metrics


def fetch_last_metrics_for_id(node_id, timestamp_str: str = None):
    """Fetch the most recent value for every (metric_name, interface) pair for
    a single node.

    Args:
        node_id: The node name to fetch metrics for
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches metrics
                      at/before that point in time. If None, fetches latest (live mode).

    Uses a self-join on MAX(timestamp) grouped by (metric_name, interface)
    within a 120-second window.  This avoids ROW_NUMBER() window functions
    which are not supported on all Spanner instance configurations.

    RouterSystemMetrics' PRIMARY KEY (router_name, metric_name, interface,
    timestamp DESC) makes both the inner GROUP BY scan and the outer join
    leading-key range scans — no secondary index needed.
    """
    # Determine the reference timestamp for queries
    if timestamp_str:
        import datetime
        try:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            target_timestamp = datetime.datetime.fromisoformat(timestamp_str)
            timestamp_clause = f"TIMESTAMP('{target_timestamp.isoformat()}')"
        except ValueError as e:
            logger.error(f"Invalid timestamp format: {e}")
            return {}
    else:
        timestamp_clause = "CURRENT_TIMESTAMP()"

    sql = f"""
        SELECT nm.router_name, nm.interface, nm.metric_name, nm.value, nm.timestamp
        FROM RouterSystemMetrics nm
        INNER JOIN (
            SELECT router_name, metric_name, interface, MAX(timestamp) AS max_ts
            FROM RouterSystemMetrics
            WHERE router_name = @node_id
              AND timestamp <= {timestamp_clause}
              AND timestamp >= TIMESTAMP_SUB({timestamp_clause}, INTERVAL @window_seconds SECOND)
            GROUP BY router_name, metric_name, interface
        ) latest
        ON nm.router_name = latest.router_name
           AND nm.metric_name = latest.metric_name
           AND nm.interface = latest.interface
           AND nm.timestamp = latest.max_ts
    """
    params = {"node_id": node_id, "window_seconds": _LATEST_WINDOW_SECONDS}
    param_types = {
        "node_id": spanner.param_types.STRING,
        "window_seconds": spanner.param_types.INT64,
    }

    def _run():
        with get_database(ROUTER_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=params, param_types=param_types)
            return {"node_metrics": _format_metrics_results(results)}

    try:
        return _run()
    except Exception as e:
        if _try_reconnect(e, ROUTER_DOMAIN):
            try:
                return _run()
            except Exception as retry_exc:
                logger.error("fetch_last_metrics_for_id SQL error after reconnect: %s", retry_exc)
                return {}
        logger.error("fetch_last_metrics_for_id SQL error: %s", e)
        return {}


def fetch_all_metrics_for_id(node_id):
  # Legacy support fallback mapping to last metrics to prevent query explosion
  return fetch_last_metrics_for_id(node_id)


def fetch_all_last_metrics(timestamp_str: str = None):
    """Fetch the most recent value for every (node_name, metric_name, interface)
    across all nodes.

    Args:
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches metrics
                      at/before that point in time. If None, fetches latest (live mode).

    Optimisations:
    1. RouterSystemMetrics' PRIMARY KEY (router_name, metric_name, interface,
       timestamp DESC) makes the whole-table scan a leading-key range scan.
    2. Self-join on MAX(timestamp) per (router_name, metric_name, interface) group
       within a 120-second window selects the latest row per group in a single
       pass — no ROW_NUMBER() window function needed (not supported on all
       Spanner instance configurations).
    3. In-memory cache with a 15-second TTL avoids re-scanning Spanner when
       the topology endpoint is hit concurrently (data changes only every 20 s).
       NOTE: Cache is only used in live mode (timestamp_str=None).
    """
    global _cache_value, _cache_ts

    # Only use cache for live mode
    if timestamp_str is None:
        now = time.monotonic()
        with _cache_lock:
            if now - _cache_ts < _CACHE_TTL_SECONDS and _cache_value:
                logger.debug("fetch_all_last_metrics: cache hit (age=%.1fs)", now - _cache_ts)
                return _cache_value

    # Determine the reference timestamp for queries
    if timestamp_str:
        import datetime
        try:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            target_timestamp = datetime.datetime.fromisoformat(timestamp_str)
            timestamp_clause = f"TIMESTAMP('{target_timestamp.isoformat()}')"
        except ValueError as e:
            logger.error(f"Invalid timestamp format: {e}")
            return {}
    else:
        timestamp_clause = "CURRENT_TIMESTAMP()"

    sql = f"""
        SELECT nm.router_name, nm.interface, nm.metric_name, nm.value, nm.timestamp
        FROM RouterSystemMetrics nm
        INNER JOIN (
            SELECT router_name, metric_name, interface, MAX(timestamp) AS max_ts
            FROM RouterSystemMetrics
            WHERE timestamp <= {timestamp_clause}
              AND timestamp >= TIMESTAMP_SUB({timestamp_clause}, INTERVAL @window_seconds SECOND)
            GROUP BY router_name, metric_name, interface
        ) latest
        ON nm.router_name = latest.router_name
           AND nm.metric_name = latest.metric_name
           AND nm.interface = latest.interface
           AND nm.timestamp = latest.max_ts
    """
    params = {"window_seconds": _LATEST_WINDOW_SECONDS}
    param_types = {"window_seconds": spanner.param_types.INT64}

    def _run():
        with get_database(ROUTER_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=params, param_types=param_types)
            result = {"node_metrics": _format_metrics_results(results)}
        # Only cache for live mode
        if timestamp_str is None:
            with _cache_lock:
                global _cache_value, _cache_ts
                _cache_value = result
                _cache_ts = time.monotonic()
        return result

    try:
        return _run()
    except Exception as e:
        if _try_reconnect(e, ROUTER_DOMAIN):
            try:
                return _run()
            except Exception as retry_exc:
                logger.error("fetch_all_last_metrics SQL error after reconnect: %s", retry_exc)
                return {}
        logger.error("fetch_all_last_metrics SQL error: %s", e)
        return {}


def fetch_all_metrics():
  # Legacy support fallback mapping to last metrics to prevent query explosion
  return fetch_all_last_metrics()


def fetch_traffic_test_metrics(test_name: str, timestamp_str: str = None) -> list:
    """
    Fetch the latest traffic-agent metrics for all flows belonging to a TrafficTest.

    Args:
        test_name: The TrafficTest name
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches metrics
                      at/before that point in time. If None, fetches latest (live mode).

    Flow IDs follow the naming convention  {test_name}_{source_device}  and
    {test_name}_{source_device}_rev  (bidirectional reverse).  We match all of
    them by looking for flow_id values that start with  "{test_name}_".

    Returns a list of dicts — one per (flow_id, role, protocol) group — each
    containing the latest value for every traffic-agent metric scraped into
    TrafficFlowSeries.

    The PRIMARY KEY is (flow_id, timestamp DESC, node_name, metric_name, role).
    Filtering on flow_id prefix is done Python-side after a time-window scan
    (the TRAFFIC kind window is small — only 120 s of data).

    We return one entry per (flow_id, role) so the UI can separately filter
    source-role rows (for throughput) and destination-role rows (for latency,
    jitter, packet_loss).
    """
    prefix = f"{test_name}_"
    logger.debug("fetch_traffic_test_metrics: test_name=%r  prefix=%r", test_name, prefix)

    # ── Cache check (only for live mode) ───────────────────────────────────────
    # The dashboard polls every 20 s and may call this function once per linked
    # traffic test in a tight sequential loop.  Serve from the in-memory cache
    # when the entry is younger than the TTL to avoid redundant Spanner scans.
    if timestamp_str is None:
        now = time.monotonic()
        with _traffic_cache_lock:
            cached = _traffic_cache.get(test_name)
            if cached is not None:
                cached_value, cached_ts = cached
                if now - cached_ts < _CACHE_TTL_SECONDS:
                    logger.debug(
                        "fetch_traffic_test_metrics: cache hit for %r (age=%.1fs)",
                        test_name, now - cached_ts,
                    )
                    return cached_value

    # Determine the reference timestamp for queries
    if timestamp_str:
        import datetime
        try:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            target_timestamp = datetime.datetime.fromisoformat(timestamp_str)
            timestamp_clause = f"TIMESTAMP('{target_timestamp.isoformat()}')"
        except ValueError as e:
            logger.error(f"Invalid timestamp format: {e}")
            return []
    else:
        timestamp_clause = "CURRENT_TIMESTAMP()"

    # Query TrafficFlowSeries within the latest 120-second window.
    # The TrafficFlowSeriesByTimestamp index (timestamp DESC) makes this a fast
    # leading-key range scan.  We filter to the requested test prefix Python-side.
    #
    # role is a real column now, not just a labels key.  It is also part of the
    # primary key, so the source-role and destination-role rows of a flow no
    # longer overwrite each other on write — the (flow_id, role) grouping below
    # finally sees both, which is what it always assumed.
    sql = f"""
        SELECT node_name, metric_name, value, labels, timestamp, flow_id, role
        FROM TrafficFlowSeries
        WHERE timestamp <= {timestamp_clause}
          AND timestamp >= TIMESTAMP_SUB({timestamp_clause}, INTERVAL @window_seconds SECOND)
        ORDER BY flow_id, timestamp DESC
    """
    sql_params = {"window_seconds": _LATEST_WINDOW_SECONDS}
    sql_param_types = {
        "window_seconds": spanner.param_types.INT64,
    }

    _METRIC_MAP = {
        # Bidirectional throughput (sent + received) — kept for backward
        # compatibility.  Prefer throughput_sent_bps / throughput_recv_bps.
        "traffic_agent_throughput_bps":       "throughput_bps",
        # Directional gauges — fix 2× double-counting on bidirectional flows.
        "traffic_agent_throughput_sent_bps":  "throughput_sent_bps",
        "traffic_agent_throughput_recv_bps":  "throughput_recv_bps",
        "traffic_agent_latency_ms":           "latency_ms",
        "traffic_agent_jitter_ms":            "jitter_ms",
        "traffic_agent_packet_loss_pct":      "packet_loss_pct",
        "traffic_agent_active_sessions":      "active_sessions",
        "traffic_agent_bytes_sent_total":     "bytes_sent_total",
        "traffic_agent_bytes_received_total": "bytes_received_total",
        "traffic_agent_flow_running":         "flow_running",
    }

    def _run():
        # Group by (flow_id, role) — return one entry per role so the UI can
        # separately access source-role throughput and destination-role
        # latency/jitter/packet_loss.  The previous single-entry-per-flow_id
        # approach caused latency/jitter/loss to always be null because the
        # destination-role rows were merged but their metrics were never
        # populated (the FORCE_INDEX join-back silently dropped them).
        #
        # Key: (flow_id, role) → entry dict
        flows: dict = {}
        # Track which (flow_id, role, metric_name) combinations we've already
        # recorded the latest value for.  The SQL orders by (flow_id,
        # timestamp DESC) so the first row we see for each combination is
        # always the most recent — we skip subsequent rows.
        seen: set = set()

        with get_database(TRAFFIC_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=sql_params, param_types=sql_param_types)
            for node_name, metric_name, value, labels_json, timestamp, flow_id_col, role_col in results:
                    try:
                        labels = json.loads(labels_json) if isinstance(labels_json, str) else (labels_json or {})
                    except Exception:
                        labels = {}

                    flow_id  = labels.get("flow_id", "") or flow_id_col or ""
                    role     = role_col or labels.get("role", "")
                    protocol = labels.get("protocol", "")

                    if not flow_id:
                        continue

                    # Filter to the requested test prefix (Python-side)
                    if not flow_id.startswith(prefix):
                        continue

                    # Group key: (flow_id, role) — one entry per role per flow
                    group_key = (flow_id, role)
                    if group_key not in flows:
                        flows[group_key] = {
                            "flow_id":  flow_id,
                            "device":   node_name or "",
                            "role":     role,
                            "protocol": protocol,
                            "timestamp": timestamp.isoformat() if timestamp else None,
                            # metric fields initialised to None
                            "throughput_bps":       None,
                            "throughput_sent_bps":  None,
                            "throughput_recv_bps":  None,
                            "latency_ms":           None,
                            "jitter_ms":            None,
                            "packet_loss_pct":      None,
                            "active_sessions":      None,
                            "bytes_sent_total":     None,
                            "bytes_received_total": None,
                            "flow_running":         None,
                        }

                    # Keep the most recent timestamp for the group.
                    entry = flows[group_key]
                    if timestamp and entry["timestamp"]:
                        if timestamp.isoformat() > entry["timestamp"]:
                            entry["timestamp"] = timestamp.isoformat()

                    field = _METRIC_MAP.get(metric_name)
                    if field is None or value is None:
                        continue

                    # Only record the first (latest) value per (flow_id, role, metric_name).
                    # The SQL orders by timestamp DESC so the first row is always newest.
                    dedup_key = (flow_id, role, metric_name)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    # First non-None value wins (already deduped to latest above).
                    if entry[field] is None:
                        entry[field] = value

        result = list(flows.values())

        # ── Cache update (only for live mode) ──────────────────────────────
        if timestamp_str is None:
            with _traffic_cache_lock:
                _traffic_cache[test_name] = (result, time.monotonic())

        return result

    try:
        return _run()
    except Exception as e:
        if _try_reconnect(e, TRAFFIC_DOMAIN):
            try:
                return _run()
            except Exception as retry_exc:
                logger.error("fetch_traffic_test_metrics SQL error after reconnect: %s", retry_exc)
                return []
        logger.error("fetch_traffic_test_metrics SQL error: %s", e)
        return []


def fetch_routing_metrics(node_id: str, timestamp_str: str = None) -> dict:
    """
    Fetch the latest underlay (Layer 2) routing-protocol metrics for a router.

    Args:
        node_id: The node name to fetch routing metrics for
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches metrics
                      at/before that point in time. If None, fetches latest (live mode).

    Queries RouterRoutingSeries for the given node, using the
    full (metric_name, labels) key so that per-peer BGP entries, per-VRF route
    counts, etc. are preserved and aggregated correctly.

    Returns a structured dict with OSPF, BGP peer, routing-table, collector,
    and BFD sections ready to be JSON-serialised and sent to the dashboard.

    Uses a self-join on MAX(timestamp) per (metric_name, labels) group within
    a 120-second window.  Avoids ROW_NUMBER() which is not supported on all
    Spanner instance configurations.
    """
    # Determine the reference timestamp for queries
    if timestamp_str:
        import datetime
        try:
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            target_timestamp = datetime.datetime.fromisoformat(timestamp_str)
            timestamp_clause = f"TIMESTAMP('{target_timestamp.isoformat()}')"
        except ValueError as e:
            logger.error(f"Invalid timestamp format: {e}")
            return {}
    else:
        timestamp_clause = "CURRENT_TIMESTAMP()"

    # Group by the PK columns so that each distinct time-series gets its own
    # latest row. This correctly separates
    # e.g. frr_route_total{afi=ipv4,vrf=default} from
    #      frr_route_total{afi=ipv6,vrf=default} and
    #      frr_route_total{afi=ipv4,vrf=BLUE_SPOKE}.
    #
    # The grouping must list every key column. When this read RouterRoutingMetrics
    # it grouped by (metric_name, vrf, afi, peer_ip) only -- but that was also all
    # the table was keyed on, so the OSPF per-interface rows the parser below
    # looks for had already collided on write and only one interface per router
    # ever reached the panel. RouterRoutingSeries keys on iface/area/safi/collector
    # as well, so they have to appear here too or the self-join re-collapses them.
    sql = f"""
        SELECT nm.metric_name, nm.value, nm.labels, nm.timestamp, nm.vrf, nm.afi
        FROM RouterRoutingSeries nm
        INNER JOIN (
            SELECT metric_name, vrf, afi, safi, peer_ip, iface, area, collector,
                   MAX(timestamp) AS max_ts
            FROM RouterRoutingSeries
            WHERE router_name = @node_id
              AND timestamp <= {timestamp_clause}
              AND timestamp >= TIMESTAMP_SUB({timestamp_clause}, INTERVAL @window_seconds SECOND)
            GROUP BY metric_name, vrf, afi, safi, peer_ip, iface, area, collector
        ) latest
        ON nm.metric_name = latest.metric_name
           AND nm.vrf = latest.vrf
           AND nm.afi = latest.afi
           AND nm.safi = latest.safi
           AND nm.peer_ip = latest.peer_ip
           AND nm.iface = latest.iface
           AND nm.area = latest.area
           AND nm.collector = latest.collector
           AND nm.timestamp = latest.max_ts
        WHERE nm.router_name = @node_id
    """
    params = {"node_id": node_id, "window_seconds": _LATEST_WINDOW_SECONDS}
    param_types = {
        "node_id": spanner.param_types.STRING,
        "window_seconds": spanner.param_types.INT64,
    }

    try:
        with get_database(ROUTER_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=params, param_types=param_types)

            ospf_neighbors   = 0
            ospf_adjacencies = 0
            ospf_interfaces  = {}  # key = interface name → {neighbors, adjacencies}
            bgp_peers   = {}   # key = (neighbor, afi, vrf)
            routes      = {}   # key = (afi, vrf)
            collectors  = {}   # key = collector name string
            bfd_peers   = None
            latest_ts   = None

            for metric_name, value, labels_json, timestamp, row_vrf, row_afi in results:
                try:
                    labels = json.loads(labels_json) if isinstance(labels_json, str) else (labels_json or {})
                except Exception:
                    labels = {}

                if latest_ts is None or (timestamp and timestamp > latest_ts):
                    latest_ts = timestamp

                if metric_name == 'frr_ospf_neighbors':
                    ospf_neighbors += int(value or 0)
                    # Track per-interface OSPF neighbors
                    iface = labels.get('iface', labels.get('interface', ''))
                    if iface:
                        if iface not in ospf_interfaces:
                            ospf_interfaces[iface] = {'neighbors': 0, 'adjacencies': 0}
                        ospf_interfaces[iface]['neighbors'] = int(value or 0)

                elif metric_name == 'frr_ospf_neighbor_adjacencies':
                    ospf_adjacencies += int(value or 0)
                    # Track per-interface OSPF adjacencies
                    iface = labels.get('iface', labels.get('interface', ''))
                    if iface:
                        if iface not in ospf_interfaces:
                            ospf_interfaces[iface] = {'neighbors': 0, 'adjacencies': 0}
                        ospf_interfaces[iface]['adjacencies'] = int(value or 0)

                elif metric_name == 'frr_bgp_peer_uptime_seconds':
                    neighbor = labels.get('peer', '') or labels.get('neighbor', '')
                    afi      = labels.get('afi', '')
                    vrf      = labels.get('vrf', 'default')
                    safi     = labels.get('safi', '')
                    peer_as  = labels.get('peer_as', '')
                    local_as = labels.get('local_as', '')
                    key      = (neighbor, afi, vrf, safi)
                    if key not in bgp_peers:
                        bgp_peers[key] = {
                            'neighbor':            neighbor,
                            'afi':                 afi,
                            'vrf':                 vrf,
                            'safi':                safi,
                            'peer_as':             peer_as,
                            'local_as':            local_as,
                            'uptime_seconds':      None,
                            'prefixes_advertised': None,
                        }
                    bgp_peers[key]['uptime_seconds'] = float(value) if value is not None else None

                elif metric_name == 'frr_bgp_peer_prefixes_advertised_count_total':
                    neighbor = labels.get('peer', '') or labels.get('neighbor', '')
                    afi      = labels.get('afi', '')
                    vrf      = labels.get('vrf', 'default')
                    safi     = labels.get('safi', '')
                    peer_as  = labels.get('peer_as', '')
                    local_as = labels.get('local_as', '')
                    key      = (neighbor, afi, vrf, safi)
                    if key not in bgp_peers:
                        bgp_peers[key] = {
                            'neighbor':            neighbor,
                            'afi':                 afi,
                            'vrf':                 vrf,
                            'safi':                safi,
                            'peer_as':             peer_as,
                            'local_as':            local_as,
                            'uptime_seconds':      None,
                            'prefixes_advertised': None,
                        }
                    bgp_peers[key]['prefixes_advertised'] = int(value) if value is not None else None

                elif metric_name == 'frr_route_total':
                    afi = labels.get('afi', '')
                    vrf = labels.get('vrf', 'default')
                    key = (afi, vrf)
                    if key not in routes:
                        routes[key] = {'afi': afi, 'vrf': vrf, 'total': None, 'fib': None}
                    routes[key]['total'] = int(value) if value is not None else None

                elif metric_name == 'frr_route_total_fib':
                    afi = labels.get('afi', '')
                    vrf = labels.get('vrf', 'default')
                    key = (afi, vrf)
                    if key not in routes:
                        routes[key] = {'afi': afi, 'vrf': vrf, 'total': None, 'fib': None}
                    routes[key]['fib'] = int(value) if value is not None else None

                elif metric_name == 'frr_collector_up':
                    collector = labels.get('collector', 'unknown')
                    collectors[collector] = int(value) if value is not None else 0

                elif metric_name == 'frr_bfd_peer_count':
                    bfd_peers = int(value) if value is not None else None

            # Convert ospf_interfaces dict to list, sorted by interface name
            ospf_iface_list = [
                {'interface': iface, 'neighbors': data['neighbors'], 'adjacencies': data['adjacencies']}
                for iface, data in sorted(ospf_interfaces.items())
            ]

            return {
                'node_id':    node_id,
                'ospf':       {
                    'neighbors': ospf_neighbors,
                    'adjacencies': ospf_adjacencies,
                    'interfaces': ospf_iface_list
                },
                'bgp_peers':  list(bgp_peers.values()),
                'routes':     list(routes.values()),
                'collectors': collectors,
                'bfd_peers':  bfd_peers,
                'timestamp':  latest_ts.isoformat() if latest_ts else None,
            }

    except Exception as e:
        logger.error(f"fetch_routing_metrics SQL error: {e}")
        return {'node_id': node_id, 'error': str(e)}


def fetch_router_metrics_range(start_ts: str, end_ts: str, node_names: list = None) -> dict:
    """
    Fetch router metrics time-series for a given time range.

    Returns a dict keyed by node_name, each value being a list of
    { timestamp, metrics: { interfaces: {...}, cpu: {...} } } snapshots
    ordered by timestamp ascending.

    Args:
        start_ts: ISO-8601 start timestamp (inclusive)
        end_ts:   ISO-8601 end timestamp (inclusive)
        node_names: optional list of node names to filter; None = all nodes
    """
    try:
        from datetime import datetime, timezone

        def _parse_iso(s):
            s = s.rstrip('Z')
            if '.' in s:
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

        start_dt = _parse_iso(start_ts)
        end_dt   = _parse_iso(end_ts)
    except Exception as e:
        logger.error("fetch_router_metrics_range: bad timestamp: %s", e)
        return {}

    # The metrics collector stores VyOS interface/system metrics (node_network_*
    # and node_cpu_*) in RouterSystemMetrics; the frr_* routing metrics live in
    # RouterRoutingSeries and are not wanted here. The PRIMARY KEY
    # (router_name, metric_name, interface, timestamp DESC) makes a per-router
    # scan a leading-key range — no index hint needed.
    if node_names:
        sql = """
            SELECT router_name, interface, metric_name, value, timestamp
            FROM RouterSystemMetrics
            WHERE router_name IN UNNEST(@node_names)
              AND timestamp >= @start_ts
              AND timestamp <= @end_ts
            ORDER BY router_name, timestamp ASC
        """
        params = {
            "start_ts":   start_dt,
            "end_ts":     end_dt,
            "node_names": node_names,
        }
        param_types = {
            "start_ts":   spanner.param_types.TIMESTAMP,
            "end_ts":     spanner.param_types.TIMESTAMP,
            "node_names": spanner.param_types.Array(spanner.param_types.STRING),
        }
    else:
        sql = """
            SELECT router_name, interface, metric_name, value, timestamp
            FROM RouterSystemMetrics
            WHERE timestamp >= @start_ts
              AND timestamp <= @end_ts
            ORDER BY router_name, timestamp ASC
        """
        params = {"start_ts": start_dt, "end_ts": end_dt}
        param_types = {
            "start_ts": spanner.param_types.TIMESTAMP,
            "end_ts":   spanner.param_types.TIMESTAMP,
        }

    # Round timestamps to the nearest 30-second bucket so that multiple
    # metric rows written at nearly the same time are merged into a single
    # snapshot per router.  This matches the flow graph's bucketing and
    # prevents the chart from plotting many near-identical timestamps as
    # separate points (which can make values look like zero when the chart
    # renders them too close together).
    _BUCKET_MS = 30_000  # 30 seconds

    def _run():
        # { node_name -> { bucket_ms -> { interfaces: {...}, cpu: {...} } } }
        nodes_ts: dict = {}
        with get_database(ROUTER_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=params, param_types=param_types)
            for node_name, interface, metric_name, value, timestamp in results:
                if not node_name or not timestamp:
                    continue
                ts_ms = int(timestamp.timestamp() * 1000)
                bucket = (ts_ms // _BUCKET_MS) * _BUCKET_MS

                if node_name not in nodes_ts:
                    nodes_ts[node_name] = {}
                if bucket not in nodes_ts[node_name]:
                    nodes_ts[node_name][bucket] = {"interfaces": {}, "cpu": {}}

                if interface:
                    ifaces = nodes_ts[node_name][bucket]["interfaces"]
                    if interface not in ifaces:
                        ifaces[interface] = {}
                    short = metric_name.replace("node_network_", "")
                    # For rate metrics (counters stored as ALIGN_RATE), take the
                    # latest non-zero value within the bucket.  For gauge metrics
                    # (ALIGN_MEAN), overwrite with the latest value.
                    existing = ifaces[interface].get(short)
                    if existing is None or (value is not None and value != 0):
                        ifaces[interface][short] = value
                else:
                    cpu = nodes_ts[node_name][bucket]["cpu"]
                    existing = cpu.get(metric_name)
                    if existing is None or (value is not None and value != 0):
                        cpu[metric_name] = value

        # Convert to sorted list per node
        result = {}
        for node_name, ts_map in nodes_ts.items():
            result[node_name] = [
                {"timestamp": bucket, "metrics": metrics}
                for bucket, metrics in sorted(ts_map.items())
            ]
        return result

    try:
        return _run()
    except Exception as e:
        if _try_reconnect(e, ROUTER_DOMAIN):
            try:
                return _run()
            except Exception as retry_exc:
                logger.error("fetch_router_metrics_range SQL error after reconnect: %s", retry_exc)
                return {}
        logger.error("fetch_router_metrics_range SQL error: %s", e)
        return {}


def fetch_traffic_metrics_range(test_names: list, start_ts: str, end_ts: str) -> dict:
    """
    Fetch traffic-flow metrics time-series for a given time range.

    Returns a dict keyed by flow_id, each value being a list of
    { timestamp, role, device, protocol, throughput_bps, throughput_sent_bps,
      throughput_recv_bps, latency_ms, jitter_ms, packet_loss_pct,
      active_sessions, flow_running } snapshots ordered by timestamp ascending.

    Args:
        test_names: list of TrafficTest names to include (flow_id starts with name_)
        start_ts:   ISO-8601 start timestamp (inclusive)
        end_ts:     ISO-8601 end timestamp (inclusive)
    """
    if not test_names:
        return {}

    try:
        from datetime import datetime, timezone

        def _parse_iso(s):
            s = s.rstrip('Z')
            if '.' in s:
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

        start_dt = _parse_iso(start_ts)
        end_dt   = _parse_iso(end_ts)
    except Exception as e:
        logger.error("fetch_traffic_metrics_range: bad timestamp: %s", e)
        return {}

    _METRIC_MAP = {
        "traffic_agent_throughput_bps":       "throughput_bps",
        "traffic_agent_throughput_sent_bps":  "throughput_sent_bps",
        "traffic_agent_throughput_recv_bps":  "throughput_recv_bps",
        "traffic_agent_latency_ms":           "latency_ms",
        "traffic_agent_jitter_ms":            "jitter_ms",
        "traffic_agent_packet_loss_pct":      "packet_loss_pct",
        "traffic_agent_active_sessions":      "active_sessions",
        "traffic_agent_bytes_sent_total":     "bytes_sent_total",
        "traffic_agent_bytes_received_total": "bytes_received_total",
        "traffic_agent_flow_running":         "flow_running",
    }

    # Build OR conditions for each test prefix
    prefixes = [f"{name}_" for name in test_names]

    # Query TrafficFlowSeries for the requested time range.
    # The PRIMARY KEY (flow_id, timestamp DESC, node_name, metric_name, role)
    # makes per-flow time-range scans efficient.  The TrafficFlowSeriesByTimestamp
    # index (timestamp DESC) supports global time-range scans across all flows.
    sql = """
        SELECT node_name, metric_name, value, labels, flow_id, timestamp, role
        FROM TrafficFlowSeries
        WHERE timestamp >= @start_ts
          AND timestamp <= @end_ts
        ORDER BY flow_id, timestamp ASC
    """
    range_params = {"start_ts": start_dt, "end_ts": end_dt}
    range_param_types = {
        "start_ts": spanner.param_types.TIMESTAMP,
        "end_ts":   spanner.param_types.TIMESTAMP,
    }

    # Round timestamps to the nearest 30-second bucket so that metrics
    # written by the source role and the destination role at nearly the
    # same time are merged into a single snapshot per flow.  The two ends
    # scrape independently and their samples land a second or two apart.
    #
    # This bucketing was originally added to hide a sawtooth on metrics like
    # packet_loss_pct, described at the time as "0 on source, 100 on dest".
    # That sawtooth was the symptom of a write-side bug, not a read-side one:
    # role was missing from the TrafficFlowMetrics primary key, so the two ends
    # overwrote each other and each bucket got whichever end won the race.
    # TrafficFlowSeries puts role in the key (7.8a), so both ends are now
    # present and the per-role merge below does what it always claimed to.
    _BUCKET_MS = 30_000  # 30 seconds

    # Metrics that are only meaningful from the destination role.
    # The source role always reports ~100% packet loss (it sends but never
    # receives), so we must ONLY use destination-role values for these fields.
    # We never fall back to source-role values — if no destination row has
    # arrived yet for this bucket, leave the field as None.
    _DEST_ONLY = {"packet_loss_pct", "latency_ms", "jitter_ms"}

    # Metrics that should be summed across roles (bidirectional totals).
    _ADDITIVE = {
        "throughput_bps", "throughput_sent_bps", "throughput_recv_bps",
        "active_sessions", "bytes_sent_total", "bytes_received_total",
    }

    def _run():
        # { flow_id -> { bucket_ms -> snapshot_dict } }
        flows: dict = {}
        with get_database(TRAFFIC_DOMAIN).snapshot() as snapshot:
            results = snapshot.execute_sql(sql, params=range_params, param_types=range_param_types)
            for node_name, metric_name, value, labels_json, flow_id, timestamp, role_col in results:
                if not flow_id:
                    continue

                # Filter to requested test names
                if not any(flow_id.startswith(p) for p in prefixes):
                    continue

                try:
                    labels = json.loads(labels_json) if isinstance(labels_json, str) else (labels_json or {})
                except Exception:
                    labels = {}

                role     = role_col or labels.get("role", "")
                protocol = labels.get("protocol", "")

                field = _METRIC_MAP.get(metric_name)
                if not field:
                    continue

                ts_ms = int(timestamp.timestamp() * 1000) if timestamp else 0
                # Round down to the nearest 30-second bucket
                bucket = (ts_ms // _BUCKET_MS) * _BUCKET_MS

                if flow_id not in flows:
                    flows[flow_id] = {}

                if bucket not in flows[flow_id]:
                    flows[flow_id][bucket] = {
                        "timestamp":            bucket,
                        "flow_id":              flow_id,
                        "role":                 role,
                        "device":               node_name or "",
                        "protocol":             protocol,
                        "throughput_bps":       None,
                        "throughput_sent_bps":  None,
                        "throughput_recv_bps":  None,
                        "latency_ms":           None,
                        "jitter_ms":            None,
                        "packet_loss_pct":      None,
                        "active_sessions":      None,
                        "bytes_sent_total":     None,
                        "bytes_received_total": None,
                        "flow_running":         None,
                    }

                snap = flows[flow_id][bucket]

                # Merge strategy per metric type:
                #
                # 1. Destination-only metrics (packet_loss_pct, latency_ms,
                #    jitter_ms): ONLY accept values from the destination role.
                #    The source role always reports ~100% loss (it sends but
                #    never receives), so source-role values for these fields
                #    are meaningless and must be discarded.  We never fall
                #    back to source values — leave as None if no dest row yet.
                #
                # 2. Additive metrics (throughput, bytes, sessions): sum
                #    contributions from all roles so the total reflects the
                #    full bidirectional traffic.
                #
                # 3. Everything else: first non-None value wins.
                if value is None:
                    pass  # nothing to merge
                elif field in _DEST_ONLY:
                    # ONLY accept destination-role values; discard source-role
                    if role == "destination":
                        snap[field] = value
                    # else: source role — skip entirely (do NOT fall back)
                elif field in _ADDITIVE:
                    snap[field] = (snap[field] or 0) + value
                else:
                    if snap[field] is None:
                        snap[field] = value

        # Convert to sorted lists keyed by flow_id
        result: dict = {}
        for flow_id, bucket_map in flows.items():
            result[flow_id] = sorted(bucket_map.values(), key=lambda x: x["timestamp"])
        return result

    try:
        return _run()
    except Exception as e:
        if _try_reconnect(e, TRAFFIC_DOMAIN):
            try:
                return _run()
            except Exception as retry_exc:
                logger.error("fetch_traffic_metrics_range SQL error after reconnect: %s", retry_exc)
                return {}
        logger.error("fetch_traffic_metrics_range SQL error: %s", e)
        return {}


#: Metric tables clear_network_metrics() empties, by the database that owns
#: them. NetworkMetrics split into two transport tables; the traffic tables
#: moved to service, so this is now two databases rather than one.
_METRIC_TABLES = (
    (ROUTER_DOMAIN, "RouterSystemMetrics"),
    (ROUTER_DOMAIN, "RouterRoutingSeries"),
    (TRAFFIC_DOMAIN, "TrafficFlowSeries"),
    # Superseded and no longer written, but a stale install can still be holding
    # rows, and "clear metrics" should leave nothing behind.
    (ROUTER_DOMAIN, "RouterRoutingMetrics"),
    (TRAFFIC_DOMAIN, "TrafficFlowMetrics"),
)


def clear_network_metrics():
    """
    Clears all records from the router and traffic-flow metric tables using
    Partitioned DML, which bypasses the 20,000 mutation-per-transaction limit and
    is safe for tables with large numbers of rows.

    Returns:
        bool: True if every table was cleared, False if any of them failed.
    """
    ok = True
    for domain, table in _METRIC_TABLES:
        try:
            row_count = get_database(domain).execute_partitioned_dml(
                f"DELETE FROM {table} WHERE TRUE"
            )
            logger.debug(f"Successfully cleared ~{row_count} records from {table}")
        except Exception as e:
            # Keep going: one absent table must not leave the others populated.
            logger.error(f"Failed to clear the {table} table: {e}")
            ok = False

    # Invalidate all in-memory caches so the next read doesn't serve stale data.
    with _cache_lock:
        global _cache_value, _cache_ts
        _cache_value = {}
        _cache_ts = 0.0
    with _traffic_cache_lock:
        _traffic_cache.clear()
    return ok
