# GNN Model for Root Cause Analysis

A HetGNN is trained on historical network topology and telemetry to pinpoint the root cause of failures. It learns what a healthy network looks like in an unsupervised manner, then flags nodes whose current behaviour deviates significantly from that learned baseline.

The model captures temporal signal through **pre-computed temporal features** (gradients, deltas, uptime) fed as node attributes — detecting trends such as rising CRC error rates or flapping BGP sessions without requiring a sequential model.

---

## Network Under Test

The topology and traffic patterns are the L3VPN hub-and-spoke example from the transport scenarios documentation.

The GNN identifies anomalies by comparing the current state to the learned "normal" baseline. What is normal differs significantly by router role:

| Role | Normal Behaviour | Anomaly Signal |
|---|---|---|
| **P Router** | High throughput, high CPU, zero customer BGP sessions | Any BGP activity on P = security anomaly |
| **RR Router** | High BGP churn, low data throughput; single most critical control-plane node | Loss of iBGP sessions from all PEs simultaneously |
| **PE Router** | High memory (thousands of VRF entries), active BGP to CE and RR | Memory pressure without VRF growth; BGP peer loss |
| **CE Router** | Lower throughput, sensitive to local interface flaps | Interface drops, loss of eBGP to PE |

### Test Strategy

The GNN is trained on telemetry collected during a 48-hour time-compressed traffic run that simulates approximately **1.3 years of realistic daily and weekly traffic patterns** across both VPNs. Two separate TrafficTest manifests drive the simulation, one per VPN, each producing a distinct traffic character.

![traffic strategy](/docs/drawings/traffic-strategy.svg)

#### Time Compression

All traffic patterns use a **240× time compression ratio**:

| Real time | Simulated time |
|---|---|
| 15 seconds | 1 hour |
| 6 minutes (360 s) | 1 day |
| 42 minutes (2,520 s) | 1 week |
| **48 hours (172,800 s)** | **≈ 480 days ≈ 1.3 years** |

Compression is achieved by setting `time_reference: elapsed` and scaling the `period` and `phase_offset` fields in the `multi_sine` pattern config. The `multi_sine` pattern repeats indefinitely, so the 48-hour run captures hundreds of day/night cycles and dozens of weekday/weekend transitions without any special scheduling.

The phase-offset formula used throughout both manifests:
```
phase_offset = period/4 − peak_elapsed_second
```

#### RED VPN — Business Enterprise Traffic

The RED VPN (`l3vpn-red-service`) operates as an **any-to-any mesh** across four branch sites (Norwich, Coventry, Plymouth, Leicester). Six bidirectional flow pairs cover every device combination:

| Pair | Protocol | Character | Forward peak |
|---|---|---|---|
| d1-red ↔ d2-red | TCP | ERP / file sync | ~42 Mbps at 14:00 sim |
| d1-red ↔ d3-red | TCP | DB replication (diagonal) | ~35 Mbps at 14:00 sim |
| d1-red ↔ d4-red | UDP | VoIP / video quality probe | ~11 Mbps at 14:00 sim |
| d2-red ↔ d3-red | TCP | Cross-site data replication | ~38 Mbps at 14:00 sim |
| d2-red ↔ d4-red | UDP | Telemetry + SNMP monitoring | ~17 Mbps at 14:00 sim |
| d3-red ↔ d4-red | UDP | Quality probe (13:30 sim) | ~15 Mbps at 13:30 sim |

**Pattern shape:** Single workday peak using two superimposed sine waves:
- Daily component (`period=360`): peak at 14:00 simulated, trough overnight
- Weekly component (`period=2520`): mid-week (Wednesday) peak, weekend trough

Downloads are 40–60 % heavier than uploads on all TCP pairs. UDP pairs use a constant reverse stream (SNMP/heartbeat) so that any rate deviation during congestion is immediately visible as an anomaly.

**Saturation:** At the 14:00 simulated peak, aggregate load on each 100 Mbit PE-CE interface reaches 85–95 %, generating congestion events, tail-drop, and jitter spikes that train the GNN to distinguish congested-but-healthy from genuinely-impaired flows.

The d3-red ↔ d4-red UDP pair peaks 30 simulated minutes earlier (13:30) to de-correlate congestion windows across pairs, ensuring the GNN sees a richer variety of partial-saturation states rather than a single synchronised peak.

#### BLUE VPN — Consumer Cell Backhaul Traffic

The BLUE VPN (`l3vpn-blue-service`) operates as a **hub-spoke** topology with the Nottingham hub serving three spoke sites (Sheffield, Liverpool, Huddersfield). Four tests cover all spokes:

| Test | Protocol | Character |
|---|---|---|
| d1-blue ↔ dh-blue | TCP | Sheffield cell backhaul — full bidir |
| d2-blue ↔ dh-blue | UDP | Liverpool streaming quality + constant hub push |
| d3-blue ↔ dh-blue | TCP | Huddersfield cell backhaul — full bidir [new] |
| d1+d2+d3 → dh-blue | UDP | Aggregate multi-source hub load probe [new] |

**Pattern shape:** Bimodal daily pattern using three superimposed sine waves:
- Half-day component (`period=180`): produces **two peaks per day** at 08:00 (commute) and 20:00 (entertainment) simulated
- Daily PM-bias component (`period=360`): evening peak slightly heavier than morning (streaming > news)
- Weekly weekend-lift component (`period=2520`): Saturday/Sunday evenings carry higher streaming load

Downloads from hub are 2–3× heavier than uploads (hub delivers video, app updates, and config; spokes upload telemetry and signalling). The Huddersfield spoke PM peak is shifted 1 simulated hour later (21:00) to stagger hub-inbound congestion across the three spokes.

**Saturation:** Hub inbound (pe2-ce1-hub, 100 Mbit) reaches ~100 Mbit at the 20:00 simulated peak from aggregate spoke uploads. The per-spoke download paths (spoke PE-CE, 100 Mbit each) saturate during hub-to-spoke content delivery bursts.

#### Training Data Profile

After 48 hours, the Ops Agent Prometheus scrape on each device has collected:

- **~17,280 Cloud Monitoring data points** per metric per device (at 10 s scrape interval)
- **10 simultaneous traffic flows** producing `throughput_bps`, `latency_ms`, `jitter_ms`, `packet_loss_pct`, `active_sessions` per flow and direction
- **Hundreds of congestion events** as PE-CE links approach saturation during compressed peak hours
- **480 complete day/night cycles** giving the model strong temporal generalisation across both business and consumer demand shapes
- **~68 weekday/weekend transitions** for the model to learn the distinct weekend demand signatures

The GNN snapshot pipeline assembles training graphs at 5-minute intervals. At 80/20 train/validation split, approximately 460 training snapshots and 116 validation snapshots are available — well above the 100-snapshot minimum required for stable scaler fitting.

---

## Graph Schema

This section defines the exact heterogeneous graph structure. Every training snapshot and inference call must produce a graph conforming to this schema.

### Node Types

**Four node types** cover all failure scenarios in the current implementation. Router role differentiation is handled through one-hot encoding (`role_P`, `role_PE`, `role_RR`, `role_CE`) rather than separate node types.

| Node Type | Description | Source Table | Feature Count |
|---|---|---|---|
| `router` | Any router (P, PE, RR, or CE). Role encoded via 4 one-hot features. | `PhysicalRouter` | 16 (13 continuous + 4 role one-hots, treated as 3 effective features) |
| `interface` | Physical interface on any router. | `PhysicalInterface` | 16 |
| `vpn` | L3VPN service instance. | `L3VPNService` | 3 |
| `flow` | A directional traffic flow between two endpoints, with live performance metrics. | `TrafficFlow` + `TrafficFlowSeries` | 7 |

> **Why not separate node types per role?** With only a handful of routers in the lab topology, separate node types (e.g. `router_rr`) would have very few training examples per type, making it hard for the model to learn a stable role-specific baseline. A single `router` type with role as one-hot features gives the model enough context to distinguish roles while sharing the projection layer across all routers for better generalisation.

> **Why VPN and Flow nodes?** VPN nodes aggregate VRF-level metrics (active BGP peer counts, total prefixes across VRFs) and enable detection of service-level degradation. VRF-specific features (`rt_import_signature`, `vrf_min_routes`, `vrf_count`) are computed **per-router** from the VRF table and included as router-level features rather than separate VRF nodes. This simplifies the schema while still capturing RT misconfiguration signals. Flow nodes expose end-to-end traffic health (latency, jitter, loss, throughput) independently of the physical and control-plane layers, enabling isolation of application-layer degradation from infrastructure faults.

> **Note:** Earlier designs included separate `bgp_session` and `vrf` node types. The current implementation aggregates BGP session state into router-level and VPN-level features, and VRF policy/route-count into router-level features, simplifying the graph schema to four node types.

### Node Features

All raw counter metrics are converted to **per-second rates** before feature engineering. Log-transform (`log1p`) is applied to high-variance counts. A `StandardScaler` is fitted per feature per node type across the training snapshots and serialised to `scalers.pkl`. Applied identically at both training and inference time.

> **Scaler scope:** For `router`, the scaler is applied **only to continuous columns** (cols 0–7) — the 4 role one-hot columns (cols 8–11) are excluded and remain in {0, 1}. For `interface`, `bgp_session`, `vrf`, and `flow`, all columns are continuous and the scaler is applied to the full feature array.

#### `router`

**16 features total** (13 continuous + 4 one-hot). Scaler applied to continuous features only (cols 0–12); role one-hots (cols 13–16) left unchanged.

| Feature | Source Metric | Derivation | Description |
|---|---|---|---|
| `state` | `PhysicalRouter.status` | `1.0` if `"running"`, else `0.0` | Router operational state from CRD status. |
| `cpu` | `node_load1` | Averaged over snapshot window | 1-minute CPU load. Values > 1.0 indicate overload. |
| `mem` | `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes` | Ratio [0, 1], higher = more free RAM | Available memory normalised; lower = more pressure. |
| `ospf_num_routes` | `frr_route_total` (all VRFs) | Summed across all VRFs | Total routes in FRR table. Sudden drop = convergence event. |
| `pfx_count_norm` | `frr_bgp_peer_prefixes_advertised_count_total` | Summed across all BGP peers on this router | Total BGP prefixes advertised by this router. |
| `routes_fib` | `frr_route_total_fib` | Summed | Routes actually installed in kernel FIB. |
| `bfd_peers` | `frr_bfd_peer_count` | Count | BFD sessions active on this router. |
| `ospf_neighbors` | `frr_ospf_neighbors` | Summed | Total OSPF neighbours. |
| `ospf_adj` | `frr_ospf_neighbor_adjacencies` | Summed | OSPF full adjacencies (FULL state). |
| `rt_import_signature` | `VRF.config` (JSON), aggregated per router | MD5 hash [0,1] of sorted rt_import RT strings across VRFs | Deterministic fingerprint of import RT policy; deviation = RT misconfiguration. |
| `vrf_min_routes` | `frr_route_total` per VRF | Minimum route count across VRFs on this router | Drops when any VRF loses routes (RT misconfig signal). |
| `vrf_count` | `VRF` table | Count of VRFs on this router | Number of VRFs provisioned. |
| `role_P` | `PhysicalRouter.role` | `1.0` if role = P, else `0.0` | One-hot role encoding — P router. |
| `role_PE` | `PhysicalRouter.role` | `1.0` if role = PE, else `0.0` | One-hot role encoding — Provider Edge. |
| `role_RR` | `PhysicalRouter.role` | `1.0` if role = RR, else `0.0` | One-hot role encoding — Route Reflector. |
| `role_CE` | `PhysicalRouter.role` | `1.0` if role = CE, else `0.0` | One-hot role encoding — Customer Edge. |

#### `interface`

**16 features total**. Scaler applied to all 16 columns (all continuous).

| Feature | Source Metric | Derivation | Description |
|---|---|---|---|
| `state` | `node_network_up` (overrides `PhysicalInterface.status` if present) | `1.0` = up, `0.0` = down | Interface operational state from metrics; falls back to CRD status. |
| `rx_drops` | `node_network_receive_drop_total` | Rate (per second), averaged over window | Inbound packet drop rate. Indicates congestion or MTU mismatch. |
| `tx_drops` | `node_network_transmit_drop_total` | Rate (per second), averaged over window | Outbound packet drop rate. Indicates egress queue overflow. |
| `mtu_norm` | `node_network_mtu_bytes` | `bytes / 9000` | MTU normalised against 9 KB jumbo maximum (~0.167 for standard 1500 B). |
| `rx_errs_rate` | `node_network_receive_errs_total` | Rate (per second), averaged over window | Inbound error rate. Used as input for `rx_err_gradient`. |
| `tx_errs_rate` | `node_network_transmit_errs_total` | Rate (per second), averaged over window | Outbound error rate. |
| `rx_bytes_rate` | `node_network_receive_bytes_total` | Rate (bytes/sec), averaged over window | Raw receive byte rate. Used for `rx_util` derivation. |
| `tx_bytes_rate` | `node_network_transmit_bytes_total` | Rate (bytes/sec), averaged over window | Raw transmit byte rate. Used for `tx_util` derivation. |
| `rx_packets_rate` | `node_network_receive_packets_total` | Rate (packets/sec), averaged over window | Inbound packet rate. |
| `tx_packets_rate` | `node_network_transmit_packets_total` | Rate (packets/sec), averaged over window | Outbound packet rate. |
| `tx_queue_len` | `node_network_transmit_queue_length` | Gauge, averaged over window | TX queue depth (raw value, not normalised). |
| `carrier_changes` | `node_network_carrier_changes_total` | Rate (per second) | Link flap counter. |
| `carrier_state` | `node_network_carrier` | Gauge (0.0 or 1.0) | Physical link signal present. |
| `rx_err_gradient` | Computed from `rx_errs_rate` | `(rx_errs_rate[t] − rx_errs_rate[t−1]) / interval_seconds` | **Engineered temporal feature.** Rate of change of inbound errors. Detects rising error trend before link failure. `0.0` for first snapshot. Computed by `SpannerDataset.compute_temporal_features()`. |
| `tx_util` | Derived | `tx_bytes_rate × 8 / speed_bps`, capped at 1.0 | Transmit utilisation in [0, 1]. |
| `rx_util` | Derived | `rx_bytes_rate × 8 / speed_bps`, capped at 1.0 | Receive utilisation in [0, 1]. |

> **Traffic asymmetry via message passing:** MTU mismatch is detected implicitly. When interface A aggregates messages from the physically connected interface B, it receives B's `rx_util`. The model learns that in a healthy link, `tx_util(A) ≈ rx_util(B)`. A mismatch — A transmitting at high `tx_util` while B reports low `rx_util` — becomes visible through 2-hop message passing without requiring edge feature support.

#### `vpn`

**3 features total**. Scaler applied to all 3 columns (all continuous).

| Feature | Source | Derivation | Description |
|---|---|---|---|
| `state` | `L3VPNService.status` | `1.0` if `"active"` or `"running"`, else `0.0` | VPN service operational state. |
| `num_active_peers` | `BGPSession` count via `VRF.vpn_id` | Count of BGP sessions with `status = "Established"` whose VRF maps to this VPN | Number of active BGP sessions across all VRFs in this VPN. |
| `total_prefixes` | `frr_route_total` per VRF, summed | Summed across all VRFs belonging to this VPN (matched via `VRF.name` → metric `vrf` label) | Total routes across all VRFs in this VPN. |

> **VRF-level features are now router-level:** Earlier designs had separate `vrf` nodes with per-VRF RT hashes and route counts. The current implementation computes VRF-aggregation features (`rt_import_signature`, `vrf_min_routes`, `vrf_count`) **at the router level** from all VRFs on that router, simplifying the schema while retaining RT misconfiguration detection.

#### `flow`

**7 features total**. Scaler applied to all 7 columns (all continuous). The model learns what "normal" looks like purely from observed traffic patterns in training data.

| Feature | Source | Derivation | Description |
|---|---|---|---|
| `throughput_sent_bps` | `traffic_agent_throughput_sent_bps` | Averaged over interval window | Observed send throughput from source-role traffic-agent. |
| `throughput_recv_bps` | `traffic_agent_throughput_recv_bps` | Averaged over interval window | Observed receive throughput from destination-role traffic-agent. |
| `latency_ms` | `traffic_agent_latency_ms` | Averaged over interval window (UDP only) | End-to-end RTT. Spike = path congestion or reroute. |
| `jitter_ms` | `traffic_agent_jitter_ms` | Averaged over interval window (UDP only) | Jitter. Rising jitter = congestion or queueing. |
| `packet_loss_pct` | `traffic_agent_packet_loss_pct` | Averaged over interval window (UDP only) | Packet loss fraction [0, 1]. |
| `is_running` | `TrafficFlow.phase` | `1.0` if phase = `"running"`, else `0.0` | Flow operational state from CRD. |
| `is_bidirectional` | `TrafficFlow.config.spec.bidirectional` | `1.0` if `"true"`, else `0.0` | Whether flow is bidirectional. |

> **Metrics source:** Flow metrics are read from the `TrafficFlowSeries` table using the `TrafficFlowSeriesByTimestamp` secondary index for efficient time-range queries. Values are averaged (AVG) over the interval window to reduce sensitivity to single-sample spikes (was MAX in earlier versions, which caused false-positive anomaly scores). The `GROUP BY` carries `role`, and each KPI is taken only from the end of the leg that measures it — both ends report every metric, but a sender observes no loss, delay or jitter.

### Edge Types

Edges are expressed as `(source_node_type, relation_name, target_node_type)` tuples. Bidirectional edge pairs are registered as **two separate typed edges** in the graph schema (both directions in `EDGE_TYPES`).

**Current implementation** (11 edge types across 4 node types):

| Edge Type Tuple | Layer | Source Table / View | Description |
|---|---|---|---|
| `(router, has_interface, interface)` | Physical | `PhysicalInterface.router_id` | Router owns an interface. |
| `(interface, interface_of, router)` | Physical | `PhysicalInterface.router_id` | Interface belongs to a router (inverse of `has_interface`). |
| `(interface, connected_to, interface)` | Physical | `Interface_Link` join on shared `link_id` | Two interfaces physically cabled together (symmetric). |
| `(router, peer_of, router)` | Control plane | OSPF peers via `PhysicalLink` + iBGP peers via `BGPSession.peer_ip` | Router-to-router peering (OSPF or iBGP, symmetric). |
| `(router, member_of, vpn)` | Service | `VRF.router_id` + `VRF.vpn_id` | Router is a member of a VPN (via its VRFs). |
| `(vpn, has_member, router)` | Service | `VRF.router_id` + `VRF.vpn_id` | VPN has a member router (inverse of `member_of`). |
| `(flow, originates_from, interface)` | Traffic | `TrafficFlow.src_device_id` → `Device.interface_id` or `Device.gateway` → `PhysicalInterface.ip_address` | Flow originates from this interface. |
| `(interface, source_of, flow)` | Traffic | Inverse of `originates_from` | Interface is the source of this flow. |
| `(flow, terminates_at, interface)` | Traffic | `TrafficFlow.dst_device_id` → `Device.interface_id` or `Device.gateway` → `PhysicalInterface.ip_address` | Flow terminates at this interface. |
| `(interface, destination_of, flow)` | Traffic | Inverse of `terminates_at` | Interface is the destination of this flow. |
| `(flow, runs_over, vpn)` | Traffic | `TrafficFlow.config.metadata.labels.vpn` → `L3VPNService.name` | Flow runs over this VPN. |

> **Removed edge types from earlier designs:** `ospf_peer`, `bgp_peer`, `session_on`, `has_vrf`, `contains_session`, `same_vpn_as`, `source_pe`, `dest_pe`, `belongs_to_vrf` were part of schemas with separate `bgp_session` and `vrf` node types. The current simplified schema aggregates BGP/VRF features into router and VPN nodes, reducing the edge-type count to 11.

### Why the Four-Layer Graph Architecture

The current implementation uses a **four-layer heterogeneous graph** (router, interface, vpn, flow) that spans from physical layer counters up to end-to-end application performance:

```
router ──── interface                           ← physical + control plane
   │              │
   │         tx_queue_len, carrier_changes      ← direct physical impairment signals
   │         rx_err_gradient                    ← temporal degradation signal
   │
   │    rt_import_signature, vrf_min_routes     ← VRF-aggregated RT policy signals (router-level)
   │
   ▼
  vpn ──── router (member_of / has_member)      ← service layer
   │    num_active_peers, total_prefixes        ← VPN-level BGP health
   │
   ▼
  flow ──── interface (originates_from / terminates_at)  ← application plane
         throughput_sent_bps, throughput_recv_bps        ← observed throughput signals
         packet_loss_pct, jitter_ms, latency_ms          ← path impairment signals
```

**Key architectural choices:**

1. **VRF features are router-level, not separate nodes:** `rt_import_signature`, `vrf_min_routes`, and `vrf_count` are computed per-router from all VRFs on that router. This simplifies the schema while still detecting RT misconfiguration (signature deviation) and VRF route loss (min routes drop).

2. **BGP session state is aggregated:** Session counts and prefix counts are aggregated at the router level (`pfx_count_norm`, `ospf_adj`) and VPN level (`num_active_peers`), rather than separate `bgp_session` nodes. This reduces graph complexity for a small topology.

3. **Flow nodes provide end-to-end observability:** Separate from physical/control layers, enabling isolation of application-layer degradation even when infrastructure appears healthy.

---

## Model Architecture

The HetGNN is a **Graph Autoencoder**: an encoder compresses the heterogeneous graph into a latent embedding per node; a decoder reconstructs the original features from that embedding. High reconstruction error at inference time = anomalous node.

```
Input: HeteroData (single snapshot)
         │
         ▼
┌──────────────────────────────────────────┐
│  Type-specific Input Projection          │
│  Linear(input_dim_type, hidden_dim)+ReLU │
│  One projection layer per node type      │
│  (router, interface, bgp_session,        │
│   vrf, flow)                             │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  Heterogeneous Message Passing           │
│  HeteroConv(SAGEConv, aggr='sum')        │
│  NUM_LAYERS=2                            │
│  Edges filtered to types with active     │
│  nodes; isolated nodes retain their      │
│  projected representation                │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  Type-specific Decoders                  │
│  One Linear(hidden_dim, input_dim) per   │
│  node type → reconstructed features     │
└──────────────────────────────────────────┘
         │
         ▼
Per-node, per-feature reconstruction error (MSE)
```

Separate decoders per node type enable reconstruction error to be isolated by layer (router vs. interface vs. BGP session vs. VRF vs. flow), which is the basis for the RCA drill-down.

### Hyperparameters

| Parameter | Value | Notes |
|---|---|---|
| `HIDDEN_CHANNELS` | 64 (default, env: `HIDDEN_CHANNELS`) | Shared hidden dimension across all node types |
| `NUM_LAYERS` | 2 (default, env: `NUM_LAYERS`) | Heterogeneous message-passing layers |
| `DROPOUT` | 0.3 (default, env: `DROPOUT`) | Dropout probability applied after input projection and between conv layers |
| `SNAPSHOT_INTERVAL` | 0.5 min (default, env: `INTERVAL_MINUTES`) | Time between snapshots. Default 0.5 min = 30 s for 240× compressed traffic (30 s real = 2 simulated hours). For real-world traffic use `INTERVAL_MINUTES=5`. |
| `TRAINING_SNAPSHOTS` | 100 (default, adjustable via `num_snapshots` in live mode or pre-fetched count in Vertex AI mode) | Snapshots used for training. 80/20 split → ~80 training + ~20 validation. Default 100 gives stable scaler fitting. |
| `LEARNING_RATE` | 0.001 | Adam optimizer initial LR |
| `EPOCHS` | 50 | Maximum training epochs |
| `EARLY_STOPPING_PATIENCE` | 10 | Epochs without improvement before stopping |
| `MIN_DELTA` | 0.001 | Minimum change to qualify as improvement |
| `VALIDATION_SPLIT` | 0.2 | 20% of snapshots reserved for validation |
| `RANDOM_SEED` | None (stochastic, env: `RANDOM_SEED`) | If set, PyTorch RNG is seeded for reproducibility. Default is stochastic (different weight init each run). |

---

## Training Pipeline

### Loss Function

A weighted multi-task MSE loss computed independently per node type. Independent losses enable per-layer anomaly isolation:

```
L_total = ALPHA · L_router + GAMMA · L_interface + VPN_WEIGHT · L_vpn + FLOW_WEIGHT · L_flow + DIVERSITY_WEIGHT · L_diversity
```

Where:
- `L_<type> = MSE(x̂_type, x_type)` — reconstruction error per node type
- `L_diversity` — contrastive diversity penalty (mean pairwise cosine similarity within a node type) to prevent embedding collapse
- **Current weights:** `ALPHA=0.25` (router), `GAMMA=0.25` (interface), `VPN_WEIGHT=0.25` (vpn), `FLOW_WEIGHT=0.25` (flow), `DIVERSITY_WEIGHT=0.1`

> **VPN weight rationale:** VPN weight was boosted from 0.10 to 0.25 to give the autoencoder sufficient gradient signal to learn good VPN reconstructions despite only N=3 VPN nodes per snapshot (was causing high run-to-run variance in VPN anomaly scores).

Learning rate is reduced on plateau (`ReduceLROnPlateau`, factor=0.5, patience=5, min_lr=1e-6).

### Snapshot Construction (Point-in-Time Spanner Query)

For each training timestamp `T`, the snapshot is assembled using the SCD Type 2 temporal filter across all topology tables:

```sql
-- Routers active at time T
SELECT r.id, r.name, r.role, r.status
FROM PhysicalRouter r
WHERE r.valid_start_ts <= @T
  AND (r.valid_end_ts > @T OR r.valid_end_ts IS NULL)
```

The same filter applies to `PhysicalInterface`, `BGPSession`, `VRF`, `BGP_Peering`, `Interface_Link`, and `TrafficFlow`. Metrics are averaged over the snapshot window using `ALIGN_MEAN` (gauges) or `ALIGN_RATE` (counters).

### Preprocessing Steps

1. **Rate conversion:** Counter metrics (`*_total`) are converted to per-second rates by averaging delta values over the snapshot interval window.
2. **Gradient / delta computation:** `rx_err_gradient` is computed as `(rx_errs_rate[t] − rx_errs_rate[t−1]) / interval_seconds` **after all snapshots are fetched**, by calling `SpannerDataset.compute_temporal_features(snapshots, interval_seconds)`. Zero for the first snapshot.
3. **StandardScaler:** Fitted per feature per node type across **training snapshots only** (not validation). For `router`, only continuous columns 0–12 are scaled (4 role one-hots excluded). For `interface`, `vpn`, and `flow`, all columns are continuous and the full array is scaled. Serialised to `scalers.pkl`. Applied identically at training and inference time.
4. **Missing metrics:** If a metric is absent for a node at time `T`, substitute `0.0` (no imputation from previous values).
5. **Bad snapshot filtering:** Snapshots with any per-feature mean >4σ from the dataset mean (across router/interface feature subsets) are dropped before training to remove data collection artifacts. Logged as warnings with the offending feature and z-score.
6. **New nodes mid-window:** Nodes absent in a given snapshot receive a zero feature vector in the feature matrix for that snapshot.

### Train / Validation Split

- **Split:** 80% train / 20% validation on individual snapshots, chronological order (validation snapshots are always later than training snapshots).
- Scalers are fitted on training snapshots only and applied to validation snapshots (no data leakage).

### Model Registration and Quality Gates

**Current implementation:** The training pipeline writes artefacts (`model.pth`, `scalers.pkl`, `model_stats.pth`) to the output directory (local or `AIP_MODEL_DIR` for Vertex AI). No explicit quality gates are enforced in `train_hetgnn.py` — the model is always saved after training completes (or early stopping triggers).

**Recommended quality gates** (not currently implemented):

1. `best_val_loss < 0.05` (aggregate MSE across all node types)
2. No single node type dominates: `L_type / L_total < 0.8` for all types (guards against a collapsed model)
3. Healthy embedding standard deviation `< 0.3` per dimension in `cluster_stats` (stable healthy-state representation)

If implementing quality gates, the KFP pipeline component should check these conditions and fail the training job if any are violated, preventing a degraded model from being deployed.

---

## Inference and Anomaly Scoring

### Per-Type Anomaly Thresholds

**Current implementation** uses **Mahalanobis distance** thresholds computed from healthy training embeddings, stored in `model_stats.pth`:

| Node Type | Threshold Computation | Rationale |
|---|---|---|
| `router` | `mean + 2σ` of healthy Mahalanobis distances | Calibrated to healthy distribution; ~95th percentile |
| `interface` | `mean + 2σ` of healthy Mahalanobis distances | Calibrated to healthy distribution; ~95th percentile |
| `vpn` | `mean + 2σ` of healthy Mahalanobis distances | Calibrated to healthy distribution; ~95th percentile |
| `flow` | `mean + 2σ` of healthy Mahalanobis distances | Calibrated to healthy distribution; ~95th percentile |

**Fallback MSE thresholds** (used if `model_stats.pth` is absent or for backward compatibility):

| Node Type | Default MSE Threshold | Notes |
|---|---|---|
| `router` | 0.15 | Moderate; CPU/mem variance is expected across roles |
| `interface` | 0.20 | Higher variance from traffic bursts; looser threshold avoids false positives |
| `vpn` | 0.10 | VPN features have low variance; tight threshold |
| `flow` | 0.20 | Moderate; the model learns expected throughput from training |

**Anomaly scoring:**

The serving pipeline computes Mahalanobis distance `D_M(z)` for each node's embedding `z`:

```
D_M(z) = sqrt((z - μ)^T Σ^{-1} (z - μ))
```

where `μ` and `Σ^{-1}` are the healthy embedding mean and inverse covariance from `cluster_stats` in `model_stats.pth`. A node is flagged as anomalous when:

```
is_anomaly(node) = D_M(embedding) > calibrated_threshold(node_type)
```

The `calibrated_threshold` is computed during training as:

```
threshold = mean(D_M(healthy_embeddings)) + 2 × std(D_M(healthy_embeddings))
```

This adaptive threshold adjusts to the actual healthy embedding distribution and is more robust than fixed MSE thresholds.

### Per-Feature Anomaly Contribution

The inference pipeline computes reconstruction error **per feature** for each anomalous node. The top-3 highest-MSE features are reported in the `anomaly_explanation` JSON column:

```json
{
  "name": "pe-router-1",
  "anomalous": true,
  "top_features": [
    {"feature": "pfx_count_norm",   "mse": 1.60},
    {"feature": "ospf_num_routes",  "mse": 0.12},
    {"feature": "mem",              "mse": 0.07}
  ]
}
```

Non-anomalous nodes receive a minimal record:

```json
{
  "name": "p-router-1",
  "anomalous": false
}
```

### Spanner Output Contract

Each inference run writes one row to `NodeEmbedding` per node:

| Column | Type | Value |
|---|---|---|
| `id` | STRING(36) | UUID generated per row |
| `node_id` | STRING | FK to `PhysicalRouter.id`, `PhysicalInterface.id`, `BGPSession.id`, `VRF.id`, or `TrafficFlow.id` |
| `node_type` | STRING | One of: `router`, `interface`, `bgp_session`, `vrf`, `flow` |
| `hetgnn_embedding` | ARRAY\<FLOAT64\> | Latent embedding vector (length = 64) |
| `hetgnn_score` | FLOAT64 | Aggregate MSE reconstruction error |
| `anomaly_explanation` | JSON | Top-3 per-feature MSE scores for anomalous nodes; `{"anomalous": false}` for healthy nodes |
| `timestamp` | TIMESTAMP | `PENDING_COMMIT_TIMESTAMP()` |

---

## Root Cause Analysis

When the GNN flags anomalies, a two-stage process identifies the root cause:

1. **GNN inference** writes per-node `hetgnn_score` and `anomaly_explanation` to `NodeEmbedding`
2. **Rule-based classifier** reads those outputs and applies a deterministic decision tree to classify the fault type

### Fault Classification Algorithm

The classifier is a decision tree applied over the aggregated `NodeEmbedding` results. The GNN does the anomaly detection; the rules do the classification of what type of failure it is.

```
INPUT: NodeEmbedding rows from the latest inference run

STEP 1 — Is there an anomaly?
  If no nodes exceed threshold → EXIT (no fault)

STEP 2 — Which layer has the highest error?
  Compute MAX(hetgnn_score) per node_type.
  Dominant layer = node_type with the highest max score.

STEP 3 — Apply layer-specific pattern

  CASE dominant layer = 'flow':
    IF top_feature IN ('throughput_bps', 'throughput_delta')
      → TRAFFIC DEGRADATION (throughput collapse or sudden change)
      → Root cause: flow node + source_pe and dest_pe router nodes
    ELSE IF top_feature = 'packet_loss_pct'
      → PACKET LOSS (physical impairment on path)
    ELSE IF top_feature IN ('latency_ms_norm', 'jitter_norm')
      → LATENCY / JITTER (congestion or queueing on path)
    ELSE IF top_feature = 'active_sessions'
      → SESSION DEGRADATION (fewer concurrent sessions than trained baseline)

  CASE dominant layer = 'vrf':
    IF top_feature IN ('rt_import_hash', 'rt_export_hash')
      → RT POLICY CHANGE (misconfiguration or leak)
      → Root cause: vrf node on the PE with the changed hash
    ELSE IF top_feature = 'vrf_route_count'
      → VRF ROUTE LOSS (routes withdrawn from this VPN)
    ELSE IF top_feature = 'vrf_active_sessions'
      → BGP SESSION LOSS within VRF (link down or peer crash)

  CASE dominant layer = 'interface':
    IF top_feature = 'rx_err_gradient'
      → HARDWARE DEGRADATION
      → Root cause: interface with highest rx_err_gradient score
    ELSE IF top_feature IN ('mtu_norm', 'tx_util', 'rx_drops')
      → MTU MISMATCH / SILENT DROP
      → Root cause: interface with highest mtu_norm score
    ELSE IF top_feature = 'state'
      → INTERFACE DOWN

  CASE dominant layer = 'bgp_session':
    Join all anomalous bgp_session nodes → parent router (via VRF.router_id)
    Group by parent router, count failing sessions per router.
    IF router with most failing sessions has role = 'RR'
      → RR CRASH (common path analysis)
      → Root cause: the RR router node
    ELSE IF exactly 1 session is anomalous AND its parent has role = 'CE'
      → LOCAL ACCESS FAILURE (PE-CE link)
      → Root cause: the specific bgp_session
    ELSE IF top_feature = 'session_uptime_norm' across sessions on 2+ different routers
      → IP OVERLAP / DUPLICATE IP
      → Root cause: bgp_session with the most recent valid_start_ts

  CASE dominant layer = 'router':
    IF top_feature = 'ospf_num_routes' → ROUTING CONVERGENCE ISSUE
    IF top_feature IN ('cpu', 'mem')   → RESOURCE EXHAUSTION
    IF top_feature = 'vrf_count'       → VRF DELETION / MISCONFIGURATION
```

#### Step 1 — Check for Any Anomaly

```sql
SELECT
  node_id,
  node_type,
  hetgnn_score,
  JSON_VALUE(anomaly_explanation, '$.name')                         AS node_name,
  JSON_VALUE(anomaly_explanation, '$.top_features[0].feature')      AS top_feature,
  CAST(JSON_VALUE(anomaly_explanation, '$.top_features[0].mse') AS FLOAT64) AS top_mse
FROM NodeEmbedding
WHERE timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND hetgnn_score > @threshold
ORDER BY hetgnn_score DESC
LIMIT 20;
```

#### Step 2 — Identify the Dominant Failure Layer

```sql
SELECT
  node_type,
  COUNT(*)          AS anomalous_node_count,
  MAX(hetgnn_score) AS max_score,
  AVG(hetgnn_score) AS avg_score
FROM NodeEmbedding
WHERE timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND hetgnn_score > @threshold
GROUP BY node_type
ORDER BY max_score DESC;
```

#### Step 3 — BGP Common Path Analysis (RR Crash vs. Local Failure)

```sql
-- Group failing bgp_session nodes by their parent router.
-- The router with the most failing sessions is the common-path root cause.
SELECT
  r.name                   AS parent_router,
  r.role,
  COUNT(e.node_id)         AS failing_session_count,
  MAX(e.hetgnn_score)      AS max_session_score
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id   AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id        AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id        AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
GROUP BY r.name, r.role
ORDER BY failing_session_count DESC, max_session_score DESC
LIMIT 5;
-- role = 'RR' AND count > 1  →  RR Crash
-- role = 'CE' AND count = 1  →  Local Access Failure
```

---

### Failure Scenario Analysis

#### MTU Mismatch (Silent Drop)

**Symptom:** BGP and OSPF adjacencies remain "Up" but TCP/UDP traffic fails or slows severely.

- **Signal:** `tx_util` on one side of a link is high while the connected interface's `rx_util` is low. After 2-hop message passing, the model reconstructs what `tx_util` *should* predict for the neighbour's `rx_util` — the mismatch raises the reconstruction error. Additionally, the `flow` node for any flow traversing the link will show elevated `packet_loss_pct` and a drop in `throughput_bps` / `throughput_delta`.
- **GNN Observation:** High `interface` reconstruction error; `bgp_session` and `router` nodes healthy. `flow` node anomalous with high `packet_loss_pct`.
- **Top Feature:** `mtu_norm` on the specific `PhysicalInterface`.
- **Conclusion:** Physical Layer — MTU mismatch between core and edge.

```sql
SELECT
  i.name                                                                            AS interface_name,
  r.name                                                                            AS router_name,
  r.role,
  e.hetgnn_score,
  JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature')                   AS top_feature,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.top_features[0].mse') AS FLOAT64)      AS top_mse
FROM NodeEmbedding  e
JOIN PhysicalInterface i ON i.id        = e.node_id AND i.valid_end_ts IS NULL
JOIN PhysicalRouter    r ON i.router_id = r.id      AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'interface'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY e.hetgnn_score DESC
LIMIT 5;
```

#### BGP Session Down / Service Outage

**Symptom:** Total loss of connectivity between specific customer sites.

- **Signal:** `bgp_state` drops to `0.0`; `prefix_count_delta` drops sharply; `pfx_count_norm` approaches zero.
- **GNN Observation:** High `bgp_session` reconstruction error; physical and OSPF layers healthy. Associated `flow` nodes will also be anomalous (zero throughput).
- **Isolation:** If error is on a session linked to a router with `role = 'CE'` → **Local Access Failure**. If error is on sessions linked to a router with `role = 'RR'` → **Route Reflector root cause** (use common path query above).
- **Conclusion:** Overlay layer failure; suppress per-PE alerts, raise single RR alert.

```sql
-- Show each failing session with its parent router role
SELECT
  s.peer_ip,
  r.name                                                                           AS parent_router,
  r.role,
  e.hetgnn_score,
  JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature')                  AS top_feature,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.top_features[0].mse') AS FLOAT64)     AS top_mse
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id  AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id       AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id       AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY e.hetgnn_score DESC;
-- Multiple rows with role = 'RR' → run the common path query to confirm RR crash
-- Single row with role = 'CE'   → local PE-CE access failure
```

#### Hardware Degradation (CRC Errors)

**Symptom:** Intermittent packet loss and increasing latency before any routing failover.

- **Signal:** `rx_err_gradient` is positive on a specific `interface` node — errors are increasing even if the absolute count is still low.
- **GNN Observation:** Increasing `interface` reconstruction error over successive inference cycles. Associated `flow` nodes may show rising `packet_loss_pct` and `jitter_norm`.
- **Conclusion:** Physical Layer Impairment (failing SFP or fiber bend) — alert raised proactively before link failure.

```sql
-- Show the trend of anomaly score over the last 30 minutes.
-- A steadily rising score confirms gradual degradation rather than a transient blip.
SELECT
  i.name                                                                                    AS interface_name,
  r.name                                                                                    AS router_name,
  r.role,
  e.timestamp,
  e.hetgnn_score,
  JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature')                           AS top_feature,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.top_features[0].mse') AS FLOAT64)              AS top_mse
FROM NodeEmbedding  e
JOIN PhysicalInterface i ON i.id        = e.node_id AND i.valid_end_ts IS NULL
JOIN PhysicalRouter    r ON i.router_id = r.id      AND r.valid_end_ts IS NULL
WHERE e.node_type = 'interface'
  AND JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature') = 'rx_err_gradient'
  AND e.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
ORDER BY i.name, e.timestamp;
-- Steadily increasing hetgnn_score across rows = hardware degradation
-- Single spike then recovery = transient noise
```

#### IP Address Overlap / Duplicate IP

**Symptom:** Flapping sessions — connectivity works briefly, drops, returns.

- **Signal:** `session_uptime_norm` repeatedly low (session keeps resetting); `bgp_state` oscillates between `0.0` and `1.0`.
- **GNN Observation:** High `bgp_session` reconstruction error on sessions belonging to two different routers.
- **Isolation:** The `bgp_session` node with the most recent `valid_start_ts` before the flapping began is flagged as the rogue.
- **Conclusion:** Logical Identity Conflict — duplicate peer IP.

```sql
-- Find sessions with the same peer_ip on different routers.
-- The row with MAX(valid_start_ts) for a given peer_ip is the rogue session.
SELECT
  s.peer_ip,
  r.name                                                                                   AS parent_router,
  s.valid_start_ts                                                                         AS session_established,
  e.hetgnn_score,
  JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature')                          AS top_feature,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.top_features[0].mse') AS FLOAT64)             AS top_mse
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id  AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id       AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id       AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY s.peer_ip, s.valid_start_ts DESC;
-- Duplicate peer_ip values across different parent_router rows = IP overlap confirmed
```

#### Route Reflector (RR) Process Crash

**Symptom:** Network core physically healthy; no new routes learned; VPNv4 table empties.

- **Signal:** `frr_collector_up = 0` on the RR router; `frr_route_total` drops; all PE BGP sessions lose prefixes simultaneously.
- **GNN Observation:** High reconstruction error on the `router` node where `role_RR=1.0`; all `bgp_session` nodes linked to it via `session_on` are also anomalous. Associated `vrf` nodes on all PEs will show elevated `vrf_route_count` and `vrf_active_sessions` reconstruction error.
- **Common Path Analysis:** The only router node shared across all failing `bgp_session` nodes is the RR. Suppress individual session alerts; issue one RR root-cause alert.
- **Conclusion:** Control Plane failure — RR process crash or FRR daemon failure.

```sql
-- Confirm the RR as the common cause: show the anomalous RR router node alongside
-- the count of bgp_sessions that share it as their parent.
SELECT
  r.name                                                                   AS rr_router,
  r.role,
  e_router.hetgnn_score                                                    AS rr_anomaly_score,
  JSON_VALUE(e_router.anomaly_explanation, '$.top_features[0].feature')   AS rr_top_feature,
  COUNT(DISTINCT e_bgp.node_id)                                            AS affected_session_count
FROM NodeEmbedding  e_router
JOIN PhysicalRouter r       ON r.id          = e_router.node_id AND r.valid_end_ts IS NULL
JOIN VRF            v       ON v.router_id   = r.id             AND v.valid_end_ts IS NULL
JOIN BGPSession     s       ON s.vrf_id      = v.id             AND s.valid_end_ts IS NULL
JOIN NodeEmbedding  e_bgp  ON e_bgp.node_id  = s.id
WHERE e_router.node_type    = 'router'
  AND e_router.hetgnn_score > @threshold
  AND e_router.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND e_bgp.node_type       = 'bgp_session'
  AND e_bgp.hetgnn_score    > @threshold
  AND e_bgp.timestamp       = e_router.timestamp
  AND r.role                = 'RR'
GROUP BY r.name, r.role, rr_anomaly_score, rr_top_feature
ORDER BY affected_session_count DESC;
-- High affected_session_count from a single RR = confirmed RR crash root cause
```

#### VRF / Route-Target Misconfiguration

**Symptom:** Specific VPN traffic fails while physical links and BGP sessions remain up. Routes present in global table but not imported into VRF.

- **Signal:** `rt_import_hash` or `rt_export_hash` deviates from trained baseline on a `vrf` node; `vrf_route_count` drops while `vrf_active_sessions` remains stable.
- **GNN Observation:** High `vrf` reconstruction error; `bgp_session`, `interface`, and `router` layers healthy. Associated `flow` nodes anomalous with zero throughput.
- **Conclusion:** VPN Layer — RT policy change or misconfiguration. The affected VRF (and its PE router) is the root cause.

```sql
-- Identify VRF nodes with anomalous RT hash (policy change)
SELECT
  v.name                                                                          AS vrf_name,
  r.name                                                                          AS router_name,
  r.role,
  e.hetgnn_score,
  JSON_VALUE(e.anomaly_explanation, '$.top_features[0].feature')                 AS top_feature,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.top_features[0].mse') AS FLOAT64)    AS top_mse
FROM NodeEmbedding  e
JOIN VRF            v ON v.id        = e.node_id AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON r.id        = v.router_id AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'vrf'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY e.hetgnn_score DESC
LIMIT 10;
-- top_feature = 'rt_import_hash' or 'rt_export_hash' → RT policy change
-- top_feature = 'vrf_route_count'                    → routes withdrawn from VPN
```
