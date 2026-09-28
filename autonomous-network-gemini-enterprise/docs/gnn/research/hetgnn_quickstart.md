# HetGNN Quickstart: Minimal Training with Spanner

This document describes the minimal viable HetGNN configuration to validate the concept end-to-end using live data from Spanner. Start here before adding the richer feature set described in `hetgnn_rca_spanner.md`.

**Goal**: Get the model training, producing meaningful reconstruction loss curves, and writing `hetgnn_score` values to `NodeEmbedding` — with the fewest possible moving parts.

---

## 1. Actual Graph Structure (as implemented)

The `SpannerDataset` + `GraphBuilder` in `domains/transport/gnn/src/utils/` produce the following schema. Note that routers are **split into three separate node types** by role — this gives the HetGNN richer heterogeneity than a single `Router` type and is the correct structure to use.

### Node Types

| Node Type | Spanner Table | Features | # Features |
| :--- | :--- | :--- | :--- |
| `PE Router` | `PhysicalRouter` (role=PE) | `state` (1.0=Running) | 1 |
| `P Router` | `PhysicalRouter` (role=P) | `state` | 1 |
| `CE Router` | `PhysicalRouter` (role=CE) | `state` | 1 |
| `Router_Config` | `PhysicalRouter.config` | 132-dim hash embedding (128 buckets + 4 RT dims) | 132 |
| `Protocol_State` | `PhysicalRouter.config` | ospf_neighbor_count, bgp_peer_count, mpls_route_count | 3 |
| `Interface` | `PhysicalInterface` + `NetworkMetrics` | state + rx/tx bytes/drops/errors (log-scaled) | 7 |
| `Interface_Metrics` | `NetworkMetrics` | 6 raw metrics + 6 velocity (delta) metrics | 12 |
| `BGP_Session` | `BGPSession` | `state` (1.0=Established) | 1 |

### Edge Types

| Edge | Direction | Source |
| :--- | :--- | :--- |
| `Owns` | {PE/P/CE} Router → Interface | `PhysicalInterface.router_id` |
| `Connected` | Interface ↔ Interface | `Interface_Link` join table |
| `Has_Config` | {PE/P/CE} Router → Router_Config | Derived (synthetic sub-node) |
| `Has_Protocol` | {PE/P/CE} Router → Protocol_State | Derived (synthetic sub-node) |
| `Has_Metrics` | Interface → Interface_Metrics | Derived (synthetic sub-node) |
| `PeersWith` | BGP_Session ↔ BGP_Session | `BGP_Peering` table |

> **Sub-nodes** (`Router_Config`, `Protocol_State`, `Interface_Metrics`) are synthetic HetGNN nodes created by `GraphBuilder` to decompose a router/interface into its structural, protocol, and metric aspects. This is the key architectural innovation — the GNN can learn to weight these three channels independently per node.

---

## 2. Data Pipeline

All Spanner queries are handled by `domains/transport/gnn/src/utils/data.py` (`SpannerDataset.fetch_snapshot()`). You do **not** need to write SQL. The steps executed per snapshot are:

1. Query `PhysicalRouter` → PE/P/CE Router nodes
2. Query `PhysicalInterface` → Interface nodes (metrics initially zero)
3. Query `BGPSession` → BGP_Session nodes
4. Derive `Owns` edges from `PhysicalInterface.router_id`
5. Query `Interface_Link` → `Connected` edges
6. Query `BGP_Peering` → `PeersWith` edges
7. Query `NetworkMetrics` (Prometheus window) → fill Interface rx/tx/drops/errors

Smoke-test the pipeline directly against live Spanner:

```bash
cd gnn/src
SPANNER_INSTANCE=networktopology-instance \
SPANNER_DATABASE=networktopology-db \
GOOGLE_CLOUD_PROJECT=your-project \
python -m utils.data
```

This runs the `__main__` block and prints per-snapshot node counts, edge counts, BGP session state, and interface metrics for every snapshot in the window.

---

## 3. Layers and Weights — Best Practices

### Number of Layers (`NUM_LAYERS`)

**Current default: `NUM_LAYERS = 2`** — correct for this topology.

Each HetGNN layer aggregates one additional hop of neighborhood information. The meaningful path lengths in this network are:

| Scenario | Path | Hops |
| :--- | :--- | :--- |
| Interface fault → router | Interface → PE Router | 1 |
| Link fault | Interface → Interface | 1 |
| Route leak (PE→P→PE) | PE Router → Interface → Interface → P Router | 2–3 |
| BGP peer cascade | BGP_Session → BGP_Session | 1 |
| MTU mismatch (CE→P) | CE Router → Interface → Interface → P Router | 2 |

With `NUM_LAYERS = 2`, the model can see all 1- and 2-hop neighbors. That covers the majority of RCA scenarios.

**When to increase to `NUM_LAYERS = 3`:**
- Route reflector (RR) cascades where faults propagate through 3+ routers
- Hub-and-spoke topologies where CE → Hub PE → Core P is a 3-hop path
- Watch for **over-smoothing**: if validation loss stops improving but train loss is still dropping, reduce layers first

**Never use more than 3 layers** for this graph size. The node embeddings converge (all nodes become similar) and the anomaly signal disappears.

```python
# gnn/src/utils/gnn_utils.py
NUM_LAYERS = 2   # Default — correct for most scenarios
# NUM_LAYERS = 3  # Only if RR cascade detection is a priority
```

### Hidden Channels (`HIDDEN_CHANNELS`)

**Current default: `HIDDEN_CHANNELS = 64`** — good for this graph size.

| Graph Size | Recommended HIDDEN_CHANNELS |
| :--- | :--- |
| < 50 nodes | 32 |
| 50–500 nodes | 64 ← current |
| 500–5000 nodes | 128 |
| > 5000 nodes | 256 |

The lab topology (5–10 routers, 20–40 interfaces) is well within the 50–500 range. `HIDDEN_CHANNELS = 64` gives sufficient representational capacity without overfitting.

> **Rule of thumb**: `HIDDEN_CHANNELS` should be at least 4× the largest input feature dimension (here: `Router_Config` at 132 dims → 64 is slightly under, but the hash embedding is sparse so 64 works well in practice). If the Router reconstruction branch has high loss that won't decrease, try `HIDDEN_CHANNELS = 128`.

### Loss Weights (α, β, γ)

The HetGNN loss is a weighted sum of reconstruction errors across the three semantic branches. The weights control which branch the model prioritizes:

```
Total Loss = α · Loss_config  +  β · Loss_protocol  +  γ · Loss_metrics  +  δ · Loss_diversity
```

| Weight | Branch | Controls |
| :--- | :--- | :--- |
| `α` (alpha) | Router_Config reconstruction | Config-diff anomalies (route-target misconfiguration, route-map errors) |
| `β` (beta) | Protocol_State + BGP_Session reconstruction | Protocol anomalies (BGP flaps, OSPF neighbor loss, MPLS config issues) |
| `γ` (gamma) | Interface + Interface_Metrics reconstruction | Traffic anomalies (drops, errors, asymmetry, link flaps) |
| `δ` (diversity) | Embedding variance penalty | Prevents representation collapse (all nodes getting same embedding) |

**Recommended starting weights:**

```python
# Concept validation (equal signal from all branches)
ALPHA            = 0.33   # Config branch
BETA             = 0.33   # Protocol / BGP branch
GAMMA            = 0.33   # Interface metrics branch
DIVERSITY_WEIGHT = 0.1    # Collapse prevention

# Production (interface metrics dominate — Prometheus data is richest signal)
ALPHA            = 0.25
BETA             = 0.30
GAMMA            = 0.45
DIVERSITY_WEIGHT = 0.1
```

**When to adjust weights:**

- If `Loss_config` is always near zero but `Loss_metrics` is large → the model is ignoring the config branch. Increase `α` to `0.4`.
- If all node scores are nearly identical after inference → `DIVERSITY_WEIGHT` is too low. Increase to `0.15`.
- If BGP fault injection doesn't produce high scores for the downed session → `β` is too low. Increase to `0.4`.
- After adding VRF nodes (iteration v2), increase `β` since the protocol branch will have much richer structure.

### Attention Heads (`NUM_HEADS`)

**Current default: `NUM_HEADS = 4`**

`NUM_HEADS` only applies to `GATConv`-based variants. The current `HetGNN` uses `SAGEConv`, so this constant has no effect on training — it's present for future `HetGAT` experiments. Keep it at `4`.

---

## 4. Model Configuration (Minimal)

```python
# gnn/src/utils/gnn_utils.py — current defaults
HIDDEN_CHANNELS  = 64    # Embedding size per SAGEConv layer
OUT_CHANNELS     = 32    # Final projection dimension (unused by current decoder)
NUM_LAYERS       = 2     # Neighborhood aggregation depth
NUM_HEADS        = 4     # Reserved for future HetGAT experiments

# Node types — must match GraphBuilder.global_id_map keys
node_types = [
    "PE Router", "P Router", "CE Router",
    "Router_Config", "Protocol_State",
    "Interface", "Interface_Metrics",
    "BGP_Session",
]

# Edge types
edge_types = [
    ("PE Router",   "Owns",         "Interface"),
    ("P Router",    "Owns",         "Interface"),
    ("CE Router",   "Owns",         "Interface"),
    ("Interface",   "Connected",    "Interface"),
    ("PE Router",   "Has_Config",   "Router_Config"),
    ("P Router",    "Has_Config",   "Router_Config"),
    ("CE Router",   "Has_Config",   "Router_Config"),
    ("PE Router",   "Has_Protocol", "Protocol_State"),
    ("P Router",    "Has_Protocol", "Protocol_State"),
    ("CE Router",   "Has_Protocol", "Protocol_State"),
    ("Interface",   "Has_Metrics",  "Interface_Metrics"),
    ("BGP_Session", "PeersWith",    "BGP_Session"),
]
```

---

## 5. RCA Showcase — Fault Injection Scenarios

These correspond to the fault files in `examples/controller/`. Each scenario exercises a specific combination of node types and branches.

### Fault 1: MTU Mismatch (`l3vpn-hub-spoke-fault1-mtu.yaml`)

**What happens**: MTU is set to a non-standard value on one side of a link.

**Expected GNN signal**:
- `Interface_Metrics` branch: rising `rx_drops_velocity` and `tx_drops_velocity` on both sides of the affected link
- `Interface` nodes on the mismatched link will have the highest reconstruction error
- `Interface → Connected → Interface` 2-hop path will propagate the anomaly signal to the owning router

**Diagnosis indicator**: Two `Interface` nodes with high `hetgnn_score`, both owned by routers on the same physical link.

---

### Fault 2: CE Router Down (`l3vpn-hub-spoke-fault2-ce-down.yaml`)

**What happens**: A CE router is taken offline.

**Expected GNN signal**:
- `CE Router` base node: `state` flips to `0.0` → high reconstruction error in config branch
- `Interface` nodes owned by CE: `state` flips to `0.0`
- `BGP_Session` nodes for CE: `state` flips to `0.0` (Idle/Down) → `PeersWith` edges now carry zero-state on both sides
- All three branches fire simultaneously — this is the clearest, highest-confidence RCA case

**Diagnosis indicator**: One `CE Router` + all its `Interface` and `BGP_Session` nodes cluster with the highest scores.

---

### Fault 3: Route Reflector Crash (`l3vpn-hub-spoke-fault3-rr1-crash.yaml`)

**What happens**: The RR (route reflector) P router crashes, causing BGP route withdrawal across multiple PE routers.

**Expected GNN signal**:
- `P Router` base node: `state` → `0.0`
- `Protocol_State` sub-node for RR: `bgp_peer_count` drops sharply
- Multiple `BGP_Session` nodes (all RR clients) flip to `Idle` → cascade through `PeersWith` edges
- `NUM_LAYERS = 2` is the minimum to capture the cascade: BGP_Session → PeersWith → BGP_Session (1 hop) + BGP_Session → (owned by) PE Router (2 hops)

**Diagnosis indicator**: Clustered `BGP_Session` anomalies without a corresponding single-router failure — points to control-plane issue rather than physical fault.

---

### Fault 4: RT Import Misconfiguration (`l3vpn-hub-spoke-fault4-rt-import.yaml`)

**What happens**: A route-target import value is wrong on one PE, causing VPN routes to not be imported.

**Expected GNN signal**:
- `Router_Config` sub-node for the affected PE: the `vrf_rt_import` hash bucket changes → reconstruction error in config branch
- Explicit RT features (embedding dims 128–131) will be non-zero and different from normal → high config branch loss
- `Protocol_State`: `bgp_peer_count` may drop if peers withdraw
- Traffic metrics: packets drop at the PE's interfaces (no route → drop) → `Interface_Metrics` anomaly

**Diagnosis indicator**: `Router_Config` reconstruction error is highest for exactly one PE router, with matching drop increase on its interfaces. This is the case most dependent on the config embedding — a good test of the hash-embedding approach.

---

### Fault 5: SFP / Physical Failure (`l3vpn-hub-spoke-fault5-sfp.yaml`)

**What happens**: A transceiver/SFP fails, taking down a physical interface.

**Expected GNN signal**:
- `Interface` node: `state` → `0.0`, `rx_errors_velocity` and `tx_errors_velocity` spike before full failure
- `Interface_Metrics` sub-node: all 6 velocity metrics simultaneously approach zero (counters stop incrementing)
- Owning router's `Protocol_State`: OSPF neighbor count may drop if the downed link carried OSPF adjacency

**Diagnosis indicator**: A single `Interface` node with both status=0 and counter-velocity approaching zero is a physical-layer (L1) fault, not a software misconfiguration.

---

## 6. What "Working" Looks Like

A successful concept validation shows:

1. **Reconstruction loss decreases** — both train and validation loss should drop steadily over 10–20 epochs. If they don't move, check that scalers are being fit correctly and that the first forward pass initializes weights (log the `Input dimensions` line from `gnn_utils.py`).

2. **Branch losses are non-zero** — `Loss_config` (Router_Config reconstruction) and `Loss_protocol` (BGP_Session reconstruction) must both be > 0. If one is always 0, that node type has no features or its reconstruction isn't being computed.

3. **`hetgnn_score` variance in NodeEmbedding** — after writing inference results to Spanner, the scores across routers should not all be identical. If they are, representation collapse has occurred; increase `DIVERSITY_WEIGHT` to `0.15`.

4. **Fault injection test** — apply `examples/failure_tests/transport/default/link-down-bridge.yaml` (CE router down) and run inference. The downed router's `Interface.state` will flip to `0.0` and its BGP sessions will go `Idle`. The `hetgnn_score` for `router:ce1-hub` and its interfaces should be the highest scores in the graph.

---

## 7. Iteration Path

Once the minimal graph is working, add features in this order:

| Iteration | What to Add | Why |
| :--- | :--- | :--- |
| **v1 (this doc)** | Split-role Routers + sub-nodes, Interface metrics, BGP status | Prove end-to-end pipeline works |
| **v2** | Add `VRF` node + `BelongsToVRF` (Router→VRF) + `LocatedOn` (BGP_Session→VRF) edges | Connect BGP↔Router subgraphs; enable cross-branch RCA for Fault 4 |
| **v3** | Add `rx_errors`, `tx_errors`, `tx_rx_asymmetry` edge attributes | Detect silent drops and SFP degradation before full failure (Fault 5) |
| **v4** | Add `Subnet` node + `AssociatedWith` edge + `mtu_normalized` | Enable MTU mismatch detection (Fault 1) at subnet level |
| **v5** | Add `BGP_Session.flaps`, `VRF.status`, router `location` | Flap detection, provisioning faults, geo-clustering |
| **v6** | Add `L3VPNService` node + `ServicePerformance` features | Customer-impact signal; hub/spoke topology awareness |

Each iteration should result in a measurable improvement in anomaly detection F1-score when tested against the fault injection scenarios in `examples/controller/`.
