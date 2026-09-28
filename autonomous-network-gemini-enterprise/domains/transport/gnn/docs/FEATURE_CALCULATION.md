# GNN Feature Calculation Guide

This document explains how all node and edge features are calculated in the GNN pipeline, from Spanner to the final PyTorch tensors.

## Data Flow Overview

```
Spanner DB → SpannerDataset.fetch_snapshot() → snapshot dict → GraphBuilder.process_snapshot() → HeteroData (PyTorch)
```

## Node Features

### 1. Router Features (13 features)

**Source**: [gnn/src/utils/data.py:283-305](../src/utils/data.py#L283-L305)

| Feature | Type | Source | Calculation |
|---------|------|--------|-------------|
| `state` | Binary | `PhysicalRouter.status` | `1.0` if status='running', else `0.0` |
| `cpu` | Continuous | `NetworkMetrics.node_load1` | Average load1 metric over interval (gauge) |
| `mem` | Continuous | `NetworkMetrics.node_memory_*` | `MemAvailable / MemTotal` (normalized to [0,1]) |
| `ospf_num_routes` | Count | `NetworkMetrics.frr_route_total` | Sum of all route counts across VRFs (gauge) |
| `pfx_count_norm` | Count | `NetworkMetrics.frr_bgp_peer_prefixes_advertised_count_total` | Sum across all BGP peers |
| `routes_fib` | Count | `NetworkMetrics.frr_route_total_fib` | Sum of FIB route counts (gauge) |
| `bfd_peers` | Count | `NetworkMetrics.frr_bfd_peer_count` | Number of BFD peers (gauge) |
| `ospf_neighbors` | Count | `NetworkMetrics.frr_ospf_neighbors` | Sum across all interfaces (gauge) |
| `ospf_adj` | Count | `NetworkMetrics.frr_ospf_neighbor_adjacencies` | Sum of adjacency counts (gauge) |
| `role_P` | One-hot | `PhysicalRouter.role` | `1.0` if role='P', else `0.0` |
| `role_PE` | One-hot | `PhysicalRouter.role` | `1.0` if role='PE', else `0.0` |
| `role_RR` | One-hot | `PhysicalRouter.role` | `1.0` if role='RR', else `0.0` |
| `role_CE` | One-hot | `PhysicalRouter.role` | `1.0` if role='CE', else `0.0` |

**Query Logic**:
```sql
SELECT id, name, role, status
FROM PhysicalRouter
WHERE valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)
```

**Metrics Aggregation**:
- Metrics are queried from `NetworkMetrics` table over the interval `[t-interval_minutes, t]`
- Router-level metrics use `node_name` to match router hostname
- Metrics are averaged unless marked as "sum" scope (routes, neighbors, adjacencies)
- See [data.py:759-786](../src/utils/data.py#L759-L786) for the `PROMETHEUS_METRIC_MAP`

---

### 2. Interface Features (16 features)

**Source**: [gnn/src/utils/data.py:310-343](../src/utils/data.py#L310-L343)

| Feature | Type | Source | Calculation |
|---------|------|--------|-------------|
| `state` | Binary | `PhysicalInterface.status` → overridden by `node_network_up` | `1.0` if status='up', else `0.0` (metric overrides CRD) |
| `rx_drops` | Rate | `NetworkMetrics.node_network_receive_drop_total` | Rate (counter derivative) over interval |
| `tx_drops` | Rate | `NetworkMetrics.node_network_transmit_drop_total` | Rate (counter derivative) |
| `mtu_norm` | Normalized | `NetworkMetrics.node_network_mtu_bytes` | `mtu / 9000.0` (normalized to jumbo frame size) |
| `rx_errs_rate` | Rate | `NetworkMetrics.node_network_receive_errs_total` | Rate (counter derivative) |
| `tx_errs_rate` | Rate | `NetworkMetrics.node_network_transmit_errs_total` | Rate (counter derivative) |
| `rx_bytes_rate` | Rate | `NetworkMetrics.node_network_receive_bytes_total` | Rate (counter derivative) in bytes/sec |
| `tx_bytes_rate` | Rate | `NetworkMetrics.node_network_transmit_bytes_total` | Rate (counter derivative) in bytes/sec |
| `rx_packets_rate` | Rate | `NetworkMetrics.node_network_receive_packets_total` | Rate (counter derivative) |
| `tx_packets_rate` | Rate | `NetworkMetrics.node_network_transmit_packets_total` | Rate (counter derivative) |
| `tx_queue_len` | Gauge | `NetworkMetrics.node_network_transmit_queue_length` | Gauge (average over interval) |
| `carrier_changes` | Rate | `NetworkMetrics.node_network_carrier_changes_total` | Rate (counter derivative) |
| `carrier_state` | Gauge | `NetworkMetrics.node_network_carrier` | Gauge (average) |
| `rx_err_gradient` | **Temporal** | Computed from previous snapshot | `Δ(rx_errs_rate) / interval_seconds` |
| `tx_util` | Derived | `tx_bytes_rate` and `PhysicalInterface.speed` | `min(tx_bytes_rate * 8 / speed_bps, 1.0)` |
| `rx_util` | Derived | `rx_bytes_rate` and `PhysicalInterface.speed` | `min(rx_bytes_rate * 8 / speed_bps, 1.0)` |

**Query Logic**:
```sql
SELECT id, router_id, name, speed, status
FROM PhysicalInterface
WHERE valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)
```

**Metrics Key Resolution**:
- Interface metrics use composite key: `"{hostname}:interface:{iface_name}"`
- Hostname is resolved via `interface.router_id → PhysicalRouter.name`
- See [data.py:880-929](../src/utils/data.py#L880-L929)

**Temporal Features**:
- `rx_err_gradient` is computed in [data.py:1023-1028](../src/utils/data.py#L1023-L1028)
- Requires previous snapshot to compute gradient
- First snapshot gets `0.0` for gradient features

**Speed Parsing**:
- `PhysicalInterface.speed` is parsed via `_parse_speed_bps()` [data.py:38-61](../src/utils/data.py#L38-L61)
- Supports formats: "1G", "10G", "1000M", "100M", "1Gbit", "1Gbps"
- Fallback: 1 Gbps if unparseable

---

### 3. VPN Features (3 features)

**Source**: [gnn/src/utils/data.py:472-504](../src/utils/data.py#L472-L504)

| Feature | Type | Source | Calculation |
|---------|------|--------|-------------|
| `state` | Binary | `L3VPNService.status` | `1.0` if status in ('active','running'), else `0.0` |
| `num_active_peers` | Count | `BGPSession` records | Count of sessions where `status='Established'` for this VPN |
| `total_prefixes` | Count | `NetworkMetrics.frr_route_total` | Sum of route counts where VRF label matches VPN |

**Query Logic**:
```sql
SELECT vpn.id, vpn.name, vpn.status, vpn.topology, vpn.valid_start_ts
FROM L3VPNService vpn
WHERE vpn.valid_start_ts <= @ts AND (vpn.valid_end_ts > @ts OR vpn.valid_end_ts IS NULL)
```

**Active Peers Calculation** [data.py:451-469](../src/utils/data.py#L451-L469):
```sql
SELECT bs.vrf_id, bs.status, v.vpn_id, v.name
FROM BGPSession bs
JOIN VRF v ON v.id = bs.vrf_id AND v.valid_start_ts <= @ts AND ...
WHERE bs.valid_start_ts <= @ts AND ... AND v.vpn_id IS NOT NULL
```
- For each VPN, count BGP sessions where `status='Established'`
- Maps VRF → VPN via `VRF.vpn_id`

**Total Prefixes** [data.py:843-850](../src/utils/data.py#L843-L850):
- From `frr_route_total` metrics with VRF label
- VRF name is matched to VPN via `vrf_name_to_vpn_id` mapping
- Summed per VPN

---

### 4. Flow Features (7 features)

**Source**: [gnn/src/utils/data.py:571-753](../src/utils/data.py#L571-L753)

| Feature | Type | Source | Calculation |
|---------|------|--------|-------------|
| `throughput_sent_bps` | Rate | `TrafficFlowSeries.traffic_agent_throughput_sent_bps` | `AVG(value)` over interval, `role='source'` |
| `throughput_recv_bps` | Rate | `TrafficFlowSeries.traffic_agent_throughput_recv_bps` | `AVG(value)` over interval, `role='destination'` |
| `latency_ms` | Gauge | `TrafficFlowSeries.traffic_agent_latency_ms` | `AVG(value)` over interval, `role='destination'` |
| `jitter_ms` | Gauge | `TrafficFlowSeries.traffic_agent_jitter_ms` | `AVG(value)` over interval, `role='destination'` |
| `packet_loss_pct` | Gauge | `TrafficFlowSeries.traffic_agent_packet_loss_pct` | `AVG(value)` over interval, `role='destination'` (UDP only) |

Both ends of a leg report every metric under the same `flow_id` and the same
`node_name` — the collector derives `node_name` from `flow_id`, so `role` is the
only thing that separates them. Each KPI is therefore taken only from the end
that actually measures it: a sender observes no loss, delay or jitter, and
reporting its readings would show 0 ms latency and a fabricated ~50% loss.
| `is_running` | Binary | `TrafficFlow.phase` | `1.0` if phase='Running', else `0.0` |
| `is_bidirectional` | Binary | `TrafficFlow.config` JSON | `1.0` if `spec.bidirectional=true`, else `0.0` |

**Query Logic for Metadata**:
```sql
SELECT tf.id, tf.name, tf.src_device_id, tf.dst_device_id, tf.phase,
       JSON_VALUE(tf.config, '$.spec.bidirectional'),
       JSON_VALUE(tf.config, '$.metadata.labels.vpn')
FROM TrafficFlow tf
WHERE tf.valid_start_ts <= @ts AND (tf.valid_end_ts > @ts OR tf.valid_end_ts IS NULL)
```

**Query Logic for Metrics** [spanner.py](../src/gnn/data/spanner.py):
```sql
SELECT flow_id, node_name, role, metric_name, AVG(value) AS avg_val
FROM TrafficFlowSeries@{FORCE_INDEX=TrafficFlowSeriesByTimestamp}
WHERE timestamp > @t_start AND timestamp <= @t_end
  AND metric_name IN UNNEST(@metrics)
GROUP BY flow_id, node_name, role, metric_name
```

**Flow ID Resolution**:
- Flow IDs from metrics have format: `{base_name}_{node_name}[_rev]`
- `_rev` suffix indicates reverse (bidirectional) flow
- Base name maps to TrafficFlow CRD via `TrafficFlow.name`

**Device → Interface Resolution** [data.py:528-564](../src/utils/data.py#L528-L564):
- Primary: `Device.interface_id → PhysicalInterface.id`
- Fallback: `Device.gateway → PhysicalInterface.ip_address`
- Used to create flow→interface edges

---

## Edge Calculation

Edges are extracted in [data.py:346-752](../src/utils/data.py#L346-L752) and converted to PyTorch edge indices in [gnn_utils.py:208-230](../src/utils/gnn_utils.py#L208-L230).

### Physical Layer Edges

**1. has_interface / interface_of** (router ↔ interface)
- **Source**: [data.py:346-362](../src/utils/data.py#L346-L362)
- Created for each interface using `PhysicalInterface.router_id`
- Bidirectional (has_interface + interface_of)

**2. connected_to** (interface ↔ interface)
- **Source**: [data.py:366-384](../src/utils/data.py#L366-L384)
- Query:
  ```sql
  SELECT il1.interface_id, il2.interface_id
  FROM Interface_Link il1
  JOIN Interface_Link il2 ON il1.link_id = il2.link_id
  WHERE il1.interface_id < il2.interface_id AND ...SCD temporal filters
  ```
- Symmetric (both directions added)
- Uses `Interface_Link` junction table to resolve many-to-many

---

### Control Plane Edges

**3. peer_of** (router ↔ router)
- **Source**: [data.py:387-442](../src/utils/data.py#L387-L442)
- Two sources combined via set union:
  
  **a) OSPF peers** — routers sharing a physical link:
  ```sql
  SELECT DISTINCT r1.id, r2.id
  FROM PhysicalLink pl
  JOIN Interface_Link il1, Interface_Link il2 ON il1.link_id = pl.id AND il2.link_id = pl.id
  JOIN PhysicalInterface pi1, pi2 ON pi1.id = il1.interface_id AND pi2.id = il2.interface_id
  JOIN PhysicalRouter r1, r2 ON r1.id = pi1.router_id AND r2.id = pi2.router_id
  WHERE ... SCD temporal filters AND r1.id != r2.id
  ```
  
  **b) iBGP peers** — via BGP session peer_ip resolution:
  ```sql
  SELECT DISTINCT v.router_id AS router_a, pi.router_id AS router_b
  FROM BGPSession bs
  JOIN VRF v ON v.id = bs.vrf_id
  JOIN PhysicalInterface pi ON pi.ip_address = bs.peer_ip
  WHERE ... SCD temporal filters AND v.router_id != pi.router_id
  ```
  
- Symmetric (both directions added)
- Deduplicates across OSPF and BGP sources

---

### Service Layer Edges

**4. member_of / has_member** (router ↔ vpn)
- **Source**: [data.py:507-521](../src/utils/data.py#L507-L521)
- Query:
  ```sql
  SELECT DISTINCT v.router_id, v.vpn_id
  FROM VRF v
  WHERE v.vpn_id IS NOT NULL AND ... SCD temporal filters
  ```
- Bidirectional (member_of + has_member)
- VRF is the join entity: router hosts VRF, VRF realizes VPN

---

### Traffic Edges

**5. originates_from / source_of** (flow → interface)
- **Source**: [data.py:738-740](../src/utils/data.py#L738-L740)
- Flow's `src_interface_id` is resolved via Device→Interface lookup
- Bidirectional

**6. terminates_at / destination_of** (flow → interface)
- **Source**: [data.py:741-743](../src/utils/data.py#L741-L743)
- Flow's `dst_interface_id` is resolved via Device→Interface lookup
- Bidirectional

**7. runs_over** (flow → vpn)
- **Source**: [data.py:744-750](../src/utils/data.py#L744-L750)
- Derived from `TrafficFlow.config` JSON: `metadata.labels.vpn`
- VPN label format: `"blue-spoke"` → normalized to `"BLUE_SPOKE"`
- Matched against VPN names to get VPN ID
- Unidirectional (flow → vpn only)

---

## Feature Scaling

**Location**: [gnn_utils.py:237-293](../src/utils/gnn_utils.py#L237-L293)

### fit_scalers(snapshots)
- Fits one `StandardScaler` per node type across all snapshots
- **Router**: Only first 9 continuous features are scaled (indices 0-8); one-hot role columns (9-12) are left unchanged
- **Interface, VPN, Flow**: All features are scaled

### GraphBuilder.process_snapshot()
- Applies fitted scalers during feature extraction [gnn_utils.py:196-203](../src/utils/gnn_utils.py#L196-L203)
- Scaling is done per node type on the NumPy array before converting to PyTorch tensor
- If no scaler is provided, raw features are used

---

## Temporal Features

**Location**: [data.py:986-1032](../src/utils/data.py#L986-L1032)

### compute_temporal_features(snapshots)
Called after all snapshots are fetched to compute features that require historical context.

**Current temporal features**:
- **`rx_err_gradient`** (interface): Rate of change of `rx_errs_rate`
  ```python
  delta_err = current.rx_errs_rate - previous.rx_errs_rate
  rx_err_gradient = delta_err / interval_seconds
  ```
- First snapshot receives `0.0` for all gradient features

---

## Missing / Null Values

**Handling**: [gnn_utils.py:166-167](../src/utils/gnn_utils.py#L166-L167)
```python
def _fv(v):
    return float(v) if v is not None else 0.0
```
- All `None` values are converted to `0.0`
- No special encoding for missing data (could be improved with mask tensors)

---

## Summary Tables

### Spanner Table → Node Type Mapping

| Spanner Table | Node Type | Primary Query Column |
|---------------|-----------|---------------------|
| `PhysicalRouter` | router | `status`, `role` |
| `PhysicalInterface` | interface | `status`, `speed` |
| `L3VPNService` | vpn | `status` |
| `TrafficFlow` | flow | `phase`, `config` |

### Metrics Table → Feature Mapping

| NetworkMetrics Metric | Node Type(s) | Feature(s) |
|----------------------|--------------|------------|
| `node_load1` | router | `cpu` |
| `node_memory_*` | router | `mem` |
| `frr_route_total*` | router, vpn | `ospf_num_routes`, `total_prefixes` |
| `frr_bgp_peer_prefixes_advertised_count_total` | router | `pfx_count_norm` |
| `frr_bfd_peer_count` | router | `bfd_peers` |
| `frr_ospf_*` | router | `ospf_neighbors`, `ospf_adj` |
| `node_network_*` | interface | 13 interface features |

| TrafficFlowSeries Metric | Node Type | Feature | Read from `role` |
|--------------------------|-----------|---------|------------------|
| `traffic_agent_throughput_sent_bps` | flow | `throughput_sent_bps` | `source` |
| `traffic_agent_throughput_recv_bps` | flow | `throughput_recv_bps` | `destination` |
| `traffic_agent_latency_ms` | flow | `latency_ms` | `destination` |
| `traffic_agent_jitter_ms` | flow | `jitter_ms` | `destination` |
| `traffic_agent_packet_loss_pct` | flow | `packet_loss_pct` | `destination` |

---

## Key Design Patterns

1. **SCD Type 2 Temporal Queries**: All topology tables use `valid_start_ts` and `valid_end_ts` to query point-in-time snapshots
2. **Metrics Aggregation**: Counter metrics are converted to rates; gauges are averaged
3. **Feature Normalization**: Utilization features clamped to [0, 1]; MTU normalized to jumbo frame size
4. **One-Hot Encoding**: Router roles are one-hot encoded (4 classes)
5. **Derived Features**: Utilization computed from bytes/sec ÷ speed; gradients from temporal deltas
6. **Edge Deduplication**: OSPF + BGP peer discovery merged via set union

---

## Next Steps for Feature Engineering

Potential improvements:
- Add mask tensors for missing data instead of 0.0 imputation
- Add more temporal features (latency gradient, throughput delta)
- Normalize route counts and peer counts
- Add flow age feature (time since flow started)
- Add interface flap detection (carrier_changes spike)
