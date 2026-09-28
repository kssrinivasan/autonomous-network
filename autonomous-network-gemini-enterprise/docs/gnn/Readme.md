# Graph Neural Networks

Graph Neural Networks are trained on the [temporal network state data in Spanner](/docs/spanner/Readme.md) to identify the root cause of failures and predict the future state of the network. 

Use case details

* [Failure Pinpointing](/docs/gnn/rca.md)

---

## Node Definitions from Spanner

Each snapshot is a heterogeneous graph with four node types. All topology tables use **SCD Type 2** — every row has `valid_start_ts` and `valid_end_ts`, and a snapshot at time T filters with:

```sql
valid_start_ts <= @T AND (valid_end_ts > @T OR valid_end_ts IS NULL)
```

Live telemetry comes from a flat `NetworkMetrics` table where Prometheus scrapes from `node_exporter` and `frr_exporter` land every ~20 seconds. The fetch window for a snapshot is `(T − interval_minutes, T]`; values are averaged over that window (or summed for additive metrics like route counts).

---

### Router nodes — `PhysicalRouter`

**Written by:** the `VyOSRouter` Kubernetes operator (`sync_physical_router()`) on every lifecycle phase transition and every 60-second monitor tick when state changes.

| GNN field | Spanner / metric source | Notes |
|---|---|---|
| `id` | `PhysicalRouter.id` | Router ID (not prefixed with `"router:"` in Spanner) |
| `hostname` | `PhysicalRouter.name` | |
| `role` | `PhysicalRouter.role` | `"CE"`, `"PE"`, `"P"`, `"RR"` — one-hot encoded into 4 binary features (`role_P`, `role_PE`, `role_RR`, `role_CE`) by the GNN |
| `state` | `PhysicalRouter.status` | `1.0` if `"running"`, else `0.0` |
| `cpu` | `NetworkMetrics` — `node_load1` | 1-minute load average from `node_exporter`; averaged over the interval window |
| `mem` | `NetworkMetrics` — `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes` | Normalised: available memory / total memory (higher = more free RAM) |
| `ospf_num_routes` | `NetworkMetrics` — `frr_route_total` (summed across all VRFs) | Scraped from `frr_exporter`; total routes in FRR table |
| `routes_fib` | `NetworkMetrics` — `frr_route_total_fib` | Routes actually installed in the kernel FIB |
| `bfd_peers` | `NetworkMetrics` — `frr_bfd_peer_count` | BFD sessions active on this router |
| `ospf_neighbors` | `NetworkMetrics` — `frr_ospf_neighbors` | Total OSPF neighbours seen |
| `ospf_adj` | `NetworkMetrics` — `frr_ospf_neighbor_adjacencies` | OSPF full adjacencies (reached FULL state) |
| `pfx_count_norm` | `NetworkMetrics` — `frr_bgp_peer_prefixes_advertised_count_total` | Sum of prefixes advertised to all BGP peers across all sessions |
| `rt_import_signature` | Derived from `VRF.config` (JSON) | Deterministic MD5 hash [0,1] of sorted rt_import RT strings across all VRFs on this router; detects RT misconfiguration |
| `vrf_min_routes` | Derived from `frr_route_total` per VRF | Minimum route count across all VRFs on this router; drops when any VRF loses routes |
| `vrf_count` | Derived from `VRF` table | Number of VRFs hosted by this router |

**Metric lookup key:** `node_name = hostname` (e.g. `ce1-hub`) in `NetworkMetrics`.

**Current feature count:** 16 total features (13 continuous + 4 one-hot role features, but one-hot encoded as 3 actual features since role is mutually exclusive)

---

### Interface nodes — `PhysicalInterface`

**Written by:** the `VyOSRouter` operator — one row per `spec.interfaces[]` entry in the `VyOSRouter` CRD.

| GNN field | Spanner / metric source | Notes |
|---|---|---|
| `id` | `PhysicalInterface.id` | Format: `"router:<name>:interface:<if-name>"`, e.g. `router:pe1:interface:eth0` |
| `device_id` | `PhysicalInterface.router_id` | Parent router ID |
| `speed_bps` | `Interface_Link → PhysicalLink.bandwidth` | Falls back to `spec.interfaces[*].speed`, default `1G`; parsed from strings like `"1G"`, `"100M"` |
| `state` | `PhysicalInterface.status` → overridden by `node_network_up` metric if present | `1.0` if `"up"` |
| `rx_drops` | `NetworkMetrics` — `node_network_receive_drop_total` (rate) | |
| `tx_drops` | `NetworkMetrics` — `node_network_transmit_drop_total` (rate) | |
| `mtu_norm` | `NetworkMetrics` — `node_network_mtu_bytes ÷ 9000` | Normalised to jumbo-frame maximum |
| `rx_errs_rate` | `NetworkMetrics` — `node_network_receive_errs_total` (rate) | CRC / input errors per second |
| `tx_errs_rate` | `NetworkMetrics` — `node_network_transmit_errs_total` (rate) | |
| `rx_bytes_rate` | `NetworkMetrics` — `node_network_receive_bytes_total` (rate) | |
| `tx_bytes_rate` | `NetworkMetrics` — `node_network_transmit_bytes_total` (rate) | |
| `rx_packets_rate` | `NetworkMetrics` — `node_network_receive_packets_total` (rate) | |
| `tx_packets_rate` | `NetworkMetrics` — `node_network_transmit_packets_total` (rate) | |
| `tx_queue_len` | `NetworkMetrics` — `node_network_transmit_queue_length` (gauge) | TX queue depth |
| `carrier_changes` | `NetworkMetrics` — `node_network_carrier_changes_total` (rate) | Link flap counter |
| `carrier_state` | `NetworkMetrics` — `node_network_carrier` (gauge) | Physical link signal present |
| `rx_util` | Derived: `rx_bytes_rate × 8 / speed_bps`, capped at `1.0` | |
| `tx_util` | Derived: `tx_bytes_rate × 8 / speed_bps`, capped at `1.0` | |
| `rx_err_gradient` | **Temporal, computed in Python** — `(rx_errs_rate[t] − rx_errs_rate[t−1]) / interval_seconds` | Rate of change of the error rate across consecutive snapshots; zero on the first snapshot. Computed by `SpannerDataset.compute_temporal_features()` after all snapshots are fetched |

**Metric lookup key:** `"{hostname}:interface:{iface_name}"` — constructed by resolving `interface_id → router_id → hostname`.

---

### VPN nodes — `L3VPNService`

**Written by:** the `VyOSL3VPN` operator (`sync_l3vpn_service()`) — one row per service entry in `spec.services[]`.

| GNN field | Spanner / metric source | Notes |
|---|---|---|
| `id` | `L3VPNService.id` | VPN service ID |
| `name` | `L3VPNService.name` | |
| `state` | `L3VPNService.status` | `1.0` if `"active"` or `"running"`, else `0.0` |
| `num_active_peers` | Derived from `BGPSession` rows | Count of BGP sessions with `status = "Established"` whose `VRF.vpn_id` maps to this VPN |
| `total_prefixes` | `NetworkMetrics` — `frr_route_total` per VRF, summed | Matched by joining `VRF.name` against the `vrf` label in the metric; aggregated across all VRFs belonging to this VPN |

**Current feature count:** 3 features

**VPN membership edges** (`member_of` / `has_member`) are derived from `VRF` rows: `SELECT DISTINCT router_id, vpn_id FROM VRF WHERE vpn_id IS NOT NULL AND <valid at T>`.

---

### Flow nodes — `TrafficFlow`

**Written by:** the `TrafficTest` Kubernetes CRD operator on every phase change (pending → deploying → running → completed / failed / stopped). Uses the same SCD Type 2 pattern.

| GNN field | Spanner / metric source | Notes |
|---|---|---|
| `id` | `TrafficFlow.id` | Format: `"flow:<flow_id_label>"` where flow_id_label includes device name suffix |
| `name` | `TrafficFlow.name` | Base flow name |
| `is_running` | `TrafficFlow.phase` | `1.0` if phase is `"running"`, else `0.0` |
| `is_bidirectional` | `TrafficFlow.config.spec.bidirectional` | `1.0` if `"true"`, else `0.0` |
| `throughput_sent_bps` | `TrafficFlowSeries` — `traffic_agent_throughput_sent_bps` | `role='source'`; averaged over interval window |
| `throughput_recv_bps` | `TrafficFlowSeries` — `traffic_agent_throughput_recv_bps` | `role='destination'`; averaged over interval window |
| `latency_ms` | `TrafficFlowSeries` — `traffic_agent_latency_ms` | UDP only; `role='destination'`; averaged over interval window |
| `jitter_ms` | `TrafficFlowSeries` — `traffic_agent_jitter_ms` | UDP only; `role='destination'`; averaged over interval window |
| `packet_loss_pct` | `TrafficFlowSeries` — `traffic_agent_packet_loss_pct` | UDP only; `role='destination'` — the sender reports a fabricated ~50%; averaged over interval window |

**Current feature count:** 7 features

**Flow edges:**
- `originates_from` / `source_of` — `TrafficFlow.src_device_id → Device.interface_id → PhysicalInterface` (with fallback to `Device.gateway → PhysicalInterface.ip_address`)
- `terminates_at` / `destination_of` — same lookup on `dst_device_id`
- `runs_over` — `TrafficFlow.config.metadata.labels.vpn` uppercased and hyphens replaced with underscores, matched against `L3VPNService.name`

**Metric lookup:** the traffic-agent emits a `flow_id` label of the form `"<flow-name>_<device-name>"` or `"<flow-name>_<device-name>_rev"` for reverse flows. Metrics are read from the `TrafficFlowSeries` table using the `TrafficFlowSeriesByTimestamp` secondary index for efficient time-range queries. Values are averaged (AVG) over the interval window to reduce sensitivity to single-sample spikes. Both ends of a leg report under the same `flow_id` and `node_name`, so the query groups by `role` as well and takes each KPI from the end that measures it.

---

### Graph edges summary

| Edge relation | Source type | Target type | Derived from |
|---|---|---|---|
| `has_interface` / `interface_of` | router ↔ interface | | `PhysicalInterface.router_id` |
| `connected_to` | interface ↔ interface | | `Interface_Link` join on shared `link_id` |
| `peer_of` | router ↔ router | | OSPF peers via `PhysicalLink` + iBGP peers via `BGPSession.peer_ip → PhysicalInterface.ip_address` |
| `member_of` / `has_member` | router ↔ vpn | | `VRF.router_id` + `VRF.vpn_id` |
| `originates_from` / `source_of` | flow ↔ router | | `TrafficFlow.src_device_id → Device → PhysicalInterface.router_id` |
| `terminates_at` | flow → router | | `TrafficFlow.dst_device_id → Device → PhysicalInterface.router_id` |
| `runs_over` | flow → vpn | | `TrafficFlow.config.metadata.labels.vpn → L3VPNService.name` |