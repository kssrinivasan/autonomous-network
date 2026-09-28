
import datetime
import hashlib
import json
import logging
import os
from collections import Counter
from typing import List, Dict, Optional, Tuple

import google.auth
from google.cloud import spanner

logger = logging.getLogger(__name__)


def _render_spanner_query(query: str, params: dict) -> str:
    """Substitute @param placeholders with Spanner-literal values for copy-paste into Spanner Studio."""
    rendered = query
    # Sort by length descending so longer names are replaced before shorter prefixes
    for key in sorted(params.keys(), key=len, reverse=True):
        value = params[key]
        if isinstance(value, datetime.datetime):
            literal = f"TIMESTAMP '{value.isoformat()}'"
        elif isinstance(value, list):
            parts = []
            for item in value:
                parts.append(f"'{item}'" if isinstance(item, str) else str(item))
            literal = f"[{', '.join(parts)}]"
        elif isinstance(value, str):
            escaped = value.replace("'", "\\'")
            literal = f"'{escaped}'"
        elif value is None:
            literal = "NULL"
        else:
            literal = str(value)
        rendered = rendered.replace(f"@{key}", literal)
    return rendered


def _parse_speed_bps(speed_str: str) -> float:
    """Convert a speed string ('1G', '10G', '1000M', '100M') to bits-per-second.

    Falls back to 1 Gbps if the string cannot be parsed.
    """
    if not speed_str:
        return 1e9
    s = speed_str.strip().upper()
    try:
        if s.endswith("GBIT") or s.endswith("GBPS"):
            return float(s[:-4]) * 1e9
        if s.endswith("G"):
            return float(s[:-1]) * 1e9
        if s.endswith("MBIT") or s.endswith("MBPS"):
            return float(s[:-4]) * 1e6
        if s.endswith("M"):
            return float(s[:-1]) * 1e6
        if s.endswith("KBIT") or s.endswith("KBPS"):
            return float(s[:-4]) * 1e3
        if s.endswith("K"):
            return float(s[:-1]) * 1e3
        return float(s)
    except ValueError:
        return 1e9


class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""

    def __init__(
        self,
        instance_id: str,
        database_id: str,
        service_database_id: Optional[str] = None,
        infra_database_id: Optional[str] = None,
        num_snapshots: int = 50,
        interval_minutes: int = 5,
        project_id: Optional[str] = None,
        from_time: Optional[datetime.datetime] = None,
        to_time: Optional[datetime.datetime] = None,
    ):
        logger.info(
            f"Initializing SpannerDataset: instance_id={instance_id}, "
            f"database_id={database_id}, service_database_id={service_database_id}, "
            f"infra_database_id={infra_database_id}, num_snapshots={num_snapshots}, "
            f"interval_minutes={interval_minutes}, project_id={project_id}, "
            f"from_time={from_time}, to_time={to_time}"
        )

        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
        if creds_path and os.path.exists(creds_path):
            credentials, detected_project = google.auth.load_credentials_from_file(
                creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            self.client = spanner.Client(
                project=effective_project,
                credentials=credentials,
                disable_builtin_metrics=True,
            )
        else:
            credentials, detected_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            self.client = spanner.Client(
                project=effective_project,
                credentials=credentials,
                disable_builtin_metrics=True,
            )

        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)  # Transport database

        # Service database (for TrafficFlow, TrafficFlowSeries)
        if service_database_id:
            self.service_database = self.instance.database(service_database_id)
        else:
            self.service_database = None
            logger.warning("Service database not configured - traffic flow features will be unavailable")

        # Infrastructure database (for ComputeHost/Device)
        if infra_database_id:
            self.infra_database = self.instance.database(infra_database_id)
        else:
            self.infra_database = None
            logger.warning("Infrastructure database not configured - device resolution may use fallback")

        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        self.from_time = from_time
        self.to_time = to_time
        logger.info("SpannerDataset initialized successfully")

    def _get_latest_timestamp(self) -> datetime.datetime:
        """Query Spanner for the most recent data timestamp across topology and metrics tables."""
        # Query transport database
        query_transport = """
            SELECT MAX(ts) FROM (
                SELECT MAX(valid_start_ts) AS ts FROM PhysicalRouter
                UNION ALL
                SELECT MAX(valid_start_ts) AS ts FROM PhysicalInterface
                UNION ALL
                SELECT MAX(timestamp)      AS ts FROM RouterSystemMetrics
                UNION ALL
                SELECT MAX(timestamp)      AS ts FROM RouterRoutingSeries
            )
        """

        timestamps = []
        try:
            with self.database.snapshot() as sn:
                results = sn.execute_sql(query_transport)
                row = results.one_or_none()
                if row and row[0] is not None:
                    timestamps.append(row[0])
        except Exception as e:
            logger.warning(f"Could not get timestamp from transport database: {e}")

        # Query service database for TrafficFlowSeries
        if self.service_database:
            query_service = "SELECT MAX(timestamp) AS ts FROM TrafficFlowSeries"
            try:
                with self.service_database.snapshot() as sn:
                    results = sn.execute_sql(query_service)
                    row = results.one_or_none()
                    if row and row[0] is not None:
                        timestamps.append(row[0])
            except Exception as e:
                logger.warning(f"Could not get timestamp from service database: {e}")

        if timestamps:
            latest = max(timestamps)
            ts = datetime.datetime(
                latest.year, latest.month, latest.day,
                latest.hour, latest.minute, latest.second, latest.microsecond,
            )
            logger.info(f"Latest timestamp across all databases: {ts.isoformat()}")
            return ts

        logger.warning("Could not determine latest timestamp, falling back to current time")
        return datetime.datetime.utcnow()

    def _get_earliest_timestamp(self) -> datetime.datetime:
        """Query Spanner for the earliest data timestamp across topology and metrics tables."""
        # Query transport database
        query_transport = """
            SELECT MIN(ts) FROM (
                SELECT MIN(valid_start_ts) AS ts FROM PhysicalRouter
                UNION ALL
                SELECT MIN(valid_start_ts) AS ts FROM PhysicalInterface
                UNION ALL
                SELECT MIN(timestamp)      AS ts FROM RouterSystemMetrics
                UNION ALL
                SELECT MIN(timestamp)      AS ts FROM RouterRoutingSeries
            )
        """

        timestamps = []
        try:
            with self.database.snapshot() as sn:
                results = sn.execute_sql(query_transport)
                row = results.one_or_none()
                if row and row[0] is not None:
                    timestamps.append(row[0])
        except Exception as e:
            logger.warning(f"Could not get earliest timestamp from transport database: {e}")

        # Query service database for TrafficFlowSeries
        if self.service_database:
            query_service = "SELECT MIN(timestamp) AS ts FROM TrafficFlowSeries"
            try:
                with self.service_database.snapshot() as sn:
                    results = sn.execute_sql(query_service)
                    row = results.one_or_none()
                    if row and row[0] is not None:
                        timestamps.append(row[0])
            except Exception as e:
                logger.warning(f"Could not get earliest timestamp from service database: {e}")

        if timestamps:
            earliest = min(timestamps)
            ts = datetime.datetime(
                earliest.year, earliest.month, earliest.day,
                earliest.hour, earliest.minute, earliest.second, earliest.microsecond,
            )
            logger.info(f"Earliest timestamp across all databases: {ts.isoformat()}")
            return ts

        logger.warning("Could not determine earliest timestamp")
        # Fallback: assume data starts 24 hours ago
        return datetime.datetime.utcnow() - datetime.timedelta(hours=24)

    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps stepping backwards from an end time.

        End time selection:
            - ``to_time`` provided → use it directly.
            - ``to_time`` not provided → query Spanner for the latest data timestamp.

        Number of snapshots:
            - ``from_time`` provided → step back at ``interval_minutes`` until the
              next step would go before ``from_time`` (``num_snapshots`` is ignored).
            - ``from_time`` not provided → generate exactly ``num_snapshots`` timestamps,
              but auto-adjust the window if it extends before the earliest available data.
        """
        end_time = self.to_time if self.to_time is not None else self._get_latest_timestamp()

        step = datetime.timedelta(minutes=self.interval_minutes)

        if self.from_time is not None:
            timestamps = []
            ts = end_time
            while ts >= self.from_time:
                timestamps.append(ts)
                ts = ts - step
            timestamps.reverse()
        else:
            # Calculate requested window
            requested_start = end_time - (step * (self.num_snapshots - 1))

            # Check earliest available data in Spanner
            earliest_available = self._get_earliest_timestamp()

            # Adjust start time if requested window goes before available data
            if requested_start < earliest_available:
                logger.warning(
                    f"Requested time window starts at {requested_start.isoformat()}, "
                    f"but earliest available data is {earliest_available.isoformat()}. "
                    f"Adjusting window to fit available data."
                )
                actual_start = earliest_available
            else:
                actual_start = requested_start

            # Generate timestamps from actual_start to end_time
            timestamps = []
            ts = actual_start
            while ts <= end_time:
                timestamps.append(ts)
                ts = ts + step

            # If we got fewer timestamps than requested, log it
            if len(timestamps) < self.num_snapshots:
                logger.warning(
                    f"Generated {len(timestamps)} timestamps (requested {self.num_snapshots}). "
                    f"Limited by available data in Spanner."
                )

        logger.info(
            f"Generated {len(timestamps)} timestamps from "
            f"{timestamps[0].isoformat()} to {timestamps[-1].isoformat()}"
        )
        return timestamps

    def fetch_snapshot(self, timestamp: datetime.datetime) -> Dict:
        """Fetches the full heterogeneous network snapshot at ``timestamp``.

        Returns a dict with keys:
            timestamp   — ISO string
            nodes       — list of node dicts (router / interface / vpn / flow)
            edges       — list of edge dicts (source, target, relation)

        Node dict keys by type
        ──────────────────────
        router:
            id, type, hostname, role, state, cpu, mem, ospf_num_routes,
            pfx_count_norm, rx_bytes_rate, tx_bytes_rate
        interface:
            id, type, name, device_id, speed_bps, state, rx_drops,
            tx_drops, mtu_norm, rx_errs_rate, rx_bytes_rate, tx_bytes_rate,
            rx_err_gradient, tx_util, rx_util
        vpn:
            id, type, name, state, num_active_peers, total_prefixes
        flow:
            id, type, name, is_running, is_bidirectional,
            throughput_sent_bps (TrafficFlowSeries, role=source),
            throughput_recv_bps (TrafficFlowSeries, role=destination),
            latency_ms, jitter_ms, packet_loss_pct (UDP only, role=destination)
        """
        logger.info(f"Fetching snapshot for timestamp: {timestamp.isoformat()}")
        snapshot_data = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}

        # Spanner TIMESTAMP comparisons require tz-aware datetimes. Convert explicitly
        # to a plain datetime to handle DatetimeWithNanoseconds from Spanner results.
        ts_utc = datetime.datetime(
            timestamp.year, timestamp.month, timestamp.day,
            timestamp.hour, timestamp.minute, timestamp.second,
            timestamp.microsecond,
            tzinfo=timestamp.tzinfo if timestamp.tzinfo is not None else datetime.timezone.utc,
        )

        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {"ts": ts_utc}
        param_types = {"ts": spanner.param_types.TIMESTAMP}

        logger.debug(f"[snapshot] Opening Spanner snapshot for ts={timestamp.isoformat()}")
        with self.database.snapshot(multi_use=True) as sn:

            # ── 1. Routers ──────────────────────────────────────────────────
            router_ids: Dict[str, str] = {}  # router_id → hostname
            query_routers = f"""
                SELECT id, name, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] PhysicalRouter:\n%s", _render_spanner_query(query_routers, params))
            for row in sn.execute_sql(query_routers, params=params, param_types=param_types):
                r_id, r_name, r_role, r_status = row
                state_val = 1.0 if r_status and r_status.lower() == "running" else 0.0
                role_str = (r_role or "").upper()
                router_ids[r_id] = r_name
                logger.debug(f"[snapshot]   router: id={r_id} name={r_name} role={role_str} status={r_status} state={state_val}")
                snapshot_data["nodes"].append({
                    "id": r_id,
                    "type": "router",
                    "hostname": r_name,
                    "role": role_str,
                    "state": state_val,
                    "cpu": None,
                    "mem": None,
                    "ospf_num_routes": None,
                    "pfx_count_norm": None,
                    "vrf_rt_import_mismatch": 0.0,
                })
            logger.info(f"Fetched {len(router_ids)} routers")

            # ── 2. Interfaces ────────────────────────────────────────────────
            iface_ids: Dict[str, str] = {}  # interface_id → router_id
            iface_speed: Dict[str, float] = {}  # interface_id → speed_bps
            query_ifaces = f"""
                SELECT id, router_id, name, speed, status
                FROM PhysicalInterface WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] PhysicalInterface:\n%s", _render_spanner_query(query_ifaces, params))
            for row in sn.execute_sql(query_ifaces, params=params, param_types=param_types):
                i_id, i_router_id, i_name, i_speed, i_status = row
                state_val = 1.0 if i_status and i_status.lower() == "up" else 0.0
                speed_bps = _parse_speed_bps(i_speed)
                iface_ids[i_id] = i_router_id
                iface_speed[i_id] = speed_bps
                logger.debug(
                    f"[snapshot]   interface: id={i_id} name={i_name} router={i_router_id} "
                    f"speed={i_speed}({speed_bps:.0f}bps) status={i_status} state={state_val}"
                )
                snapshot_data["nodes"].append({
                    "id": i_id,
                    "type": "interface",
                    "name": i_name,
                    "device_id": i_router_id,
                    "speed_bps": speed_bps,
                    "state": state_val,
                    "rx_drops": None,
                    "tx_drops": None,
                    "mtu_norm": None,
                    "rx_errs_rate": None,
                    "rx_bytes_rate": None,
                    "tx_bytes_rate": None,
                    # Temporal features — filled in by compute_temporal_features()
                    "rx_err_gradient": None,
                    "tx_util": None,
                    "rx_util": None,
                })
            logger.info(f"Fetched {len(iface_ids)} interfaces")

            # ── 3. has_interface edges (router → interface) ──────────────────
            logger.debug("[snapshot] Building has_interface edges")
            for iface_node in snapshot_data["nodes"]:
                if iface_node["type"] == "interface":
                    snapshot_data["edges"].append({
                        "source": iface_node["device_id"],
                        "target": iface_node["id"],
                        "relation": "has_interface",
                    })
                    snapshot_data["edges"].append({
                        "source": iface_node["id"],
                        "target": iface_node["device_id"],
                        "relation": "interface_of",
                    })
                    logger.debug(
                        f"[snapshot]   has_interface/interface_of: {iface_node['device_id']} <-> {iface_node['id']} ({iface_node['name']})"
                    )
            logger.debug(f"[snapshot] has_interface edges: {sum(1 for e in snapshot_data['edges'] if e['relation'] == 'has_interface')}")

            # ── 4. connected_to edges (interface ↔ interface) ────────────────
            connected_to_count = 0
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM Interface_Link il1
                JOIN Interface_Link il2 ON il1.link_id = il2.link_id
                WHERE il1.interface_id < il2.interface_id
                  AND il1.valid_start_ts <= @ts
                  AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                  AND il2.valid_start_ts <= @ts
                  AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            logger.debug("[snapshot:sql] Interface_Link (connected_to):\n%s", _render_spanner_query(query_links, params))
            for row in sn.execute_sql(query_links, params=params, param_types=param_types):
                a, b = row
                logger.debug(f"[snapshot]   connected_to: {a} <-> {b}")
                snapshot_data["edges"].append({"source": a, "target": b, "relation": "connected_to"})
                snapshot_data["edges"].append({"source": b, "target": a, "relation": "connected_to"})
                connected_to_count += 1
            logger.debug(f"[snapshot] connected_to pairs: {connected_to_count} ({connected_to_count * 2} directed edges)")

            # ── 5. peer_of edges (router ↔ router) ──────────────────────────
            # Sources: (a) OSPF peers via shared PhysicalLink, (b) iBGP peers via
            # BGPSession.peer_ip → PhysicalInterface.ip_address.  Both use the
            # unified "peer_of" relation.  A set deduplicates across both sources.
            peer_pairs: set = set()

            query_ospf = f"""
                SELECT DISTINCT r1.id AS router_a, r2.id AS router_b
                FROM PhysicalLink pl
                JOIN Interface_Link il1 ON il1.link_id = pl.id
                JOIN Interface_Link il2 ON il2.link_id = pl.id
                    AND il1.interface_id < il2.interface_id
                JOIN PhysicalInterface pi1 ON pi1.id = il1.interface_id
                    AND pi1.valid_start_ts <= @ts
                    AND (pi1.valid_end_ts > @ts OR pi1.valid_end_ts IS NULL)
                JOIN PhysicalInterface pi2 ON pi2.id = il2.interface_id
                    AND pi2.valid_start_ts <= @ts
                    AND (pi2.valid_end_ts > @ts OR pi2.valid_end_ts IS NULL)
                JOIN PhysicalRouter r1 ON r1.id = pi1.router_id
                    AND r1.valid_start_ts <= @ts
                    AND (r1.valid_end_ts > @ts OR r1.valid_end_ts IS NULL)
                JOIN PhysicalRouter r2 ON r2.id = pi2.router_id
                    AND r2.valid_start_ts <= @ts
                    AND (r2.valid_end_ts > @ts OR r2.valid_end_ts IS NULL)
                WHERE pl.valid_start_ts <= @ts
                  AND (pl.valid_end_ts > @ts OR pl.valid_end_ts IS NULL)
                  AND il1.valid_start_ts <= @ts
                  AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                  AND il2.valid_start_ts <= @ts
                  AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
                  AND r1.id != r2.id
            """
            for row in sn.execute_sql(query_ospf, params=params, param_types=param_types):
                ra, rb = row
                peer_pairs.add(tuple(sorted([ra, rb])))

            query_bgp_peer = f"""
                SELECT DISTINCT v.router_id AS router_a, pi.router_id AS router_b
                FROM BGPSession bs
                JOIN VRF v ON v.id = bs.vrf_id
                    AND v.valid_start_ts <= @ts
                    AND (v.valid_end_ts > @ts OR v.valid_end_ts IS NULL)
                JOIN PhysicalInterface pi ON pi.ip_address = bs.peer_ip
                    AND pi.valid_start_ts <= @ts
                    AND (pi.valid_end_ts > @ts OR pi.valid_end_ts IS NULL)
                WHERE bs.valid_start_ts <= @ts
                  AND (bs.valid_end_ts > @ts OR bs.valid_end_ts IS NULL)
                  AND v.router_id != pi.router_id
            """
            for row in sn.execute_sql(query_bgp_peer, params=params, param_types=param_types):
                ra, rb = row
                peer_pairs.add(tuple(sorted([ra, rb])))

            for ra, rb in peer_pairs:
                snapshot_data["edges"].append({"source": ra, "target": rb, "relation": "peer_of"})
                snapshot_data["edges"].append({"source": rb, "target": ra, "relation": "peer_of"})
            logger.info(f"peer_of pairs: {len(peer_pairs)}")

            # ── 6. BGP sessions — used for VPN active-peer counts only ───────
            # bgp_session is no longer a node type; we query to derive VPN features.
            # vrf_vpn: vrf_id → vpn_id;  vpn_active_peers: vpn_id → count
            # vrf_name_to_vpn_id: VRF.name → vpn_id  (used to match frr_route_total vrf label)
            vrf_vpn: Dict[str, str] = {}
            vrf_name_to_vpn_id: Dict[str, str] = {}
            vpn_active_peers: Dict[str, int] = {}
            query_bgp = f"""
                SELECT bs.vrf_id, bs.status, v.vpn_id, v.name
                FROM BGPSession bs
                JOIN VRF v ON v.id = bs.vrf_id
                    AND v.valid_start_ts <= @ts
                    AND (v.valid_end_ts > @ts OR v.valid_end_ts IS NULL)
                WHERE bs.valid_start_ts <= @ts
                  AND (bs.valid_end_ts > @ts OR bs.valid_end_ts IS NULL)
                  AND v.vpn_id IS NOT NULL
            """
            for row in sn.execute_sql(query_bgp, params=params, param_types=param_types):
                vrf_id, bs_status, vpn_id, vrf_name = row
                if vpn_id:
                    vrf_vpn[vrf_id] = vpn_id
                    if vrf_name:
                        vrf_name_to_vpn_id[vrf_name] = vpn_id
                    if bs_status and bs_status.lower() == "established":
                        vpn_active_peers[vpn_id] = vpn_active_peers.get(vpn_id, 0) + 1
            logger.info(f"BGP sessions processed for VPN peer counts: {len(vrf_vpn)} VRF→VPN mappings")

            # ── 7. VPN nodes + member_of / has_member edges ──────────────────
            vpn_ids: set = set()
            query_vpn = f"""
                SELECT vpn.id, vpn.name, vpn.status, vpn.topology, vpn.valid_start_ts
                FROM L3VPNService vpn
                WHERE vpn.valid_start_ts <= @ts
                  AND (vpn.valid_end_ts > @ts OR vpn.valid_end_ts IS NULL)
            """
            for row in sn.execute_sql(query_vpn, params=params, param_types=param_types):
                vpn_id, vpn_name, vpn_status, vpn_topology, vpn_start = row
                state_val = 1.0 if vpn_status and vpn_status.lower() in ("active", "running") else 0.0
                active_peers = float(vpn_active_peers.get(vpn_id, 0))
                vpn_ids.add(vpn_id)
                snapshot_data["nodes"].append({
                    "id": vpn_id,
                    "type": "vpn",
                    "name": vpn_name,
                    "state": state_val,
                    "num_active_peers": active_peers,
                    "total_prefixes": 0.0,  # populated from metrics if available
                })
                logger.debug(f"[snapshot]   vpn: id={vpn_id} name={vpn_name} state={state_val} peers={active_peers}")
            logger.info(f"Fetched {len(vpn_ids)} VPN nodes")

            # member_of / has_member: router ↔ vpn via VRF
            # Also: collect per-router VRF data (rt_import + vrf_name) so we can compute
            # router-level VRF aggregation features (rt_import_signature, vrf_min_routes,
            # vrf_count) further down in section 9 after metric data is available.
            # ORDER BY for deterministic insertion order — Counter.most_common
            # tie-break uses insertion order, so without ORDER BY the chosen
            # consensus would flip across snapshots when there's a tie.
            query_vrf_members = f"""
                SELECT v.router_id, v.vpn_id, v.name, v.config
                FROM VRF v
                WHERE v.vpn_id IS NOT NULL
                  AND v.valid_start_ts <= @ts
                  AND (v.valid_end_ts > @ts OR v.valid_end_ts IS NULL)
                ORDER BY v.vpn_id, v.name, v.router_id
            """
            member_count = 0
            seen_member_pairs = set()  # dedup edge insertion since query is no longer DISTINCT
            router_vrfs: Dict[str, List[Dict]] = {}  # router_id → [{vrf_name, rt_import, vpn_id}, ...]
            # Key by (vpn_id, vrf_name) — same vrf_name in two different VPNs must
            # NOT be merged into one consensus group.
            vrfs_by_vpnname: Dict[Tuple[str, str], List[Tuple[str, frozenset]]] = {}
            # Per router, the list of (vpn_id, vrf_name) tuples it hosts — used for
            # the per-router aggregation below.
            router_to_vrfs: Dict[str, List[Tuple[str, str]]] = {}

            for row in sn.execute_sql(query_vrf_members, params=params, param_types=param_types):
                router_id, vpn_id, vrf_name, vrf_config = row
                if router_id and vpn_id and vpn_id in vpn_ids:
                    pair = (router_id, vpn_id)
                    if pair not in seen_member_pairs:
                        snapshot_data["edges"].append({"source": router_id, "target": vpn_id, "relation": "member_of"})
                        snapshot_data["edges"].append({"source": vpn_id, "target": router_id, "relation": "has_member"})
                        seen_member_pairs.add(pair)
                        member_count += 1
                if router_id and vrf_name and vpn_id:
                    rt_import: List[str] = []
                    try:
                        cfg = json.loads(vrf_config) if isinstance(vrf_config, str) else (vrf_config or {})
                        rt_raw = cfg.get("rt_import") if isinstance(cfg, dict) else None
                        if isinstance(rt_raw, list):
                            rt_import = [str(x) for x in rt_raw]
                        elif rt_raw is not None:
                            rt_import = [str(rt_raw)]
                    except Exception as e:
                        logger.debug(f"[snapshot]   VRF {vrf_name} config parse failed: {e}")
                    router_vrfs.setdefault(router_id, []).append({
                        "vrf_name": vrf_name,
                        "rt_import": rt_import,
                        "vpn_id": vpn_id,
                    })
                    vrfs_by_vpnname.setdefault((vpn_id, vrf_name), []).append((router_id, frozenset(rt_import)))
                    router_to_vrfs.setdefault(router_id, []).append((vpn_id, vrf_name))
            logger.info(f"member_of pairs: {member_count}; VRFs collected for {len(router_vrfs)} routers")

            # ── Jaccard consensus mismatch for VRF RT imports ──────────────
            # vrf_mismatch[(router_id, vpn_id, vrf_name)] ∈ [0, 1]: 0 = matches
            # the consensus rt_import for its (vpn_id, vrf_name) peer group;
            # 1 = totally disjoint. Singletons + ambiguous-consensus (no clear
            # majority > 50%) → 0.0 (no signal, avoid false positives).
            vrf_mismatch: Dict[Tuple[str, str, str], float] = {}
            for r_id, vn_pairs in router_to_vrfs.items():
                for vpn_id, vrf_name in vn_pairs:
                    vrf_mismatch[(r_id, vpn_id, vrf_name)] = 0.0

            for (vpn_id, vrf_name), members in vrfs_by_vpnname.items():
                if len(members) < 2:
                    continue
                rt_sets = [rt_set for _, rt_set in members]
                top_rt, top_count = Counter(rt_sets).most_common(1)[0]
                # Require strict majority — a 2-vs-2 tie leaves consensus
                # undefined; better to emit no signal than flag two routers
                # arbitrarily as mismatched.
                if top_count / len(members) <= 0.5:
                    logger.debug(
                        f"[snapshot]   no clear consensus for VRF ({vpn_id}, {vrf_name}): "
                        f"winner has {top_count}/{len(members)} votes — skipping"
                    )
                    continue
                consensus_rt = top_rt
                for r_id, rt_set in members:
                    if not rt_set and not consensus_rt:
                        score = 0.0
                    elif not rt_set or not consensus_rt:
                        score = 1.0
                    else:
                        jaccard = len(rt_set.intersection(consensus_rt)) / len(rt_set.union(consensus_rt))
                        score = 1.0 - jaccard
                    vrf_mismatch[(r_id, vpn_id, vrf_name)] = score

            # ── 8. Flow nodes + edges ────────────────────────────────────────
            # Metrics time window — reused by section 9 below.
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)

            # Device name/ID mappings (from infrastructure database if available)
            device_name_to_id: Dict[str, str] = {}
            device_id_to_name: Dict[str, str] = {}
            device_to_interface: Dict[str, str] = {}

            # Query infrastructure database for ComputeHost (formerly Device)
            if self.infra_database:
                query_compute = f"""
                    SELECT ch.id, ch.name
                    FROM ComputeHost ch
                    WHERE ch.valid_start_ts <= @ts
                      AND (ch.valid_end_ts > @ts OR ch.valid_end_ts IS NULL)
                      AND ch.name IS NOT NULL
                """
                try:
                    with self.infra_database.snapshot() as sn_infra:
                        for row in sn_infra.execute_sql(query_compute, params=params, param_types=param_types):
                            dev_id, dev_name = row
                            if dev_id and dev_name:
                                device_name_to_id[dev_name] = dev_id
                                device_id_to_name[dev_id] = dev_name
                    logger.info(f"Loaded {len(device_name_to_id)} devices from infrastructure database")
                except Exception as e:
                    logger.warning(f"Could not query infrastructure database for devices: {e}")
            else:
                logger.debug("Infrastructure database not configured - device names unavailable")

            # Resolve device_id → interface_id via IP address matching (transport database)
            # Build IP → interface lookup from PhysicalInterface
            query_iface_ips = f"""
                SELECT id, ip_address
                FROM PhysicalInterface
                WHERE valid_start_ts <= @ts
                  AND (valid_end_ts > @ts OR valid_end_ts IS NULL)
                  AND ip_address IS NOT NULL
            """
            ip_to_interface: Dict[str, str] = {}
            for row in sn.execute_sql(query_iface_ips, params=params, param_types=param_types):
                iface_id, ip_addr = row
                if iface_id and ip_addr:
                    ip_to_interface[ip_addr] = iface_id
            logger.debug(f"Built IP→interface mapping: {len(ip_to_interface)} entries")

            # TrafficFlow CRD: metadata for each active flow (from service database)
            crd_flows: Dict[str, Dict] = {}  # base_flow_name → metadata
            if self.service_database:
                query_flows = f"""
                    SELECT tf.id, tf.name, tf.src_device_id, tf.dst_device_id, tf.phase,
                           JSON_VALUE(tf.config, '$.spec.bidirectional'),
                           JSON_VALUE(tf.config, '$.metadata.labels.vpn')
                    FROM TrafficFlow tf
                    WHERE tf.valid_start_ts <= @ts
                      AND (tf.valid_end_ts > @ts OR tf.valid_end_ts IS NULL)
                """
                try:
                    with self.service_database.snapshot() as sn_service:
                        for row in sn_service.execute_sql(query_flows, params=params, param_types=param_types):
                            f_id, f_name, src_dev, dst_dev, f_phase, f_bidir, vpn_label = row
                            if f_name:
                                crd_flows[f_name] = {
                                    "id": f_id,
                                    "src_dev": src_dev,
                                    "dst_dev": dst_dev,
                                    "phase": f_phase,
                                    "bidir": f_bidir,
                                    "vpn_label": vpn_label,
                                }
                                # Try to resolve device IDs to interfaces via IP lookup
                                # (simplified - full implementation would query ComputeHost.ip_address/gateway)
                    logger.info(f"Loaded {len(crd_flows)} traffic flows from service database")
                except Exception as e:
                    logger.warning(f"Could not query service database for traffic flows: {e}")
            else:
                logger.warning("Service database not configured - skipping traffic flow queries")

            # vpn_name → vpn_id for runs_over edges
            vpn_name_to_id: Dict[str, str] = {
                n["name"]: n["id"]
                for n in snapshot_data["nodes"]
                if n["type"] == "vpn" and n.get("name") and n.get("id")
            }

            # Single scan over TrafficFlowSeries covering both flow discovery and KPI values.
            # Uses secondary index TrafficFlowSeriesByTimestamp for efficient time-range access.
            # AVG(value) over the 30s window — was MAX, but MAX spiked badly on UDP flows
            # where single-sample latency/jitter/packet-loss bursts produced wildly
            # oscillating feature values and false-positive anomaly scores.
            #
            # The GROUP BY must carry `role`. An earlier version of this comment
            # claimed "(flow_id, node_name, metric_name) already separates source
            # vs destination rows" — it does not. The collector derives node_name
            # from the flow_id, so both ends of a flow carry the SAME node_name;
            # role is the only thing that tells them apart.
            _FLOW_FIELD_NAME = {
                "traffic_agent_throughput_sent_bps": "throughput_sent_bps",
                "traffic_agent_throughput_recv_bps": "throughput_recv_bps",
                "traffic_agent_latency_ms":          "latency_ms",
                "traffic_agent_jitter_ms":           "jitter_ms",
                "traffic_agent_packet_loss_pct":     "packet_loss_pct",
            }
            # Which end of the flow actually measures each KPI. The traffic agent
            # exports all five from both ends, but only one end observes anything
            # real: a sender cannot see loss, jitter or one-way delay, and a
            # receiver cannot see what was offered. On the running lab the wrong
            # end reports 0 for both throughputs and a flat 50.0 for packet loss,
            # so accepting it silently poisons the anomaly features.
            _FLOW_METRIC_ROLE = {
                "traffic_agent_throughput_sent_bps": "source",
                "traffic_agent_throughput_recv_bps": "destination",
                "traffic_agent_latency_ms":          "destination",
                "traffic_agent_jitter_ms":           "destination",
                "traffic_agent_packet_loss_pct":     "destination",
            }
            _FLOW_KPI_METRICS_SET = [
                "traffic_agent_throughput_sent_bps",
                "traffic_agent_throughput_recv_bps",
                "traffic_agent_latency_ms",
                "traffic_agent_jitter_ms",
                "traffic_agent_packet_loss_pct",
            ]

            # flow_id_label → {base_name, is_reverse, src_iface, dst_iface, crd}
            discovered_flows: Dict[str, Dict] = {}
            # flow_id_label → {field_name: value}  (built in same pass)
            flow_kpi_pre: Dict[str, Dict[str, float]] = {}

            # Query TrafficFlowSeries from service database
            if self.service_database:
                query_flow_combined = """
                    SELECT flow_id, node_name, role, metric_name, AVG(value) AS avg_val
                    FROM TrafficFlowSeries@{FORCE_INDEX=TrafficFlowSeriesByTimestamp}
                    WHERE timestamp > @t_start AND timestamp <= @t_end
                      AND metric_name IN UNNEST(@metrics)
                    GROUP BY flow_id, node_name, role, metric_name
                """
                params_fl = {
                    "t_start": t_start, "t_end": ts_utc,
                    "metrics": _FLOW_KPI_METRICS_SET,
                }
                ptypes_fl = {
                    "t_start": spanner.param_types.TIMESTAMP,
                    "t_end": spanner.param_types.TIMESTAMP,
                    "metrics": spanner.param_types.Array(spanner.param_types.STRING),
                }

                try:
                    with self.service_database.snapshot() as sn_service:
                        for row in sn_service.execute_sql(query_flow_combined, params=params_fl, param_types=ptypes_fl):
                            flow_id_label, node_name, role, metric_name, agg_val = row
                            if not flow_id_label or not node_name:
                                continue

                            # Accumulate KPI values (now AVG over the 30s window — see comment above),
                            # taking each KPI only from the end of the flow that measures it.
                            field = _FLOW_FIELD_NAME.get(metric_name)
                            wanted_role = _FLOW_METRIC_ROLE.get(metric_name)
                            if (field and agg_val is not None
                                    and (wanted_role is None or role == wanted_role)):
                                flow_kpi_pre.setdefault(flow_id_label, {})[field] = float(agg_val)

                            # Flow discovery (first time we see this flow_id_label)
                            if flow_id_label in discovered_flows:
                                continue

                            if flow_id_label.endswith(f"_{node_name}_rev"):
                                base_name = flow_id_label[: -(len(node_name) + 5)]
                                is_reverse = True
                            elif flow_id_label.endswith(f"_{node_name}"):
                                base_name = flow_id_label[: -(len(node_name) + 1)]
                                is_reverse = False
                            else:
                                parts = flow_id_label.rsplit("_", 1)
                                base_name = parts[0] if len(parts) == 2 else flow_id_label
                                is_reverse = flow_id_label.endswith("_rev")

                            crd = crd_flows.get(base_name, {})
                            src_dev_id = crd.get("src_dev")
                            dst_dev_id = crd.get("dst_dev")
                            if is_reverse:
                                src_iface = device_to_interface.get(dst_dev_id or "")
                                dst_iface = device_to_interface.get(src_dev_id or "")
                            else:
                                src_iface = device_to_interface.get(src_dev_id or "")
                                dst_iface = device_to_interface.get(dst_dev_id or "")

                            discovered_flows[flow_id_label] = {
                                "base_name": base_name,
                                "is_reverse": is_reverse,
                                "protocol": "",
                                "src_iface": src_iface,
                                "dst_iface": dst_iface,
                                "crd": crd,
                            }
                except Exception as e:
                    logger.warning(f"Could not query TrafficFlowSeries from service database: {e}")
            else:
                logger.debug("Service database not configured - traffic flow metrics unavailable")

            # Fall back to CRD-only nodes for active flows absent from metrics this window
            for f_name, crd in crd_flows.items():
                src_dev_id = crd.get("src_dev")
                src_dev_name = device_id_to_name.get(src_dev_id or "")
                if src_dev_name:
                    fwd_key = f"{f_name}_{src_dev_name}"
                    if fwd_key not in discovered_flows:
                        discovered_flows[fwd_key] = {
                            "base_name": f_name,
                            "is_reverse": False,
                            "protocol": "",
                            "src_iface": device_to_interface.get(src_dev_id or ""),
                            "dst_iface": device_to_interface.get(crd.get("dst_dev") or ""),
                            "crd": crd,
                        }

            # Emit flow nodes and edges (originates_from / terminates_at / source_of / runs_over)
            runs_over_count = 0
            for flow_id_label, info in discovered_flows.items():
                node_id = f"flow:{flow_id_label}"
                crd = info["crd"]
                phase = crd.get("phase", "")
                is_running = 1.0 if phase and phase.lower() == "running" else 0.0
                is_bidir = 1.0 if str(crd.get("bidir", "")).lower() == "true" else 0.0
                snapshot_data["nodes"].append({
                    "id": node_id,
                    "type": "flow",
                    "name": info["base_name"],
                    "protocol": info["protocol"],
                    "is_reverse": info["is_reverse"],
                    "src_interface_id": info["src_iface"],
                    "dst_interface_id": info["dst_iface"],
                    "is_running": is_running,
                    "is_bidirectional": is_bidir,
                    "throughput_sent_bps": 0.0,
                    "throughput_recv_bps": 0.0,
                    "latency_ms": 0.0,
                    "jitter_ms": 0.0,
                    "packet_loss_pct": 0.0,
                })
                logger.debug(
                    f"[snapshot]   flow: id={node_id} base={info['base_name']} "
                    f"rev={info['is_reverse']} src_iface={info['src_iface']} dst_iface={info['dst_iface']}"
                )
                src_iface = info["src_iface"]
                dst_iface = info["dst_iface"]
                if src_iface:
                    snapshot_data["edges"].append({"source": node_id, "target": src_iface, "relation": "originates_from"})
                    snapshot_data["edges"].append({"source": src_iface, "target": node_id, "relation": "source_of"})
                if dst_iface:
                    snapshot_data["edges"].append({"source": node_id, "target": dst_iface, "relation": "terminates_at"})
                    snapshot_data["edges"].append({"source": dst_iface, "target": node_id, "relation": "destination_of"})
                vpn_label = crd.get("vpn_label")
                if vpn_label:
                    vpn_name = vpn_label.upper().replace("-", "_")
                    vpn_id = vpn_name_to_id.get(vpn_name)
                    if vpn_id:
                        snapshot_data["edges"].append({"source": node_id, "target": vpn_id, "relation": "runs_over"})
                        runs_over_count += 1

            logger.info(f"runs_over edges: {runs_over_count}")
            logger.info(f"Fetched {len(discovered_flows)} flow nodes ({len(crd_flows)} CRD records)")

            # ── 9. Prometheus metrics ────────────────────────────────────────
            # t_start already computed above
            logger.debug(f"[snapshot] Metrics window: {t_start.isoformat()} -> {timestamp.isoformat()}")

            PROMETHEUS_METRIC_MAP = {
                # interface
                "node_network_receive_drop_total":      ("interface", "rx_drops",       "rate"),
                "node_network_transmit_drop_total":     ("interface", "tx_drops",       "rate"),
                "node_network_mtu_bytes":               ("interface", "mtu_raw",        "gauge"),
                "node_network_receive_errs_total":      ("interface", "rx_errs_rate",   "rate"),
                "node_network_transmit_errs_total":     ("interface", "tx_errs_rate",   "rate"),
                "node_network_receive_bytes_total":     ("interface", "rx_bytes_rate",  "rate"),
                "node_network_transmit_bytes_total":    ("interface", "tx_bytes_rate",  "rate"),
                "node_network_receive_packets_total":   ("interface", "rx_packets_rate","rate"),
                "node_network_transmit_packets_total":  ("interface", "tx_packets_rate","rate"),
                "node_network_transmit_queue_length":   ("interface", "tx_queue_len",   "gauge"),
                "node_network_carrier_changes_total":   ("interface", "carrier_changes","rate"),
                "node_network_carrier":                 ("interface", "carrier_state",  "gauge"),
                "node_network_up":                      ("interface", "net_up",         "gauge"),
                # router
                "node_load1":                           ("router",    "cpu",            "gauge"),
                "node_memory_MemAvailable_bytes":       ("router",    "mem_bytes",      "gauge"),
                "node_memory_MemTotal_bytes":           ("router",    "mem_total_bytes","gauge"),
                "frr_route_total":                      ("router_sum", "routes_total",  "gauge"),
                "frr_route_total_fib":                  ("router_sum", "routes_fib",    "gauge"),
                "frr_bfd_peer_count":                   ("router",     "bfd_peers",     "gauge"),
                "frr_ospf_neighbors":                   ("router_sum", "ospf_neighbors","gauge"),
                "frr_ospf_neighbor_adjacencies":        ("router_sum", "ospf_adj",      "gauge"),
                # traffic agent metrics are read from TrafficFlowSeries, not NetworkMetrics
                # router-level sum across all peers
                "frr_bgp_peer_prefixes_advertised_count_total": ("bgp_or_router", "pfx_count", "gauge"),
            }

            # Split metrics by table (NetworkMetrics split into RouterSystemMetrics + RouterRoutingSeries)
            SYSTEM_METRICS = [
                "node_network_receive_drop_total",
                "node_network_transmit_drop_total",
                "node_network_mtu_bytes",
                "node_network_receive_errs_total",
                "node_network_transmit_errs_total",
                "node_network_receive_bytes_total",
                "node_network_transmit_bytes_total",
                "node_network_receive_packets_total",
                "node_network_transmit_packets_total",
                "node_network_transmit_queue_length",
                "node_network_carrier_changes_total",
                "node_network_carrier",
                "node_network_up",
                "node_load1",
                "node_memory_MemAvailable_bytes",
                "node_memory_MemTotal_bytes",
            ]
            ROUTING_METRICS = [
                "frr_route_total",
                "frr_route_total_fib",
                "frr_bfd_peer_count",
                "frr_ospf_neighbors",
                "frr_ospf_neighbor_adjacencies",
                "frr_bgp_peer_prefixes_advertised_count_total",
            ]

            # UNION query from both RouterSystemMetrics and RouterRoutingSeries
            query_metrics = """
                SELECT router_name AS node_name, interface, metric_name, value, labels
                FROM RouterSystemMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                  AND router_name IS NOT NULL
                  AND metric_name IN UNNEST(@system_metrics)
                UNION ALL
                SELECT router_name AS node_name, '' AS interface, metric_name, value, labels
                FROM RouterRoutingSeries
                WHERE timestamp > @t_start AND timestamp <= @t_end
                  AND router_name IS NOT NULL
                  AND metric_name IN UNNEST(@routing_metrics)
            """
            params_m = {
                "t_start": t_start,
                "t_end": timestamp,
                "system_metrics": SYSTEM_METRICS,
                "routing_metrics": ROUTING_METRICS,
            }
            ptypes_m = {
                "t_start": spanner.param_types.TIMESTAMP,
                "t_end": spanner.param_types.TIMESTAMP,
                "system_metrics": spanner.param_types.Array(spanner.param_types.STRING),
                "routing_metrics": spanner.param_types.Array(spanner.param_types.STRING),
            }

            # Accumulators: {target_id: {metric_key: [values]}}
            prom_agg: Dict[str, Dict[str, list]] = {}
            metrics_row_count = 0

            logger.debug("[snapshot:sql] RouterSystemMetrics + RouterRoutingSeries:\n%s", _render_spanner_query(query_metrics, params_m))
            for row in sn.execute_sql(query_metrics, params=params_m, param_types=ptypes_m):
                node_name, iface_name, metric_name, value, labels_json = row
                if not node_name or value is None:
                    logger.debug(f"[snapshot]   metrics row skipped: node_name={node_name!r} value={value!r}")
                    continue
                entry = PROMETHEUS_METRIC_MAP.get(metric_name)
                if not entry:
                    logger.debug(f"[snapshot]   metrics row skipped: unknown metric={metric_name!r}")
                    continue
                scope, field, _ = entry
                metrics_row_count += 1

                labels: Dict = {}
                try:
                    if labels_json:
                        labels = json.loads(labels_json) if isinstance(labels_json, str) else labels_json
                except Exception:
                    pass

                if scope == "interface":
                    if iface_name:
                        target_id = f"{node_name}:interface:{iface_name}"
                        prom_agg.setdefault(target_id, {}).setdefault(field, []).append(float(value))
                        logger.debug(f"[snapshot]   metric[interface] key={target_id} field={field} value={value}")
                elif scope == "router":
                    prom_agg.setdefault(node_name, {}).setdefault(field, []).append(float(value))
                    logger.debug(f"[snapshot]   metric[router] key={node_name} field={field} value={value}")
                elif scope == "router_sum":
                    # Always sum router-level metrics across all labels (e.g. sum of routes in all VRFs)
                    prom_agg.setdefault(node_name, {}).setdefault(field, []).append(float(value))
                    logger.debug(f"[snapshot]   metric[router_sum] key={node_name} field={field} value={value}")
                    # For frr_route_total: also accumulate per-VPN so total_prefixes is populated,
                    # AND per-VRF so router-level vrf_min_routes can be computed downstream.
                    if field == "routes_total":
                        vrf_label = labels.get("vrf", "")
                        if vrf_label:
                            mapped_vpn_id = vrf_name_to_vpn_id.get(vrf_label)
                            if mapped_vpn_id:
                                vpn_key = f"vpn:{mapped_vpn_id}"
                                prom_agg.setdefault(vpn_key, {}).setdefault("total_prefixes", []).append(float(value))
                                logger.debug(f"[snapshot]   metric[vpn] key={vpn_key} total_prefixes={value}")
                            # Per-VRF accumulator (sum across samples; VRF identified by (node_name, vrf_label))
                            vrf_key = f"vrf:{node_name}:{vrf_label}"
                            prom_agg.setdefault(vrf_key, {}).setdefault("routes_total", []).append(float(value))
                # flow metrics are sourced from TrafficFlowSeries, not NetworkMetrics
                elif scope == "bgp_or_router":
                    # Map to router-level sum AND to per-session if peer label available
                    prom_agg.setdefault(node_name, {}).setdefault("pfx_count_router", []).append(float(value))
                    # Try per-session: labels should have 'peer' key with the peer IP
                    peer_ip = labels.get("peer") or labels.get("neighbor")
                    if peer_ip:
                        session_key = f"bgp:{node_name}:{peer_ip}"
                        prom_agg.setdefault(session_key, {}).setdefault("pfx_count_session", []).append(float(value))
                        logger.debug(f"[snapshot]   metric[bgp_session] key={session_key} pfx_count_session={value}")
                    logger.debug(f"[snapshot]   metric[bgp_or_router] router_key={node_name} pfx_count_router={value}")

            logger.debug(f"[snapshot] Metrics rows processed: {metrics_row_count}; unique target keys: {len(prom_agg)}")

            # Average all accumulated samples by default, but SUM for specific scopes
            avg_metrics: Dict[str, Dict[str, float]] = {}
            for tid, mdict in prom_agg.items():
                avg_metrics[tid] = {}
                for field, vs in mdict.items():
                    if not vs: continue
                    # Find original scope to decide on aggregation
                    # Since we only have field name here, we'll check common sum fields
                    if field in ["routes_total", "routes_fib", "ospf_neighbors", "ospf_adj", "pfx_count_router"]:
                        avg_metrics[tid][field] = sum(vs)
                    else:
                        avg_metrics[tid][field] = sum(vs) / len(vs)
            logger.debug(f"[snapshot] avg_metrics keys: {sorted(avg_metrics.keys())}")

            # Build lookup: router_id → hostname (for resolving interface metric keys)
            # Prometheus uses node_name (hostname) and interface name
            router_id_to_hostname: Dict[str, str] = {
                n["id"]: n["hostname"] for n in snapshot_data["nodes"] if n["type"] == "router"
            }
            logger.debug(f"[snapshot] router_id_to_hostname: {router_id_to_hostname}")

            # Apply metrics to nodes
            for node in snapshot_data["nodes"]:
                ntype = node["type"]

                if ntype == "interface":
                    iid = node["id"]
                    # Resolve: interface_id → router_id → hostname
                    # Prometheus metric keys are stored as "{hostname}:interface:{iface_name}"
                    hostname = router_id_to_hostname.get(iface_ids.get(iid, ""), "")
                    iface_key = f"{hostname}:interface:{node['name']}" if hostname else iid

                    m = avg_metrics.get(iface_key, {})
                    if not m:
                        logger.debug(f"[snapshot]   interface {node['name']} (key={iface_key}): no metrics found")
                    else:
                        logger.debug(f"[snapshot]   interface {node['name']} (key={iface_key}): metrics={m}")
                    node["rx_drops"] = m.get("rx_drops", 0.0)
                    node["tx_drops"] = m.get("tx_drops", 0.0)
                    mtu_raw = m.get("mtu_raw", 0.0)
                    node["mtu_norm"] = mtu_raw / 9000.0 if mtu_raw > 0 else 0.0
                    node["rx_errs_rate"] = m.get("rx_errs_rate", 0.0)
                    node["tx_errs_rate"] = m.get("tx_errs_rate", 0.0)
                    node["rx_bytes_rate"] = m.get("rx_bytes_rate", 0.0)
                    node["tx_bytes_rate"] = m.get("tx_bytes_rate", 0.0)
                    node["rx_packets_rate"] = m.get("rx_packets_rate", 0.0)
                    node["tx_packets_rate"] = m.get("tx_packets_rate", 0.0)
                    node["tx_queue_len"] = m.get("tx_queue_len", 0.0)
                    node["carrier_changes"] = m.get("carrier_changes", 0.0)
                    node["carrier_state"] = m.get("carrier_state", 0.0)

                    # node_network_up overrides the CRD-derived state if present
                    if "net_up" in m:
                        node["state"] = float(m["net_up"] > 0)
                        logger.debug(f"[snapshot]   interface {node['name']}: net_up override -> state={node['state']}")

                    # Compute utilisation (bits per second / speed)
                    spd = node.get("speed_bps", 1e9)
                    if spd > 0:
                        node["tx_util"] = min(node["tx_bytes_rate"] * 8 / spd, 1.0)
                        node["rx_util"] = min(node["rx_bytes_rate"] * 8 / spd, 1.0)
                    logger.debug(
                        f"[snapshot]   interface {node['name']}: tx_util={node['tx_util']:.4f} "
                        f"rx_util={node['rx_util']:.4f} rx_errs_rate={node['rx_errs_rate']}"
                    )

                elif ntype == "router":
                    hostname = node.get("hostname", "")
                    m = avg_metrics.get(hostname, {})
                    if not m:
                        logger.debug(f"[snapshot]   router {hostname}: no metrics found")
                    else:
                        logger.debug(f"[snapshot]   router {hostname}: metrics={m}")
                    node["cpu"] = m.get("cpu", 0.0)
                    mem_bytes = m.get("mem_bytes", 0.0)
                    mem_total = m.get("mem_total_bytes", 4 * 1024 * 1024 * 1024)
                    node["mem"] = min(mem_bytes / mem_total, 1.0) if mem_total > 0 else 0.0
                    node["ospf_num_routes"] = m.get("routes_total", 0.0)
                    node["routes_fib"] = m.get("routes_fib", 0.0)
                    node["bfd_peers"] = m.get("bfd_peers", 0.0)
                    node["ospf_neighbors"] = m.get("ospf_neighbors", 0.0)
                    node["ospf_adj"] = m.get("ospf_adj", 0.0)
                    # Sum per-peer pfx_count_router values already summed via pfx_count_router
                    node["pfx_count_norm"] = m.get("pfx_count_router", 0.0)

                    # ── VRF-aggregation features (from router_vrfs collected in section 7) ──
                    # rt_import_signature — deterministic [0,1] hash of the sorted set of
                    #   rt_import RT strings across this router's VRFs. Picks up RT misconfig.
                    # vrf_min_routes     — min route count across this router's VRFs. Drops
                    #   when any single VRF loses routes (downstream RT-misconfig signal).
                    # vrf_count          — number of VRFs this router hosts.
                    vrfs_here = router_vrfs.get(node["id"], [])
                    if vrfs_here:
                        rt_strings = sorted(
                            ",".join(sorted(v.get("rt_import", []))) for v in vrfs_here
                        )
                        sig_input = "|".join(rt_strings)
                        # hashlib for determinism — Python's hash() is PYTHONHASHSEED-randomised
                        hash_int = int(hashlib.md5(sig_input.encode("utf-8")).hexdigest()[:8], 16)
                        node["rt_import_signature"] = hash_int / 0xFFFFFFFF

                        vrf_route_counts: List[float] = []
                        for v in vrfs_here:
                            vrf_key = f"vrf:{hostname}:{v['vrf_name']}"
                            vmet = avg_metrics.get(vrf_key, {})
                            vrf_route_counts.append(float(vmet.get("routes_total", 0.0)))
                        node["vrf_min_routes"] = min(vrf_route_counts) if vrf_route_counts else 0.0
                        node["vrf_count"] = float(len(vrfs_here))
                    else:
                        node["rt_import_signature"] = 0.0
                        node["vrf_min_routes"] = 0.0
                        node["vrf_count"] = 0.0

                    vn_pairs = router_to_vrfs.get(node["id"], [])
                    if vn_pairs:
                        node["vrf_rt_import_mismatch"] = max(
                            vrf_mismatch.get((node["id"], vpn_id, vrf_name), 0.0)
                            for vpn_id, vrf_name in vn_pairs
                        )
                    else:
                        node["vrf_rt_import_mismatch"] = 0.0

                    logger.debug(
                        f"[snapshot]   router {hostname}: cpu={node['cpu']} mem={node['mem']:.4f} "
                        f"ospf_routes={node['ospf_num_routes']} pfx_count_norm={node['pfx_count_norm']} "
                        f"vrf_count={node['vrf_count']} vrf_min_routes={node['vrf_min_routes']} "
                        f"rt_sig={node['rt_import_signature']:.4f} rt_mismatch={node['vrf_rt_import_mismatch']:.4f}"
                    )

                # flow metrics populated below from TrafficFlowSeries query

                elif ntype == "vpn":
                    vpn_id = node["id"]
                    m = avg_metrics.get(f"vpn:{vpn_id}", {})
                    if m.get("total_prefixes"):
                        node["total_prefixes"] = m["total_prefixes"]
                    logger.debug(f"[snapshot]   vpn {node['name']}: total_prefixes={node['total_prefixes']}")

            # ── 10. Apply flow KPI features (collected in combined query above) ─
            flow_nodes_updated = 0
            for node in snapshot_data["nodes"]:
                if node["type"] != "flow":
                    continue
                flow_id_label = node["id"].removeprefix("flow:")
                kpis = flow_kpi_pre.get(flow_id_label, {})
                node["throughput_sent_bps"] = kpis.get("throughput_sent_bps", 0.0)
                node["throughput_recv_bps"] = kpis.get("throughput_recv_bps", 0.0)
                node["latency_ms"]          = kpis.get("latency_ms",          0.0)
                node["jitter_ms"]           = kpis.get("jitter_ms",           0.0)
                node["packet_loss_pct"]     = kpis.get("packet_loss_pct",     0.0)
                if kpis:
                    flow_nodes_updated += 1
                logger.debug(f"[snapshot]   flow {flow_id_label}: kpis={kpis}")
            logger.info(f"Flow KPI metrics applied to {flow_nodes_updated}/{len(discovered_flows)} flow nodes")

        logger.info(
            f"Snapshot {timestamp.isoformat()}: "
            f"{len(snapshot_data['nodes'])} nodes, {len(snapshot_data['edges'])} edges"
        )
        return snapshot_data

    @staticmethod
    def compute_temporal_features(
        snapshots: List[Dict],
        interval_seconds: float = 300.0,
    ) -> List[Dict]:
        """Compute gradient / delta features across consecutive snapshots in-place.

        Modifies each snapshot dict to populate:
          - interface nodes: rx_err_gradient  (rate of change of rx error rate)

        The first snapshot in the list receives 0.0 for all gradient features.

        Args:
            snapshots:        Ordered list of snapshot dicts (oldest first).
            interval_seconds: Seconds between consecutive snapshots (default 300 = 5 min).

        Returns:
            The same list (mutated in-place) for convenience.
        """
        if not snapshots:
            return snapshots

        # Build lookup: snapshot_index → {node_id: node_dict}
        def _index(snap):
            return {n["id"]: n for n in snap["nodes"]}

        prev_idx = _index(snapshots[0])

        for snap_i, snap in enumerate(snapshots):
            snap_ts = datetime.datetime.fromisoformat(snap["timestamp"])
            curr_idx = _index(snap)

            for node in snap["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                prev = prev_idx.get(nid)

                if ntype == "interface":
                    if prev is not None and snap_i > 0:
                        delta_err = node.get("rx_errs_rate", 0.0) - prev.get("rx_errs_rate", 0.0)
                        node["rx_err_gradient"] = delta_err / interval_seconds if interval_seconds > 0 else 0.0
                    else:
                        node["rx_err_gradient"] = 0.0

            prev_idx = curr_idx

        return snapshots


if __name__ == "__main__":
    import sys
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    _local_creds = str((Path(__file__).resolve().parent.parent / "networkagent.json").resolve())
    logger.info(_local_creds)
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", _local_creds)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    print(f"Using service-account credentials: {creds_path}")

    INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
    DATABASE_ID = os.getenv("SPANNER_DATABASE", "transporttopology-db")
    SERVICE_DATABASE_ID = os.getenv("SERVICE_DATABASE", "servicetopology-db")
    INFRA_DATABASE_ID = os.getenv("INFRA_DATABASE", "infratopology-db")
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agents-1234")
    NUM_SNAPSHOTS = 1
    INTERVAL_MINUTES = 5

    FROM_TIME = datetime.datetime(2026, 4, 14, 18, 0, 0)
    TO_TIME = datetime.datetime(2026, 4, 14, 21, 0, 0)

    dataset = SpannerDataset(
        instance_id=INSTANCE_ID,
        database_id=DATABASE_ID,
        service_database_id=SERVICE_DATABASE_ID,
        infra_database_id=INFRA_DATABASE_ID,
        num_snapshots=NUM_SNAPSHOTS,
        interval_minutes=INTERVAL_MINUTES,
        project_id=PROJECT_ID,
        # from_time=FROM_TIME,
        # to_time=TO_TIME,
    )

    timestamps = dataset._get_timestamps()
    snapshots = []
    for i, ts in enumerate(timestamps):
        try:
            snap = dataset.fetch_snapshot(ts)
            snapshots.append(snap)
            node_types: Dict[str, int] = {}
            for n in snap["nodes"]:
                node_types[n["type"]] = node_types.get(n["type"], 0) + 1
            edge_types: Dict[str, int] = {}
            for e in snap["edges"]:
                edge_types[e["relation"]] = edge_types.get(e["relation"], 0) + 1
            print(f"[{i+1:02d}] {ts.isoformat()}")
            print(f"     Nodes: " + "  ".join(f"{k}={v}" for k, v in sorted(node_types.items())))
            print(f"     Edges: " + "  ".join(f"{k}={v}" for k, v in sorted(edge_types.items())))
        except Exception as exc:
            print(f"[{i+1:02d}] ERROR: {exc}")

    SpannerDataset.compute_temporal_features(snapshots, interval_seconds=INTERVAL_MINUTES * 60)
    print(f"\nFetched {len(snapshots)}/{NUM_SNAPSHOTS} snapshots with temporal features applied.")
