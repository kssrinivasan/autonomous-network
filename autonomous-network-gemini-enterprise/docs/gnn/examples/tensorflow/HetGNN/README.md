# HetGNN Example — Typed Failure Pinpointing

Minimal, self-contained Heterogeneous GNN (HetGNN) autoencoder
demonstrating **component-layer failure diagnosis** on the hub-and-spoke
topology.

```bash
pip install tensorflow numpy
python simple_hetgnn_pinpointing.py
```

---

## What this adds over the GCN example

| | GCN (`GCN/`) | HetGNN (`HetGNN/`) |
|---|---|---|
| Node types | 1 (everything is a "node") | 3 — Router, Interface, BGPSession |
| Feature sets | 1 shared vector per node | Separate features per type |
| Weight matrices | Shared W for all nodes | Separate W per node type AND edge type |
| Anomaly output | "Node X is anomalous" | **"The Interface branch is anomalous"** |
| Fault diagnosis | Detects an anomaly | Names the fault layer (physical / protocol / config) |

The key output is the **branch-level anomaly score**:

```
Branch-level diagnosis:
  Router    (cpu/mem/ospf)      0.00001   ✓ normal
  Interface (drops/mtu)         0.18260   ⚠️  FAULT LAYER   ← Fault 1
  BGPSession (state/prefixes)   0.00001   ✓ normal

→ Root layer: Interface (drops/mtu)
  Check MTU and TX drops on pe1_eth1.
```

---

## Topology

A focused slice of the hub-and-spoke lab around the PE1↔P1 fault link:

```
  [Router: pe1] ──HasInterface──▶ [Interface: pe1_eth1]
  [Router: p1 ] ──HasInterface──▶ [Interface: p1_eth3 ]
                                        ↕  ConnectsTo
  [Router: pe1] ──HasBGP──────▶  [BGPSession: pe1_bgp]
```

**Node types and their features:**

| Type | Nodes | Features |
|------|-------|---------|
| Router | pe1, p1 | cpu_percent, mem_percent, ospf_state |
| Interface | pe1_eth1, p1_eth3 | tx_drops_rate, rx_drops_rate, mtu_norm |
| BGPSession | pe1_bgp | bgp_state, pfx_count_norm, uptime_norm |

**Edge types (incidence matrices):**

| Edge type | Relation | Matrix |
|-----------|---------|--------|
| HasInterface | Router → Interface | P_RI [N_I, N_R] |
| ConnectsTo | Interface ↔ Interface | P_II [N_I, N_I] |
| HasBGP | Router → BGPSession | P_RB [N_B, N_R] |

---

## Architecture

```
X_R [N_R, 3] ──W_proj_R──▶ h_R [N_R, H]
                                   │
                     W_msg_RI ─────┤
                     W_msg_RB ─────┤
                                   │
X_I [N_I, 3] ──W_proj_I──▶ h_I ◀──┤──W_msg_II (peer)
                              │    │
                         W_upd_I   │
                              │    │
                         h_I_new   │
                              │    │
X_B [N_B, 3] ──W_proj_B──▶ h_B ◀──┘──W_upd_B
                              │
                         h_B_new

Decoders (one per type):
  h_R     @ W_dec_R  → X_R_hat  [N_R, 3]
  h_I_new @ W_dec_I  → X_I_hat  [N_I, 3]
  h_B_new @ W_dec_B  → X_B_hat  [N_B, 3]

Loss = (MSE_R + MSE_I + MSE_B) / 3
```

Every `W_*` above is a **separate, independent** weight matrix. Nothing is
shared between node types or edge types — that is what makes this
heterogeneous.

---

## Faults Demonstrated

### Fault 1 — MTU mismatch on pe1_eth1 (Interface branch fault)

From the research doc: PE1 `eth1` MTU set to 1400, P1 `eth3` stays at 1500.
Large packets silently dropped. BGP and OSPF control plane stays UP.

Changed features:
- `pe1_eth1.tx_drops_rate` → 0.75 (spike)
- `pe1_eth1.mtu_norm` → 0.156 (was 0.167)

Expected diagnosis: **Interface branch scores highest** → physical/config layer.

---

### Fault 2 — BGP session teardown on pe1_bgp (BGP branch fault)

Adapted from Fault 2 in the research doc. The pe1↔RR1 BGP session goes down.
Router health and interface metrics remain unchanged.

Changed features:
- `pe1_bgp.bgp_state` → 0.0 (Down)
- `pe1_bgp.pfx_count_norm` → 0.0 (zero prefixes)
- `pe1_bgp.uptime_norm` → 0.0 (timer reset)

Expected diagnosis: **BGP branch scores highest** → protocol layer.

---

## Expected Output

```
==============================================================
  HetGNN Failure Pinpointing — Simple TensorFlow Example
  Topology: PE1↔P1 link (hub-and-spoke lab)
  Node types: Router | Interface | BGPSession
==============================================================
...training...

[4/5] FAULT 1 — MTU mismatch on pe1_eth1 (Interface layer fault)

  Branch-level diagnosis:
    Router    (cpu/mem/ospf)       0.00001
    Interface (drops/mtu)          0.18xxx   ⚠️  FAULT LAYER
    BGPSession (state/prefixes)    0.00001

  → Root layer: Interface (drops/mtu)

[5/5] FAULT 2 — pe1_bgp session teardown (Protocol layer fault)

  Branch-level diagnosis:
    Router    (cpu/mem/ospf)       0.00001
    Interface (drops/mtu)          0.00001
    BGPSession (state/prefixes)    0.63xxx   ⚠️  FAULT LAYER

  → Root layer: BGPSession (state/prefixes)
```

---

## What's Next

| Step | Adds | Why |
|------|------|-----|
| Add `Config` node type | Separate MTU misconfiguration from TX drop hardware fault | Currently both appear in the Interface branch |
| Add `OSPF_Adjacency` node type | Distinguish OSPF from BGP protocol faults | Currently protocol branch is just BGP |
| Directionality (D-GAT) | Asymmetric attention on ConnectsTo edges | Detect one-way drop (MTU only drops in one direction) |
| Temporal backbone (STGNN) | Track branch anomaly score trajectory over time | Slow degradation detection |
| Spanner integration | Replace synthetic features with real NetworkMetrics rows | Production use |

The production PyTorch HetGNN implementation is at `domains/transport/gnn/src/model/hetgnn.py`.
