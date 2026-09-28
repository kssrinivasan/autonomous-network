# HetGNN Maths Walkthrough — Worked Example

A step-by-step walk through every equation in `simple_hetgnn_pinpointing.py`
using concrete numbers.

We use the same **3-node slice** as the GCN walkthrough, but now we split it
into **three distinct node types**:

```
  [Router: pe1] ──HasInterface──> [Interface: pe1_eth1]
  [Router: p1 ] ──HasInterface──> [Interface: p1_eth3 ]
                                        ↕  ConnectsTo
  [Router: pe1] ──HasBGP──────> [BGPSession: pe1_bgp ]
```

| Type | Nodes | Features |
|------|-------|---------|
| Router | pe1, p1 | cpu_percent, mem_percent, ospf_state |
| Interface | pe1_eth1, p1_eth3 | tx_drops_rate, rx_drops_rate, mtu_norm |
| BGPSession | pe1_bgp | bgp_state, pfx_count_norm, uptime_norm |

---

## Why Heterogeneous? The Core Motivation

In the GCN example every node uses the **same** feature vector and the same
weight matrix W. This collapses `cpu_percent` (a router metric) and
`tx_drops_rate` (an interface metric) into a single undifferentiated pool.
The model cannot tell whether a spike in reconstruction error came from a
router config problem or an interface hardware problem.

A HetGNN fixes this by giving each node type its **own** weight matrices:
- `W_proj_R`, `W_msg_*→R`, `W_dec_R` — exclusively for Router nodes
- `W_proj_I`, `W_msg_*→I`, `W_dec_I` — exclusively for Interface nodes
- `W_proj_B`, `W_msg_*→B`, `W_dec_B` — exclusively for BGPSession nodes

When the model produces a high reconstruction error on the Interface branch
but not on the BGP or Router branch, it is directly naming the fault layer.
This is the **"Component Level Decomposition"** described in the research doc.

---

## Step 1 — Typed Feature Matrices

Instead of one feature matrix X, we have one per type:

**Router features X_R  [N_R=2, F_R=3]** — healthy state:
```
           cpu   mem   ospf
pe1   →  [ 0.22  0.30  1.0 ]
p1    →  [ 0.20  0.30  1.0 ]
```

**Interface features X_I  [N_I=2, F_I=3]** — healthy state:
```
              txd   rxd   mtu
pe1_eth1  →  [ 0.01  0.01  0.167 ]
p1_eth3   →  [ 0.01  0.01  0.167 ]
```

**BGP features X_B  [N_B=1, F_B=3]** — healthy state:
```
             bgp   pfx   upt
pe1_bgp  →  [ 1.0  0.50  0.80 ]
```

These three matrices are completely separate inputs — they are never concatenated
or mixed before they reach their respective typed weight matrices.

---

## Step 2 — Incidence Matrices (the heterogeneous graph structure)

In the GCN we had one adjacency matrix A_hat encoding all connections.
In a HetGNN we have **one incidence matrix per edge type**. Each encodes
one specific relationship between two specific node types.

Convention: `P[dst_idx, src_idx] = 1` means "dst receives a message from src".

### P_RI — HasInterface  [N_I=2, N_R=2]
Which router owns which interface?
```
             pe1   p1
pe1_eth1 →  [ 1    0 ]   pe1_eth1 ← pe1
p1_eth3  →  [ 0    1 ]   p1_eth3  ← p1
```

### P_II — ConnectsTo  [N_I=2, N_I=2]
Which interface connects to which other interface (the physical p2p link)?
```
              pe1_eth1  p1_eth3
pe1_eth1  →  [   0        1   ]   pe1_eth1 ← p1_eth3
p1_eth3   →  [   1        0   ]   p1_eth3  ← pe1_eth1
```

### P_RB — HasBGP  [N_B=1, N_R=2]
Which router hosts which BGP session?
```
             pe1   p1
pe1_bgp  →  [ 1    0 ]   pe1_bgp ← pe1
```

Notice: there is **no single A_hat** that combines all of these.
Each relationship type is kept separate so it can carry a different
learned message weight.

---

## Step 3 — Typed Projections (Encoder)

Before any messages are passed, each node type projects its raw features
into a shared hidden space of dimension H. We use H=4 here (H=16 in the script).

Each type has its own weight matrix:

```
W_proj_R  [F_R=3, H=4]   — for routers
W_proj_I  [F_I=3, H=4]   — for interfaces
W_proj_B  [F_B=3, H=4]   — for BGP sessions
```

### Compute h_R = ReLU(X_R @ W_proj_R)

Example W_proj_R (after training on healthy data):
```
         h0    h1    h2    h3
cpu    [ 0.80  0.20 -0.10  0.50 ]
mem    [ 0.30  0.60  0.10  0.40 ]
ospf   [ 0.10  0.90  0.05  0.20 ]
```

For pe1 (cpu=0.22, mem=0.30, ospf=1.0):
```
h0: 0.22×0.80 + 0.30×0.30 + 1.0×0.10 = 0.176 + 0.090 + 0.100 = 0.366
h1: 0.22×0.20 + 0.30×0.60 + 1.0×0.90 = 0.044 + 0.180 + 0.900 = 1.124
h2: 0.22×(-0.10) + 0.30×0.10 + 1.0×0.05 = -0.022+0.030+0.050 = 0.058
h3: 0.22×0.50 + 0.30×0.40 + 1.0×0.20 = 0.110 + 0.120 + 0.200 = 0.430
```

After ReLU (no negatives here, so unchanged):
```
h_R[pe1] = [0.366, 1.124, 0.058, 0.430]
h_R[p1]  = [0.360, 1.100, 0.058, 0.420]   (similar because p1 ≈ pe1 baseline)
```

### Compute h_I = ReLU(X_I @ W_proj_I)

W_proj_I is a **different** matrix — interfaces have different features,
so they need different learned projections.

Example W_proj_I:
```
         h0    h1    h2    h3
txd    [ 1.20  0.10 -0.30  0.20 ]
rxd    [ 1.10  0.15 -0.25  0.20 ]
mtu    [ 0.05  0.80  0.10  0.70 ]
```

For pe1_eth1 (txd=0.01, rxd=0.01, mtu=0.167):
```
h0: 0.01×1.20 + 0.01×1.10 + 0.167×0.05 = 0.012 + 0.011 + 0.008 = 0.031
h1: 0.01×0.10 + 0.01×0.15 + 0.167×0.80 = 0.001 + 0.002 + 0.134 = 0.137
h2: 0.01×(-0.30) + 0.01×(-0.25) + 0.167×0.10 = -0.003-0.003+0.017 = 0.011 → after ReLU: 0.011
h3: 0.01×0.20 + 0.01×0.20 + 0.167×0.70 = 0.002 + 0.002 + 0.117 = 0.121
```

```
h_I[pe1_eth1] = [0.031, 0.137, 0.011, 0.121]
h_I[p1_eth3]  = [0.031, 0.137, 0.011, 0.121]   (same features → same projection)
```

### Compute h_B = ReLU(X_B @ W_proj_B)

For pe1_bgp (bgp=1.0, pfx=0.50, upt=0.80), with W_proj_B:
```
h_B[pe1_bgp] = [0.92, 0.74, 0.00, 0.68]   (example)
```

---

## Step 4 — Typed Message Passing

This is the core of the HetGNN. Each edge type uses its own weight matrix to
transform the source node embeddings before they are aggregated at the destination.

### 4a — HasInterface message: Router → Interface

The W_msg_RI weight matrix transforms router embeddings into messages
that are meaningful to interface nodes. Think: "what does a router want
to tell its interfaces about itself?"

```
m_RI = P_RI @ (h_R @ W_msg_RI)
```

First apply W_msg_RI [H=4, H=4] to h_R:

```
h_R @ W_msg_RI:
  pe1: h_R[pe1] × W_msg_RI → msg_pe1 = [0.41, 0.28, 0.15, 0.35]  (example)
  p1:  h_R[p1]  × W_msg_RI → msg_p1  = [0.40, 0.27, 0.15, 0.34]
```

Now apply P_RI to aggregate at each interface:
```
         ⎡ 1  0 ⎤   ⎡ 0.41  0.28  0.15  0.35 ⎤
P_RI  =  ⎢ 0  1 ⎥ × ⎢ 0.40  0.27  0.15  0.34 ⎥
         ⎣     ⎦   ⎣                          ⎦

m_RI[pe1_eth1] = 1×msg_pe1 + 0×msg_p1 = [0.41, 0.28, 0.15, 0.35]
m_RI[p1_eth3]  = 0×msg_pe1 + 1×msg_p1 = [0.40, 0.27, 0.15, 0.34]
```

Each interface receives the message from **its own parent router only** —
pe1_eth1 gets pe1's message, p1_eth3 gets p1's message. P_RI enforces this.

---

### 4b — ConnectsTo message: Interface → Interface

This captures what each interface "hears" from the interface on the other end
of the physical link. In the MTU fault, pe1_eth1 will hear about p1_eth3's
normal MTU, creating a contrast that amplifies the anomaly signal.

```
m_II = P_II @ (h_I @ W_msg_II)
```

Apply W_msg_II to h_I (different weights — peer-to-peer messages carry
different semantics than parent-to-child router→interface messages):

```
h_I @ W_msg_II:
  pe1_eth1: [0.031, 0.137, 0.011, 0.121] × W_msg_II → peer_pe1 = [0.12, 0.09, 0.10, 0.11]
  p1_eth3:  [0.031, 0.137, 0.011, 0.121] × W_msg_II → peer_p1  = [0.12, 0.09, 0.10, 0.11]
```

Apply P_II (cross-peer aggregation):
```
         ⎡ 0  1 ⎤   ⎡ peer_pe1 ⎤
P_II  =  ⎢ 1  0 ⎥ × ⎢ peer_p1  ⎥
         ⎣     ⎦   ⎣          ⎦

m_II[pe1_eth1] = 0×peer_pe1 + 1×peer_p1  = [0.12, 0.09, 0.10, 0.11]
m_II[p1_eth3]  = 1×peer_pe1 + 0×peer_p1  = [0.12, 0.09, 0.10, 0.11]
```

In the healthy case, peer messages are identical because both interfaces
are identical. In the fault case, they will differ — see Step 8.

---

### 4c — HasBGP message: Router → BGPSession

The router sends a message to its BGP session node.

```
m_RB = P_RB @ (h_R @ W_msg_RB)
```

```
m_RB[pe1_bgp] = 1×msg_pe1_rb + 0×msg_p1_rb = [0.38, 0.25, 0.14, 0.33]  (example)
```

---

## Step 5 — Typed Updates

After collecting all incoming messages, each node type combines them with
its own self-representation and applies a final typed update transformation.

### Interface update

Interfaces receive two message types:
1. `m_RI` — from their parent router (context: how is my router doing?)
2. `m_II` — from their peer interface across the link (context: how is the other end?)

```
h_I_new = ReLU( (h_I + m_RI + m_II) @ W_upd_I )
```

For pe1_eth1 (healthy):
```
h_I[pe1_eth1]   = [0.031, 0.137, 0.011, 0.121]
m_RI[pe1_eth1]  = [0.410, 0.280, 0.150, 0.350]   ← from pe1 router
m_II[pe1_eth1]  = [0.120, 0.090, 0.100, 0.110]   ← from p1_eth3 peer

sum             = [0.561, 0.507, 0.261, 0.581]

After W_upd_I and ReLU:
h_I_new[pe1_eth1] = [0.48, 0.62, 0.00, 0.55]   (example)
```

### BGP update

BGP sessions receive one message type:
1. `m_RB` — from their parent router

```
h_B_new = ReLU( (h_B + m_RB) @ W_upd_B )

h_B[pe1_bgp]  = [0.92, 0.74, 0.00, 0.68]
m_RB[pe1_bgp] = [0.38, 0.25, 0.14, 0.33]

sum           = [1.30, 0.99, 0.14, 1.01]

After W_upd_B and ReLU:
h_B_new[pe1_bgp] = [0.71, 0.88, 0.12, 0.75]   (example)
```

### Router update

In this example, no edges point inward toward routers, so routers keep
their projected embedding unchanged:

```
h_R_new = h_R
```

---

## Step 6 — Typed Decoders

Each branch has its own decoder weight matrix that maps the updated embedding
back to that type's original feature space.

```
X_R_hat = h_R_new @ W_dec_R    [N_R, H] @ [H, F_R] → [N_R, 3]
X_I_hat = h_I_new @ W_dec_I    [N_I, H] @ [H, F_I] → [N_I, 3]
X_B_hat = h_B_new @ W_dec_B    [N_B, H] @ [H, F_B] → [N_B, 3]
```

After training on healthy data, each decoder learns to reproduce the healthy
baseline for its node type. For a healthy snapshot:

```
X_R_hat ≈ [[0.22, 0.30, 1.0],   # pe1 reconstructed
            [0.20, 0.30, 1.0]]   # p1  reconstructed

X_I_hat ≈ [[0.01, 0.01, 0.167], # pe1_eth1 reconstructed
            [0.01, 0.01, 0.167]] # p1_eth3  reconstructed

X_B_hat ≈ [[1.0,  0.50, 0.80]]  # pe1_bgp reconstructed
```

---

## Step 7 — Multi-Task Loss

Three separate MSE losses, one per branch, summed with equal weights:

```
Loss_R = MSE(X_R_hat, X_R) = (1/N_R×F_R) × Σ(X_R_hat - X_R)²
Loss_I = MSE(X_I_hat, X_I) = (1/N_I×F_I) × Σ(X_I_hat - X_I)²
Loss_B = MSE(X_B_hat, X_B) = (1/N_B×F_B) × Σ(X_B_hat - X_B)²

Loss_total = (Loss_R + Loss_I + Loss_B) / 3
```

For a healthy snapshot all three losses are near zero. Gradient descent
trains all three W matrices simultaneously — each branch learns from its
own reconstruction error, so no branch "pollutes" another.

**Why separate losses matter:** If we had one combined feature vector and
one loss, a persistent configuration feature (ospf_state = 1.0) would
dominate gradient updates and crowd out the subtler interface drop signals.
Separate losses give each branch equal learning budget.

---

## Step 8 — Fault 1: MTU Mismatch (Interface Branch Fires)

Inject: pe1_eth1 tx_drops → 0.75, mtu_norm → 0.156 (was 0.167).

### Typed projection (fault)

The Interface projection for pe1_eth1 now receives fault features:

```
h_I_fault[pe1_eth1]:
h0: 0.75×1.20 + 0.01×1.10 + 0.156×0.05 = 0.900+0.011+0.008 = 0.919
h1: 0.75×0.10 + 0.01×0.15 + 0.156×0.80 = 0.075+0.002+0.125 = 0.202
h2: 0.75×(-0.30) + 0.01×(-0.25) + 0.156×0.10 = -0.225-0.003+0.016 = -0.212 → ReLU → 0.000
h3: 0.75×0.20 + 0.01×0.20 + 0.156×0.70 = 0.150+0.002+0.109 = 0.261
```

```
h_I_fault[pe1_eth1] = [0.919, 0.202, 0.000, 0.261]
```

Compare to healthy embedding `[0.031, 0.137, 0.011, 0.121]`:
- h0 jumped from 0.031 → 0.919 (tx_drops spike)
- The embedding is in a completely different region of latent space

### Updated h_I after messages

The ConnectsTo message m_II from p1_eth3 (still healthy) now creates a
contrast at pe1_eth1:

```
m_II[pe1_eth1] = peer_p1_eth3 = [0.12, 0.09, 0.10, 0.11]   (unchanged, p1_eth3 healthy)

sum for pe1_eth1 = h_I_fault + m_RI + m_II
                 = [0.919, 0.202, 0.000, 0.261]   ← fault signal
                 + [0.410, 0.280, 0.150, 0.350]   ← router message (unchanged)
                 + [0.120, 0.090, 0.100, 0.110]   ← peer message (healthy contrast)
                 = [1.449, 0.572, 0.250, 0.721]
```

This sum is far outside anything seen in training. After W_upd_I, the
resulting h_I_new[pe1_eth1] lies in uncharted latent space.

### Decoder failure

The Interface decoder W_dec_I was trained to map healthy embeddings to
healthy features. Faced with the fault embedding, it produces values near
the healthy baseline:

```
X_I_hat[pe1_eth1] ≈ [0.010, 0.010, 0.167]   (what the decoder learned to output)
X_I[pe1_eth1]      = [0.750, 0.010, 0.156]   (actual fault values)
```

### Per-branch anomaly scores

```
Interface branch MSE:
  pe1_eth1: mean((0.750-0.010)², (0.010-0.010)², (0.156-0.167)²)
           = mean(0.5476, 0.0000, 0.0001)
           = 0.1826

Router branch MSE:
  pe1:  mean((0.22-0.22)², (0.30-0.30)², (1.0-1.0)²) ≈ 0.0000
  p1:   ≈ 0.0000

BGP branch MSE:
  pe1_bgp: mean((1.0-1.0)², (0.50-0.50)², (0.80-0.80)²) ≈ 0.0000
```

Branch diagnosis:
```
Router branch     0.0000   ✓ normal
Interface branch  0.1826   ⚠️  FAULT LAYER  ← highest
BGP branch        0.0000   ✓ normal

→ Root layer: Interface (drops/mtu)
  This is a physical/config layer problem — check MTU and TX drops on pe1_eth1.
```

---

## Step 9 — Fault 2: BGP Session Teardown (BGP Branch Fires)

Inject: pe1_bgp bgp_state → 0.0, pfx_count → 0.0, uptime → 0.0.

The Router and Interface features are unchanged — only the BGP node changes.

### BGP projection (fault)

```
h_B_fault[pe1_bgp]:
  Input: [0.0, 0.0, 0.0]   (all features dropped to zero)

  h0: 0.0×... = 0.000
  h1: 0.0×... = 0.000
  h2: 0.0×... = 0.000
  h3: 0.0×... = 0.000

h_B_fault[pe1_bgp] = [0.000, 0.000, 0.000, 0.000]
```

Compare to healthy embedding `[0.92, 0.74, 0.00, 0.68]` — completely zeroed out.

### BGP update

```
h_B_new_fault = ReLU( (h_B_fault + m_RB) @ W_upd_B )
              = ReLU( ([0, 0, 0, 0] + [0.38, 0.25, 0.14, 0.33]) @ W_upd_B )
```

The router message m_RB is unchanged (pe1 router is healthy). But the self
embedding contribution is zero — very different from training where h_B was
`[0.92, 0.74, 0.00, 0.68]`. The combined sum has a completely different ratio
of self vs message that the model has never seen.

### Per-branch anomaly scores

```
BGP branch MSE:
  pe1_bgp: mean((0.0-1.0)², (0.0-0.50)², (0.0-0.80)²)
           = mean(1.0000, 0.2500, 0.6400)
           = 0.6300   ← very HIGH

Router branch MSE:
  pe1, p1: ≈ 0.0000

Interface branch MSE:
  pe1_eth1, p1_eth3: ≈ 0.0000
```

Branch diagnosis:
```
Router branch      0.0000   ✓ normal
Interface branch   0.0000   ✓ normal
BGP branch         0.6300   ⚠️  FAULT LAYER  ← highest

→ Root layer: BGPSession (state/prefixes)
  This is a protocol layer problem — check BGP session state on pe1.
```

---

## Summary: The Two Faults Compared

| | Fault 1 (MTU mismatch) | Fault 2 (BGP teardown) |
|---|---|---|
| **Changed features** | Interface: tx_drops, mtu_norm | BGP: bgp_state, pfx_count, uptime |
| **Router branch score** | ~0.000 | ~0.000 |
| **Interface branch score** | **~0.183** ← highest | ~0.000 |
| **BGP branch score** | ~0.000 | **~0.630** ← highest |
| **Diagnosis** | Physical/config layer | Protocol layer |
| **Human action** | Check MTU on pe1_eth1 | Check BGP session on pe1 |

The GCN in `GCN/simple_failure_pinpointing.py` would have detected *something*
anomalous in both cases, but could not tell you **which layer** the fault
was in. The HetGNN gives you that answer for free from the branch scores,
without any additional analysis or fault labels.

---

## Summary of Every Typed Formula

| Step | Formula | Typed by |
|------|---------|---------|
| 1 | `h_R = ReLU(X_R @ W_proj_R)` | Node type: Router |
| 1 | `h_I = ReLU(X_I @ W_proj_I)` | Node type: Interface |
| 1 | `h_B = ReLU(X_B @ W_proj_B)` | Node type: BGPSession |
| 2 | `m_RI = P_RI @ (h_R @ W_msg_RI)` | Edge type: HasInterface |
| 2 | `m_II = P_II @ (h_I @ W_msg_II)` | Edge type: ConnectsTo |
| 2 | `m_RB = P_RB @ (h_R @ W_msg_RB)` | Edge type: HasBGP |
| 3 | `h_I' = ReLU((h_I + m_RI + m_II) @ W_upd_I)` | Node type: Interface |
| 3 | `h_B' = ReLU((h_B + m_RB) @ W_upd_B)` | Node type: BGPSession |
| 4 | `X_R_hat = h_R' @ W_dec_R` | Node type: Router |
| 4 | `X_I_hat = h_I' @ W_dec_I` | Node type: Interface |
| 4 | `X_B_hat = h_B' @ W_dec_B` | Node type: BGPSession |
| 5 | `Loss = (MSE_R + MSE_I + MSE_B) / 3` | All branches equally |
| 6 | `score_type[i] = mean((X_type_hat[i] - X_type[i])²)` | Per node, per type |

Every weight matrix in this table is **independent**. There are no shared
weights between types. That is the entire point of a Heterogeneous GNN.
