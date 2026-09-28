# HetGNN Maths Walkthrough — PyTorch / torch_geometric

Every arithmetic step shown with concrete numbers.

Code reference: `simple_hetgnn_pinpointing.py`  →  `HetGNNAutoencoder`  
Production reference: `domains/transport/gnn/src/model/hetgnn.py`

---

## Overall pipeline

Before diving in, here is the high-level flow so every later formula has a place to land:

```
Raw features (X_R, X_I, X_B)
        │
        ▼  Part A
Typed linear projections  →  each node type gets its own W_proj that maps
                             its F raw features to H latent dimensions
        │
        ▼  Part B
Heterogeneous message passing (HeteroConv / SAGEConv)
        →  each node absorbs information from its neighbours
           through per-edge-type weight matrices
        │
        ▼  Part C
Per-type decoders  →  each branch tries to reconstruct its own raw features
                      from the latent embedding
        │
        ▼
Reconstruction error per branch  →  the branch with the highest error
                                    is the identified fault layer

The entire model is trained end-to-end on healthy data only (Part D), so it
learns what "normal" looks like and flags anything that deviates.

**Example network** — a small iBGP topology with a route reflector. The ★-marked nodes
are the 5-node subset used for all arithmetic in Parts A–C:

```
  rr1  (route-reflector)
   ├─ospf_peer──▶  pe1 ★
   │                ├─has_bgp──────▶  pe1_bgp ★
   │                ├─has_interface──▶ pe1_eth1 ★ ──connects_to──▶ p1_eth3 ★ ◀─has_interface─ p1 ★
   │                └─has_interface──▶ pe1_eth0   ──connects_to──▶ pe2_eth0 ◀─has_interface─ pe2
   └─ospf_peer──▶  pe2
                    ├─has_bgp──────▶  pe2_bgp
                    ├─has_interface──▶ pe2_eth0   (connects to pe1_eth0 above)
                    └─has_interface──▶ pe2_eth1   ──connects_to──▶ ce1_eth0 ◀─has_interface─ ce1

  ★ = 5-node subset used in the Parts A–C arithmetic walkthrough

  Node types:  router (4: rr1, pe1, pe2, p1/ce1)
               interface (5: pe1_eth0, pe1_eth1, pe2_eth0, pe2_eth1, p1_eth3/ce1_eth0)
               bgp_session (2: pe1_bgp, pe2_bgp)
  Edge types:  ospf_peer  ·  has_interface  ·  connects_to  ·  has_bgp
  Edge count:  ~17 directed edges across the full topology
```

The same `W_proj`, `W_root`, `W_neigh`, and `W_dec` weight matrices apply to every node
of each type across the whole graph. Adding more routers (rr1, pe2, ce1, …) grows the
input tensors `X_R`, `X_I`, `X_B` in their first dimension but does not change any weight
shape. Part E covers this scaling property in full.

---

## Setup

```
  [router: pe1] ──has_interface──▶ [interface: pe1_eth1]
  [router: p1 ] ──has_interface──▶ [interface: p1_eth3 ]
                                         ↕ connects_to
  [router: pe1] ──has_bgp──────▶  [bgp_session: pe1_bgp]
```

**Node counts and feature dims (F=3 each, H=2 for this walkthrough):**

| Type | N | Nodes | Features |
|------|---|-------|---------|
| router | 2 | pe1, p1 | cpu, mem, ospf_state |
| interface | 2 | pe1_eth1, p1_eth3 | tx_drops, rx_drops, mtu_norm |
| bgp_session | 1 | pe1_bgp | bgp_state, pfx_count, uptime |

**Healthy features:**
```
X_R = [[0.22, 0.30, 1.0],   # pe1
        [0.20, 0.30, 1.0]]   # p1

X_I = [[0.01, 0.01, 0.167],  # pe1_eth1
        [0.01, 0.01, 0.167]]  # p1_eth3

X_B = [[1.0,  0.50, 0.80]]   # pe1_bgp
```

All features are normalised to roughly [0, 1] before being fed to the model. This prevents
features with naturally large magnitudes (e.g. prefix counts in the thousands) from
dominating the gradient signal over features that are already small fractions (e.g. CPU %).
The normalisation factors are computed from training data and fixed at inference time.

---

## Why typed projections? (vs shared GCN weights)

A homogeneous GCN assumes all nodes speak the same "language" — the same F features with
the same meaning. That works if all nodes are routers. Here the graph has three node types
with completely different feature semantics:

- `cpu_percent` (router) is a fractional utilisation score
- `tx_drops_rate` (interface) is a log-scaled counter
- `bgp_state` (bgp_session) is a binary UP/DOWN flag

If we forced these into one shared feature vector and one shared weight matrix, the model
would try to interpret BGP state as if it were a CPU reading — which produces nonsense.
More subtly, it would conflate interface drops with router memory, making it impossible to
identify *which layer* a fault is in even if it detects that *something* is wrong.

In a GCN every node has the same F features and shares one weight matrix W [F, H].
Here, routers have `cpu/mem/ospf`, interfaces have `drops/mtu`, BGP has `state/prefixes`.
These features are **semantically incompatible** — averaging them or sharing one W would
be nonsensical.

**Solution:** `lin_dict` in `HetGNNAutoencoder` gives each type its own:
```python
self.lin_dict['router']      = nn.Linear(3, H)   # cpu,mem,ospf   → latent
self.lin_dict['interface']   = nn.Linear(3, H)   # drops,mtu      → latent
self.lin_dict['bgp_session'] = nn.Linear(3, H)   # state,pfx,upt  → latent
```

All three project into the **same** latent space (dim H), so message passing
can then mix them meaningfully.

---

## Part A — Typed Linear Projections  (`lin_dict[nt](x).relu()`)

**What a linear projection does:** Each node's raw feature vector `x` (length F=3) is
multiplied by a weight matrix `W_proj` (shape [F, H] = [3, 2]) and passed through ReLU,
producing a latent vector `h` (length H=2). You can think of each column of `W_proj` as a
"detector" that answers one latent question — e.g. "how much overall stress is this node
under?" — by taking a learned weighted combination of the raw features. The ReLU then
clips negative answers to zero, enforcing that latent activations are non-negative and
introducing the non-linearity that lets the network represent more than just linear
functions of the inputs.

Because every type uses the **same output size H=2**, all node embeddings end up in the
same 2-D latent space after projection. This is the prerequisite for message passing to
make sense: nodes can only meaningfully exchange information if their embeddings are
expressed in the same units.

**W_proj_R [3→2], W_proj_I [3→2], W_proj_B [3→2]** (trained weights — example values):

```
W_proj_R = [[ 0.5,  0.2 ],   W_proj_I = [[ 0.8,  0.1 ],   W_proj_B = [[ 0.6,  0.3 ],
             [ 0.3,  0.4 ],               [ 0.2,  0.6 ],               [ 0.2,  0.5 ],
             [ 0.1,  0.3 ]]               [ 0.4,  0.7 ]]               [ 0.1,  0.4 ]]
```

### A.1 — Router projections

Each element of the output vector is a dot-product of the input row with one column of
`W_proj_R`. Column 0 of `W_proj_R` is `[0.5, 0.3, 0.1]ᵀ` — it weights cpu most heavily
(0.5), then memory (0.3), and barely touches ospf_state (0.1). Column 1 is
`[0.2, 0.4, 0.3]ᵀ` — it emphasises memory and ospf_state more equally. These weight
values are learned, so in practice they would settle on whatever combination of raw
features best helps the decoder reconstruct healthy router state.

**pe1 = [0.22, 0.30, 1.0]:**
```
h0: 0.22×0.5 + 0.30×0.3 + 1.0×0.1  =  0.110 + 0.090 + 0.100  =  0.300
h1: 0.22×0.2 + 0.30×0.4 + 1.0×0.3  =  0.044 + 0.120 + 0.300  =  0.464

h_R[pe1] = ReLU([0.300, 0.464]) = [0.300, 0.464]
```

Both values are already positive so ReLU has no effect here. In a fault scenario where a
raw feature is 0 (e.g. BGP state going DOWN), the dot-product can go negative; ReLU
clamps those to 0, which means the latent dimension is "inactive" — a deliberate signal to
the decoder that something unusual has happened.

**p1 = [0.20, 0.30, 1.0]:**
```
h0: 0.20×0.5 + 0.30×0.3 + 1.0×0.1  =  0.100 + 0.090 + 0.100  =  0.290
h1: 0.20×0.2 + 0.30×0.4 + 1.0×0.3  =  0.040 + 0.120 + 0.300  =  0.460

h_R[p1] = [0.290, 0.460]
```

Notice that pe1 and p1 have very similar projections (`[0.300, 0.464]` vs `[0.290, 0.460]`)
because their raw features only differ in cpu (0.22 vs 0.20). Both routers are healthy and
the small difference reflects the slight cpu load difference — exactly what we want. A
model that represents healthy nodes as a tight cluster and faulty nodes as outliers is the
goal.

### A.2 — Interface projections (healthy: tx_drops=0.01, rx=0.01, mtu=0.167)

**pe1_eth1 = p1_eth3 = [0.01, 0.01, 0.167]:**
```
h0: 0.01×0.8 + 0.01×0.2 + 0.167×0.4  =  0.008 + 0.002 + 0.067  =  0.077
h1: 0.01×0.1 + 0.01×0.6 + 0.167×0.7  =  0.001 + 0.006 + 0.117  =  0.124

h_I[pe1_eth1] = h_I[p1_eth3] = [0.077, 0.124]
```

The interface projections land much closer to the origin than the router projections
(`[0.077, 0.124]` vs `[0.300, 0.464]`). This is expected: healthy interfaces have near-zero
drop rates, so most of the latent activation comes only from the MTU term (column 2 of
`W_proj_I` has the largest weights, 0.4 and 0.7). A faulty interface with a high drop rate
would activate the first two terms strongly and produce a much larger latent vector — which
is exactly the displacement the decoder will fail to reconstruct correctly.

### A.3 — BGP projection (healthy: bgp=1.0, pfx=0.50, upt=0.80)

**pe1_bgp = [1.0, 0.50, 0.80]:**
```
h0: 1.0×0.6 + 0.50×0.2 + 0.80×0.1  =  0.600 + 0.100 + 0.080  =  0.780
h1: 1.0×0.3 + 0.50×0.5 + 0.80×0.4  =  0.300 + 0.250 + 0.320  =  0.870

h_B[pe1_bgp] = [0.780, 0.870]
```

The BGP node projects to `[0.780, 0.870]` — the largest latent values of any node.
This is because BGP state is binary (1.0 = UP, which is the healthiest value possible),
and `W_proj_B` gives bgp_state the highest weight in both dimensions. When the session
goes DOWN (0.0), both dimensions drop to near zero — a dramatic shift that Part C will
show produces a large reconstruction error.

After Part A, every node regardless of its original type lives in the same 2-D space:

```
  h_R[pe1]       ≈ [0.300, 0.464]
  h_R[p1]        ≈ [0.290, 0.460]
  h_I[pe1_eth1]  ≈ [0.077, 0.124]
  h_I[p1_eth3]   ≈ [0.077, 0.124]
  h_B[pe1_bgp]   ≈ [0.780, 0.870]
```

Message passing can now treat these as comparable vectors and combine them.

---

## Part B — Heterogeneous Message Passing  (`HeteroConv(SAGEConv, aggr='sum')`)

**What message passing achieves in a heterogeneous graph:** After the typed projections
all nodes live in the same H-dimensional latent space. Now we let them talk. The key
difference from a homogeneous GCN is that the *meaning* of a message depends on what
type of entity sent it. A message from a router to an interface carries information about
the router's CPU and OSPF state; a message from one interface to another carries
information about the connected link's drop rate. Using separate `SAGEConv` modules per
edge type means the model learns separate transformation weights for each type of
relationship — it "listens differently" to a router update vs a link-peer update.

After message passing, each interface node's embedding encodes not just its own drops and
MTU but also what the router above it and the interface across the link are currently doing.
This is what lets the model distinguish "this interface is dropping packets because of a
local MTU misconfiguration" from "this interface looks bad because the router above it is
overloaded" — the fault signal comes from different directions.

### How SAGEConv differs from GCN

**GCN:**
```
h_v^new = ReLU( Â_norm_row_v @ H_all @ W )
```
One shared W, one aggregation over all neighbours (normalised sum).

**SAGEConv (GraphSAGE, used in production):**
```
h_v^new = ReLU( W_self @ h_v  +  W_neigh @ (1/|N(v)|) Σ_{u∈N(v)} h_u )
```
Two separate weight matrices — one for self, one for neighbours.
In PyG's heterogeneous mode, for edge type `(src_type, rel, dst_type)`:
```
h_dst^new = W_root @ h_dst  +  W_neigh @ mean_{u ∈ N_src(dst)} h_src[u]
```

**`HeteroConv` routes messages by edge type** — each SAGEConv only fires for its
assigned edge type, with its own private `W_root` and `W_neigh`.

The two-weight design of SAGEConv also fixes a subtle problem with plain GCN in
heterogeneous graphs: the GCN aggregation mixes self and neighbours into one weighted sum,
so in a bipartite edge (router→interface) the destination node (interface) has no "self"
contribution in the usual sense — it only receives from routers. SAGEConv handles bipartite
edges correctly by keeping `W_root` (applied to the destination's own embedding) and
`W_neigh` (applied to the source neighbours) as separate linear maps, then summing them.

### B.1 — Edge type: ('router', 'has_interface', 'interface')

**Intuition for this edge type:** An interface physically lives on a router. If the router
is overloaded (high CPU, memory pressure), that stress can manifest as increased packet
drops on its interfaces — even if the interface hardware is fine. By sending a message
from each router to its attached interfaces, the model lets the interface embedding absorb
information about the router's current state. This is what allows the model to later
distinguish "interface dropping packets because of its own MTU misconfiguration" from
"interface dropping packets because its parent router is overwhelmed".

Edge index: pe1(router 0)→pe1_eth1(iface 0), p1(router 1)→p1_eth3(iface 1)

Weights for this conv (W_root_RI [2,2], W_neigh_RI [2,2]):
```
W_root_RI  = [[ 0.6,  0.1 ],    W_neigh_RI = [[ 0.3,  0.4 ],
               [ 0.2,  0.7 ]]                  [ 0.5,  0.2 ]]
```

**Update pe1_eth1** (destination = interface 0, source = router 0 = pe1):

`self_part` keeps the interface's own identity in its new embedding — without it, the
interface would become a pure aggregate of its router's information and lose its own signal.
`neigh_part` injects the router's latent state into the interface embedding. The separate
weight matrices `W_root_RI` and `W_neigh_RI` let the model learn different transformations
for "what does my own state contribute?" vs "what does my router's state contribute?".

```
self_part = h_I[pe1_eth1] @ W_root_RI:
  s0: 0.077×0.6 + 0.124×0.2  =  0.046 + 0.025  =  0.071
  s1: 0.077×0.1 + 0.124×0.7  =  0.008 + 0.087  =  0.095

neigh_part = h_R[pe1] @ W_neigh_RI:   (1 neighbour, mean = itself)
  n0: 0.300×0.3 + 0.464×0.5  =  0.090 + 0.232  =  0.322
  n1: 0.300×0.4 + 0.464×0.2  =  0.120 + 0.093  =  0.213

msg_RI[pe1_eth1] = [0.071+0.322,  0.095+0.213] = [0.393, 0.308]
```

The `neigh_part` values (0.322, 0.213) dwarf the `self_part` values (0.071, 0.095) here.
This is because the router embedding is much larger than the interface embedding in latent
space (routers project to ~0.3–0.5, interfaces to ~0.1). In a healthy network this is
fine — the decoder is trained to expect these combined values. In a fault scenario where
the router's embedding spikes, the interface embedding would be displaced even if the
interface itself is healthy, which is why the model must use branch-level scores rather
than individual node scores alone.

**Update p1_eth3** (mirror — p1 router feeds p1_eth3):
```
self_part = h_I[p1_eth3] @ W_root_RI = [0.071, 0.095]   (same features)
neigh_part = h_R[p1] @ W_neigh_RI:
  n0: 0.290×0.3 + 0.460×0.5  =  0.087 + 0.230  =  0.317
  n1: 0.290×0.4 + 0.460×0.2  =  0.116 + 0.092  =  0.208

msg_RI[p1_eth3] = [0.071+0.317,  0.095+0.208] = [0.388, 0.303]
```

p1_eth3's message is very close to pe1_eth1's (`[0.388, 0.303]` vs `[0.393, 0.308]`),
reflecting the fact that both routers and both interfaces are healthy and nearly identical.

### B.2 — Edge type: ('interface', 'connects_to', 'interface')

**Intuition for this edge type:** The two interfaces are at opposite ends of a physical
link — pe1_eth1 on one router, p1_eth3 on the other. If one side develops an MTU
mismatch, packets will be dropped in one direction only; the peer interface will see
rx_drops even though its own configuration is correct. The `connects_to` edge lets each
interface observe its peer's state so the model can detect whether drops are local (one
side only) or symmetric (both sides, suggesting a link-layer issue).

Both interfaces share the same features → same embeddings → symmetric peer messages.

Weights for this conv (W_root_II [2,2], W_neigh_II [2,2]):
```
W_root_II  = [[ 0.5,  0.2 ],    W_neigh_II = [[ 0.4,  0.1 ],
               [ 0.3,  0.6 ]]                  [ 0.2,  0.5 ]]
```

**Update pe1_eth1** (receives from p1_eth3):
```
self_part = h_I[pe1_eth1] @ W_root_II:
  s0: 0.077×0.5 + 0.124×0.3  =  0.039 + 0.037  =  0.076
  s1: 0.077×0.2 + 0.124×0.6  =  0.015 + 0.074  =  0.090

neigh_part = h_I[p1_eth3] @ W_neigh_II:   (peer = p1_eth3)
  n0: 0.077×0.4 + 0.124×0.2  =  0.031 + 0.025  =  0.056
  n1: 0.077×0.1 + 0.124×0.5  =  0.008 + 0.062  =  0.070

msg_II[pe1_eth1] = [0.076+0.056,  0.090+0.070] = [0.132, 0.160]
```

Note that `msg_II` uses completely separate weight matrices from `msg_RI`, even though both
write into the interface embedding. The `has_interface` message comes from a router (a
different semantic domain) and the `connects_to` message comes from a peer interface (the
same semantic domain). Giving them separate weights lets the model learn to treat router
stress and peer interface stress as independent signals.

### B.3 — HeteroConv aggregation (aggr='sum')

**Why `aggr='sum'` instead of `'mean'`?** With `'mean'`, each message is divided by the
number of contributing edge types. For a node like pe1_eth1 that receives from two
different edge types (one `has_interface` message and one `connects_to` message), `'mean'`
would halve the contribution of each. `'sum'` preserves the full magnitude of each message,
letting the model freely learn how to weight the different signal sources through its weight
matrices. In practice `'mean'` is safer when degree varies widely; `'sum'` is preferred
here because the number of edge types per node is fixed by the schema.

For destination pe1_eth1 (interface type), HeteroConv sums contributions
from all edge types that write to the `interface` destination:

```
h_I_new[pe1_eth1] = ReLU( msg_RI[pe1_eth1] + msg_II[pe1_eth1] )
                  = ReLU( [0.393, 0.308] + [0.132, 0.160] )
                  = ReLU( [0.525, 0.468] )
                  = [0.525, 0.468]
```

This final embedding `[0.525, 0.468]` is what gets passed to the interface decoder. It
encodes three sources of information simultaneously:
- The interface's own drop rate and MTU (from the projection in Part A)
- The state of the router it is attached to (from `msg_RI` via the `has_interface` edge)
- The state of its link peer (from `msg_II` via the `connects_to` edge)

The decoder was trained to map embeddings like this — seen during healthy training — back
to the original healthy feature values. In a fault scenario, one of these three sources
will shift the embedding away from the trained cluster, and the decoder will fail to
reconstruct correctly, revealing the fault.

### B.4 — Router update: no incoming edges

Routers in this graph only *send* messages — they have no incoming edge types in this
example topology. Their embeddings after message passing are therefore identical to their
projections from Part A:

```
h_R_new[pe1] = h_R[pe1] = [0.300, 0.464]
h_R_new[p1]  = h_R[p1]  = [0.290, 0.460]
```

In a larger graph, routers would typically also receive messages (e.g. from other routers
via an `ospf_peer` edge), which would let the model capture how a failing upstream router
affects downstream router state. For this minimal example, routers are leaf senders only.

### B.5 — BGP update: edge type ('router', 'has_bgp', 'bgp_session')

**Why does router state flow into the BGP embedding?** The `has_bgp` edge exists because
a BGP session runs *on* a router — if the router's CPU is overloaded, its BGP process may
miss keepalives and flap the session. By having the router send a message to its BGP node,
the model captures this upstream dependency. In a fault scenario where the router is healthy
but the BGP session tears down independently, the router message provides a "control" signal
that distinguishes "BGP down due to router overload" from "BGP down due to an external
policy change" — the former would show elevated router features in the message; the latter
would not.

Weights for this conv (W_root_RB [2,2], W_neigh_RB [2,2]):
```
W_root_RB  = [[ 0.5,  0.2 ],    W_neigh_RB = [[ 0.2,  0.3 ],
               [ 0.3,  0.6 ]]                  [ 0.4,  0.1 ]]
```

pe1 → pe1_bgp (1 edge, 1 neighbour):

```
self_part = h_B[pe1_bgp] @ W_root_RB:
  s0: 0.780×0.5 + 0.870×0.3  =  0.390 + 0.261  =  0.651
  s1: 0.780×0.2 + 0.870×0.6  =  0.156 + 0.522  =  0.678

neigh_part = h_R[pe1] @ W_neigh_RB:   (1 neighbour, mean = itself)
  n0: 0.300×0.2 + 0.464×0.4  =  0.060 + 0.186  =  0.246
  n1: 0.300×0.3 + 0.464×0.1  =  0.090 + 0.046  =  0.136

h_B_new[pe1_bgp] = ReLU([0.651+0.246,  0.678+0.136])
                 = ReLU([0.897, 0.814])
                 = [0.897, 0.814]   ← router health is baked into the BGP embedding
```

BGP has no `connects_to` type edges, so only one source of messages. This means the BGP
decoder has less context than the interface decoder: it can see whether the router is
healthy, but not whether there is a wider topology change (e.g. a peer router going down).
In a production graph you would typically add `bgp_peer` edges between BGP sessions so
that route-reflector failures propagate through the BGP embedding layer.

After Part B, every node has a richer embedding that reflects not just its own telemetry
but also the state of everything directly connected to it. The decoder's job in Part C is
to determine whether that combined embedding is consistent with a healthy network.

---

## Part C — Decoder and Branch Anomaly Scores

### C.1 — Per-type decode (`decoder_dict[nt](h)`)

**Why a separate decoder per branch?** This is the mechanism that enables branch-level
diagnosis. If all types shared one decoder, a large reconstruction error on the BGP features
could leak into the interface reconstruction — the gradients would interfere and the model
could not separately attribute errors to different fault layers.

With per-type decoders, the interface decoder only ever sees interface embeddings and only
ever outputs interface features. Its reconstruction error is a pure measure of how much the
interface branch deviated from healthy. Whichever branch's decoder reports the highest error
is the answer to "which layer is the fault in?" — configuration, protocol, or physical.

```python
self.decoder_dict['router']      = nn.Linear(H=2, F=3)
self.decoder_dict['interface']   = nn.Linear(H=2, F=3)
self.decoder_dict['bgp_session'] = nn.Linear(H=2, F=3)
```

Each branch reconstructs its own features independently from its own embedding.
Trained on healthy data: recon ≈ actual for healthy nodes.

**Decoder weight matrices** (example trained values — shape [H=2, F=3]):
```
W_dec_R = [[ 0.50,  0.00,  2.50 ],    b_dec_R = [ 0.070,  0.067,  0.000 ]
            [ 0.00,  0.50,  0.50 ]]

W_dec_I = [[ 0.01,  0.01,  0.10 ],    b_dec_I = [ 0.000,  0.000,  0.010 ]
            [ 0.01,  0.01,  0.20 ]]

W_dec_B = [[ 0.60,  0.25,  0.45 ],    b_dec_B = [ 0.020,  0.010,  0.015 ]
            [ 0.55,  0.35,  0.45 ]]
```

Each decoder computes:  `recon = h @ W_dec + b`
The columns of W_dec correspond to output features; the rows correspond to latent
dimensions. A column with small weights means that latent dimension contributes little to
reconstructing that feature; a large weight means it contributes heavily.

**Healthy decode baseline** — confirming near-zero reconstruction error on the healthy graph:

*Router decoder applied to h_R_new[pe1] = [0.300, 0.464]:*
```
recon[cpu]  = 0.300×0.50 + 0.464×0.00 + 0.070  =  0.150 + 0.000 + 0.070  =  0.220   actual: 0.22
recon[mem]  = 0.300×0.00 + 0.464×0.50 + 0.067  =  0.000 + 0.232 + 0.067  =  0.299   actual: 0.30
recon[ospf] = 0.300×2.50 + 0.464×0.50 + 0.000  =  0.750 + 0.232 + 0.000  =  0.982   actual: 1.00

MSE_router[pe1] = ( (0.22-0.220)² + (0.30-0.299)² + (1.00-0.982)² ) / 3
                = ( 0.000 + 0.000 + 0.000 ) / 3  =  0.000
```

*Interface decoder applied to h_I_new[pe1_eth1] = [0.525, 0.468]:*
```
recon[tx_drops] = 0.525×0.01 + 0.468×0.01 + 0.000  =  0.005 + 0.005  =  0.010   actual: 0.01
recon[rx_drops] = 0.525×0.01 + 0.468×0.01 + 0.000  =  0.010                      actual: 0.01
recon[mtu_norm] = 0.525×0.10 + 0.468×0.20 + 0.010  =  0.053 + 0.094 + 0.010  =  0.157   actual: 0.167

MSE_interface[pe1_eth1] = ( (0.01-0.010)² + (0.01-0.010)² + (0.167-0.157)² ) / 3
                        = ( 0.000 + 0.000 + 0.000 ) / 3  =  0.000
```

*BGP decoder applied to h_B_new[pe1_bgp] = [0.897, 0.814]:*
```
recon[bgp_state] = 0.897×0.60 + 0.814×0.55 + 0.020  =  0.538 + 0.448 + 0.020  =  1.006   actual: 1.00
recon[pfx_count] = 0.897×0.25 + 0.814×0.35 + 0.010  =  0.224 + 0.285 + 0.010  =  0.519   actual: 0.50
recon[uptime]    = 0.897×0.45 + 0.814×0.45 + 0.015  =  0.404 + 0.366 + 0.015  =  0.785   actual: 0.80

MSE_bgp[pe1_bgp] = ( (1.00-1.006)² + (0.50-0.519)² + (0.80-0.785)² ) / 3
                 = ( 0.000 + 0.000 + 0.000 ) / 3  =  0.000
```

All three branches score 0.000 on the healthy graph — the model has successfully learned
what "normal" looks like. These decoder weights are now fixed; Parts C.2 and C.4 apply
them to faulty embeddings and observe where they fail.

### C.2 — Fault 1: MTU mismatch on pe1_eth1

**What "trained on healthy data" means for the decoder:** During training the model only
ever saw embeddings that correspond to healthy nodes. The decoder's weights settle so that
given a healthy interface embedding (around `[0.525, 0.468]` post-message-passing), it
outputs approximately `[0.01, 0.01, 0.167]` — near-zero drops and the correct MTU.
The decoder has never learned to output large drop values, because it has never needed to.
When a faulty node's embedding lands outside the healthy cluster, the decoder still applies
the same weights, producing output values that approximate the healthy range — which are
wrong. The gap between the decoder's prediction and the actual faulty features is the
reconstruction error.

Fault snapshot:
```
X_I_fault = [[0.75, 0.01, 0.156],   # pe1_eth1: tx_drops spike, mtu 1400
              [0.01, 0.01, 0.167]]   # p1_eth3: healthy
```

Projection of pe1_eth1 fault:
```
h_I_fault[pe1_eth1]:
  h0: 0.75×0.8 + 0.01×0.2 + 0.156×0.4  =  0.600 + 0.002 + 0.062  =  0.664  (was 0.077)
  h1: 0.75×0.1 + 0.01×0.6 + 0.156×0.7  =  0.075 + 0.006 + 0.109  =  0.190  (was 0.124)

h_I_fault[pe1_eth1] = [0.664, 0.190]   vs healthy [0.077, 0.124]
```

**Message passing for faulty pe1_eth1** — same weight matrices as Part B; router pe1 and
peer p1_eth3 are unchanged (only pe1_eth1's raw features changed):

Edge type `has_interface` — pe1 router feeds pe1_eth1:
```
self_part = h_I_fault[pe1_eth1] @ W_root_RI:
  s0: 0.664×0.6 + 0.190×0.2  =  0.398 + 0.038  =  0.437   (was 0.071 in healthy)
  s1: 0.664×0.1 + 0.190×0.7  =  0.066 + 0.133  =  0.199   (was 0.095 in healthy)

neigh_part = h_R[pe1] @ W_neigh_RI:   (router unchanged)
  n0: 0.300×0.3 + 0.464×0.5  =  0.090 + 0.232  =  0.322
  n1: 0.300×0.4 + 0.464×0.2  =  0.120 + 0.093  =  0.213

msg_RI_fault[pe1_eth1] = [0.437+0.322,  0.199+0.213] = [0.759, 0.412]   (was [0.393, 0.308])
```

Edge type `connects_to` — healthy p1_eth3 peer feeds pe1_eth1:
```
self_part = h_I_fault[pe1_eth1] @ W_root_II:
  s0: 0.664×0.5 + 0.190×0.3  =  0.332 + 0.057  =  0.389   (was 0.076)
  s1: 0.664×0.2 + 0.190×0.6  =  0.133 + 0.114  =  0.247   (was 0.090)

neigh_part = h_I[p1_eth3] @ W_neigh_II:   (peer still healthy)
  n0: 0.077×0.4 + 0.124×0.2  =  0.031 + 0.025  =  0.056
  n1: 0.077×0.1 + 0.124×0.5  =  0.008 + 0.062  =  0.070

msg_II_fault[pe1_eth1] = [0.389+0.056,  0.247+0.070] = [0.445, 0.317]   (was [0.132, 0.160])
```

HeteroConv sum:
```
h_I_new_fault[pe1_eth1] = ReLU( [0.759, 0.412] + [0.445, 0.317] )
                        = ReLU( [1.204, 0.729] )
                        = [1.204, 0.729]   vs healthy [0.525, 0.468]
```

The faulty embedding is roughly 2.3× larger than the healthy one — it has moved well
outside the region the decoder was trained on. The `self_part` contribution (0.437, 0.199)
is now comparable to the router's `neigh_part` (0.322, 0.213), because the inflated
tx_drops feature dominates `W_proj_I`'s first column weight of 0.8.

**Decoder output for the faulty embedding** (using `W_dec_I` from C.1):
```
recon[tx_drops] = 1.204×0.01 + 0.729×0.01 + 0.000  =  0.012 + 0.007  =  0.019
recon[rx_drops] = 1.204×0.01 + 0.729×0.01 + 0.000  =  0.019
recon[mtu_norm] = 1.204×0.10 + 0.729×0.20 + 0.010  =  0.120 + 0.146 + 0.010  =  0.276
```

The decoder predicts near-zero drops (0.019) because it was trained on near-zero-drop
embeddings. It also over-predicts MTU (0.276 vs actual 0.156) because the inflated
embedding magnitude pushes all outputs upwards. Both errors stem from the same cause: the
embedding is out of distribution.

```
actual_I[pe1_eth1] = [0.75, 0.01, 0.156]

MSE:
  tx_drops: (0.75 - 0.019)²  =  (0.731)²  =  0.534
  rx_drops: (0.01 - 0.019)²  =  (0.009)²  =  0.000
  mtu_norm: (0.156 - 0.276)² =  (0.120)²  =  0.014

MSE_interface = (0.534 + 0.000 + 0.014) / 3  =  0.183
```

The router and BGP embeddings are unchanged (their inputs didn't change):
```
MSE_router      ≈ 0.000
MSE_bgp_session ≈ 0.000
```

### C.3 — Branch scores

**Why `max` over nodes rather than `mean`?** In a large graph, most nodes of a given type
will be healthy and their individual errors will be near zero. Taking the mean would dilute
a single faulty node's large error across all the healthy ones. Taking the max ensures that
even a single anomalous node within a branch raises the branch score — which is exactly
what we want for fault pinpointing: "at least one interface is behaving abnormally".

```
branch_score[nt] = max over nodes of: mean((recon[nt] - x[nt])², dim=features)
```

```
  router      0.000   ✓ normal
  interface   0.183   ⚠️  FAULT LAYER  ← tx_drops drove the embedding out of range
  bgp_session 0.000   ✓ normal

→ Root layer: interface
```

The fact that `bgp_session` scores 0.000 despite pe1_bgp receiving a message from pe1
(whose embedding was unchanged) confirms an important property: the BGP decoder was trained
on embeddings that already include router state. A healthy router feeding into a BGP
session produces an embedding in the trained healthy cluster — the decoder reconstructs
correctly. It is only when the router *or* the BGP session itself goes outside the healthy
range that the BGP branch fires.

### C.4 — Fault 2: BGP session teardown

```
X_B_fault = [[0.0, 0.0, 0.0]]   # pe1_bgp: DOWN, 0 prefixes, 0 uptime
```

BGP projection of fault:
```
h_B_fault[pe1_bgp]:
  h0: 0.0×0.6 + 0.0×0.2 + 0.0×0.1  =  0.000   (was 0.780)
  h1: 0.0×0.3 + 0.0×0.5 + 0.0×0.4  =  0.000   (was 0.870)

h_B_fault = [0.000, 0.000]  ← completely zeroed
```

The projection collapses to the origin because every raw feature is zero. The healthy
projection was `[0.780, 0.870]`; the fault moves it all the way to `[0.000, 0.000]`.

**Message passing for faulty pe1_bgp** (using W_root_RB, W_neigh_RB from B.5;
router pe1 is unchanged):
```
self_part = h_B_fault[pe1_bgp] @ W_root_RB:
  s0: 0.000×0.5 + 0.000×0.3  =  0.000   (was 0.651)
  s1: 0.000×0.2 + 0.000×0.6  =  0.000   (was 0.678)

neigh_part = h_R[pe1] @ W_neigh_RB:   (router unchanged)
  n0: 0.300×0.2 + 0.464×0.4  =  0.060 + 0.186  =  0.246
  n1: 0.300×0.3 + 0.464×0.1  =  0.090 + 0.046  =  0.136

h_B_new_fault[pe1_bgp] = ReLU([0.000+0.246,  0.000+0.136])
                       = [0.246, 0.136]   vs healthy [0.897, 0.814]
```

The `self_part` is completely zeroed because the session's own projection was zeroed by the
fault. Only the router's `neigh_part` survives. The BGP embedding shrinks to roughly 27%
of its healthy magnitude — the session has lost its identity in the latent space and is now
just a faint echo of the router's signal.

**Decoder output for the faulty BGP embedding** (using `W_dec_B` from C.1):
```
recon[bgp_state] = 0.246×0.60 + 0.136×0.55 + 0.020  =  0.148 + 0.075 + 0.020  =  0.243
recon[pfx_count] = 0.246×0.25 + 0.136×0.35 + 0.010  =  0.062 + 0.048 + 0.010  =  0.119
recon[uptime]    = 0.246×0.45 + 0.136×0.45 + 0.015  =  0.111 + 0.061 + 0.015  =  0.187
```

The decoder produces partial output — not all zeros and not healthy values — because the
router's `neigh_part` provides a residual signal. The actual values are all zero (session
is completely down). The mismatch is the reconstruction error:

```
actual_B[pe1_bgp] = [0.0, 0.0, 0.0]

MSE:
  bgp_state:   (0.0 - 0.243)²  =  (0.243)²  =  0.059
  pfx_count:   (0.0 - 0.119)²  =  (0.119)²  =  0.014
  uptime:      (0.0 - 0.187)²  =  (0.187)²  =  0.035

MSE_bgp = (0.059 + 0.014 + 0.035) / 3  =  0.036
```

Branch scores:
```
  router      0.000   ✓ normal
  interface   0.000   ✓ normal
  bgp_session 0.036   ⚠️  FAULT LAYER

→ Root layer: bgp_session
```

---

## Part D — Multi-task Training Loss

**Multi-task learning — what it means and why it helps:** Training three branches at once
with a shared backbone is called multi-task learning. The `HeteroConv` message-passing
layers are shared — their weights are updated by the combined gradient of all three branch
losses simultaneously. This is beneficial because the shared layers learn representations
that are simultaneously useful for reconstructing router state, interface state, and BGP
state. A feature that is irrelevant to all three branches will receive small gradient
updates from every direction and will be suppressed; a feature that matters for even one
branch will be preserved.

**Gradient isolation by branch:** However, `W_proj_I` (the interface input projection) and
`W_dec_I` (the interface decoder) are updated *only* by `loss_interface`. If a BGP fault
causes `loss_bgp` to spike, those gradients flow only through `W_proj_B` and `W_dec_B` and
do not touch any interface weights. This ensures that the interface decoder stays calibrated
to healthy interface states even when BGP faults are frequent — and vice versa.

```python
loss = (loss_router + loss_interface + loss_bgp) / 3.0
```

Each branch independently learns to reconstruct its own features. The gradients
for the interface branch cannot corrupt the BGP branch — they are separated by
independent `W_proj` and `W_dec` matrices.

This is the key advantage over GCN: when the interface branch fires, we know
the fault is in the physical/config layer, not in a protocol or hardware layer.

---

## Part E — Scaling to Production Networks

### E.1 — What does and does not grow with network size

A common first instinct is that a bigger network means a bigger model. With GNNs this is
not the case. The key insight is that **weight matrices are shaped by feature count (F) and
latent dimension (H), not by node count (N)**.

```
Walkthrough (toy):     N_R=2,   N_I=2,   N_B=1
Production (medium):   N_R=50,  N_I=400, N_B=100
Production (large):    N_R=500, N_I=8000, N_B=2000

In all three cases:
  W_proj_R   shape  [F_R, H]  =  [3, 64]    ← same
  W_root_RI  shape  [H,   H]  =  [64, 64]   ← same
  W_dec_R    shape  [H, F_R]  =  [64, 3]    ← same
```

The same projection weights are applied to every router node regardless of how many
routers there are. This shared-weight property is what makes GNNs *inductive* — you can
train on one network topology and run inference on a different (larger or smaller) one
without retraining.

| What grows with N | What does not grow with N |
|------------------|--------------------------|
| Input tensors X_R [N_R, F], X_I [N_I, F], … | W_proj, W_root, W_neigh, W_dec matrices |
| Edge index tensors [2, E] | Number of model parameters |
| Message-passing compute time | H (latent dimension) |
| Memory for intermediate embeddings | F (features per node type) |

### E.2 — Growing the feature set (larger F)

In a real network, each router might expose 20 metrics rather than 3:

```
# Toy (F=3):
router features: [cpu_util, mem_util, ospf_state]

# Production (F=20):
router features: [cpu_util, mem_util, ospf_state, ospf_nbr_count,
                  bgp_pfx_rx, bgp_pfx_tx, isis_adj_count,
                  pkt_loss_rate, jitter_ms, latency_ms,
                  fwd_table_util, acl_hit_rate, npu_util,
                  fan_rpm_norm, psu_voltage_norm, temp_celsius_norm,
                  uptime_norm, reload_count_norm, config_age_norm,
                  alarm_count_norm]
```

This changes only `W_proj_R` from shape `[3, H]` to `[20, H]`. Everything downstream
(message passing, decoder) remains identical — the projection is the only place F appears.

```python
# Toy
self.lin_dict['router'] = nn.Linear(3, H)    # [3→H]

# Production — only this line changes
self.lin_dict['router'] = nn.Linear(20, H)   # [20→H]

# Message passing and decoder are identical in both cases:
# HeteroConv(SAGEConv(...))  and  nn.Linear(H, 20)
```

### E.3 — Growing the node type vocabulary (more types)

A real graph might have additional node types the toy example lacks:

```python
# Toy schema (3 types)
node_types = ['router', 'interface', 'bgp_session']

# Production schema (more types)
node_types = ['router', 'interface', 'bgp_session',
              'vrf', 'prefix', 'optical_transponder', 'lag']

edge_types = [
    ('router',            'has_interface',    'interface'),
    ('interface',         'connects_to',      'interface'),
    ('router',            'has_bgp',          'bgp_session'),
    # new:
    ('router',            'has_vrf',          'vrf'),
    ('vrf',               'imports_prefix',   'prefix'),
    ('router',            'has_transponder',  'optical_transponder'),
    ('interface',         'member_of',        'lag'),
    ('lag',               'has_member',       'interface'),
]
```

Each new node type adds one new `lin_dict` entry and one new `decoder_dict` entry. Each
new edge type adds one new `SAGEConv` module inside `HeteroConv`. The per-type weight
matrices remain small — shaped by F and H — regardless of how many nodes of that type
exist in the graph.

```python
# Model __init__ scales in parameter count only with new TYPES, not with new NODES:
for nt in node_types:
    self.lin_dict[nt]     = nn.Linear(F[nt], H)   # O(F × H) params per type
    self.decoder_dict[nt] = nn.Linear(H, F[nt])   # O(H × F) params per type

for et in edge_types:
    conv_dict[et] = SAGEConv((-1, -1), H)          # O(H²) params per edge type

# Total parameters ≈  |node_types| × 2×F×H  +  |edge_types| × 2×H²
# For F=20, H=64, 7 node types, 8 edge types:
#   = 7 × 2×20×64  +  8 × 2×64²
#   = 17,920  +  65,536  ≈  83K parameters
# Completely independent of N.
```

### E.4 — Where N does matter: message passing compute

The real scaling cost is in the message-passing step. For each edge type `(src, rel, dst)`,
SAGEConv must aggregate over every edge in that type's edge index:

```
# Edge index for ('router', 'has_interface', 'interface')
# shape [2, E_RI]  where E_RI = number of has_interface edges

# For a network with 500 routers each having 8 interfaces:
#   E_RI = 4000 edges
# Message passing: for each destination interface, mean over its router neighbours
#   → 4000 dot-products of size H=64  =  256K multiply-adds per forward pass
```

For very large graphs (hundreds of thousands of nodes, millions of edges), this becomes
expensive. The standard solution is **neighbourhood sampling**: instead of aggregating all
neighbours, sample a fixed-size random subset `k` per node:

```python
# Full message passing (used in this walkthrough — small graph):
loader = DataLoader(graph, batch_size=1)

# Neighbourhood sampling (production — large graph):
from torch_geometric.loader import NeighborLoader

loader = NeighborLoader(
    graph,
    num_neighbors={et: 10 for et in edge_types},  # sample 10 neighbours per edge type
    batch_size=256,                                 # process 256 seed nodes at a time
    input_nodes=('router', router_node_ids),
)

# The SAGEConv forward pass is identical — it just receives a smaller edge index.
# Mean aggregation over 10 sampled neighbours is an unbiased estimate of the
# mean over all neighbours, so the model still trains and infers correctly.
```

This keeps per-batch cost constant regardless of total graph size:

```
Cost per batch ∝  batch_size × k × H²
                = 256 × 10 × 64²
                = 10.5M multiply-adds   ← same whether N is 1,000 or 1,000,000
```

### E.5 — Summary: what changes, what stays the same

```
Dimension        Toy walkthrough    Medium production    Large production
─────────────────────────────────────────────────────────────────────────
N (nodes)        5                  ~550                 ~10,500
E (edges)        4                  ~4,400               ~85,000
|node_types|     3                  5                    7
F (features)     3                  10                   20
H (latent dim)   2                  32                   64
─────────────────────────────────────────────────────────────────────────
Model params     ~40                ~13K                 ~83K
─────────────────────────────────────────────────────────────────────────
W_proj shape     [3, 2]             [10, 32]             [20, 64]
W_sage shape     [2, 2]             [32, 32]             [64, 64]
W_dec shape      [2, 3]             [32, 10]             [64, 20]
─────────────────────────────────────────────────────────────────────────
Training data    N snapshots        N × T snapshots      N × T snapshots
                 (T time steps)     over topology        over topology
─────────────────────────────────────────────────────────────────────────
```

The model grows only when you add node types, edge types, or increase F or H — not
when the network itself grows larger. A model trained on a 50-router network can be run
directly on a 500-router network with identical weights, because the weight matrices only
care about feature semantics, not about how many nodes share those features.

---

## Summary of every formula

| Step | Formula | PyTorch code |
|------|---------|-------------|
| Typed projection | h_nt = ReLU(x_nt @ W_proj_nt) | `self.lin_dict[nt](x).relu()` |
| SAGEConv | h_dst = W_root@h_dst + W_neigh@mean(h_src) | `SAGEConv((-1,-1), H)` inside HeteroConv |
| Edge routing | HeteroConv dispatches by (src_type, rel, dst_type) | `HeteroConv(conv_dict, aggr='sum')` |
| Decode | recon_nt = h_nt @ W_dec_nt + b_nt | `self.decoder_dict[nt](h)` |
| Node score | score_i = mean((recon_i - x_i)²) | `((recon-x)**2).mean(dim=1)` |
| Branch score | branch = max(node_scores_nt) | `float(err.max())` |
| Train loss | L = (L_R + L_I + L_B) / 3 | `(loss_r + loss_i + loss_b) / 3.0` |
