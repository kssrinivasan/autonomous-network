# STGNN Maths Walkthrough — PyTorch / torch_geometric

Every arithmetic step shown with concrete numbers.

Code reference: `simple_stgnn_pinpointing.py`  →  `STGNNAutoencoder`  
Production reference: the production HetGNN in `domains/transport/gnn/src/model/hetgnn.py` (there is no separate `stgnn.py` — the temporal model is folded into it)

The math is framework-agnostic (GRU equations are the same in TF and PyTorch).
This walkthrough adds PyTorch-specific code references throughout.

---

## Setup

```
  [router: pe1] ──has_bgp──▶ [bgp_session: pe1_bgp]
```

We trace pe1_bgp through **3 time steps** to show how the STGNN catches a
BGP flap that a static HetGNN would miss entirely.

**T=3, H=2** (code uses H=32, T=3 in example; 2 makes every matrix on-screen)

**Time series — BGP flap scenario:**

| t | Router X_R | Interface X_I | BGP X_B | Event |
|---|-----------|--------------|---------|-------|
| 0 | [0.22, 0.30, 1.0] | [0.01, 0.01, 0.167] | [1.0, 0.50, 0.80] | Healthy |
| 1 | [0.22, 0.30, 1.0] | [0.01, 0.01, 0.167] | [0.0, 0.00, 0.00] | BGP DOWN |
| 2 | [0.22, 0.30, 1.0] | [0.01, 0.01, 0.167] | [1.0, 0.50, 0.80] | Recovered |

At t=2 every feature looks **identical to t=0** — a static HetGNN on t=2 alone
gives anomaly score ≈ 0. The STGNN catches the flap because the GRU's hidden
state still carries the scar from t=1.

---

## Part A — Spatial Step (run at every time step independently)

**What the spatial loop accomplishes:** At each time step, the STGNN takes a snapshot of
the network graph and runs the full HetGNN spatial pass — typed projections, then
`HeteroConv(SAGEConv)` message passing. The output is a spatial embedding for each node
at that instant: a compressed representation of that node's own state *and* what its
neighbours are doing.

The loop runs T times, producing a sequence of spatial embeddings `[v_B^0, v_B^1, v_B^2]`
for each node. This sequence is then handed to the GRU — the spatial step "translates"
raw features into latent space at each tick; the GRU "reads" the translated sequence and
builds a memory of how the network has evolved.

The spatial step is identical to the HetGNN walkthrough: typed projections +
`HeteroConv(SAGEConv)`. We focus on the BGP branch.

**Code path (from `forward()` in `stgnn.py` / `simple_stgnn_pinpointing.py`):**
```python
for t in range(T_local):
    x_t = {nt: x[:, t, :] for nt, x in x_dict_seq.items()}   # slice at t
    h_t = {nt: self.lin_dict[nt](x_t[nt]).relu() ...}         # typed projection
    for conv in self.convs:
        h_updated = conv(h_t, filtered)                         # SAGEConv
    histories[nt].append(h_t[nt])                              # save for GRU
```

### Projection weights

```
W_proj_B [3→2] (BGP projection):
  bgp_state  → [[ 0.8,  0.4 ]]
  pfx_count  → [[ 0.2,  0.6 ]]
  uptime     → [[ 0.1,  0.3 ]]

W_msg_RB [2→2] (Router → BGP message weight, inside SAGEConv):
  h0 → [[ 0.3,  0.4 ]]
  h1 → [[ 0.5,  0.2 ]]

W_upd_B [2→2] (SAGEConv update = self + neigh combined):
  h0 → [[ 0.7,  0.3 ]]
  h1 → [[ 0.4,  0.6 ]]
```

### A.1 — BGP projection at each time step

**t=0 (healthy: [1.0, 0.50, 0.80]):**
```
h_B_proj^0:
  h0: 1.0×0.8 + 0.50×0.2 + 0.80×0.1  =  0.800 + 0.100 + 0.080  =  0.980
  h1: 1.0×0.4 + 0.50×0.6 + 0.80×0.3  =  0.400 + 0.300 + 0.240  =  0.940

h_B_proj^0 = ReLU([0.980, 0.940]) = [0.980, 0.940]
```

**t=1 (fault: [0.0, 0.0, 0.0]):**
```
h_B_proj^1:
  h0: 0.0×0.8 + 0.0×0.2 + 0.0×0.1  =  0.000
  h1: 0.0×0.4 + 0.0×0.6 + 0.0×0.3  =  0.000

h_B_proj^1 = [0.000, 0.000]  ← completely zeroed
```

**t=2 (recovered: [1.0, 0.50, 0.80]):**
```
h_B_proj^2 = [0.980, 0.940]  ← identical to t=0
```

### A.2 — Router → BGP message (SAGEConv neighbour part)

Router pe1 has cpu=0.22, mem=0.30, ospf=1.0 at all 3 time steps (unchanged).
After its own projection (W_proj_R), the router embedding is constant:
```
h_R[pe1] = [0.300, 0.464]   (same at t=0, 1, 2)
```

SAGEConv neighbour message from router to BGP:
```
neigh_msg = h_R[pe1] @ W_msg_RB:
  m0: 0.300×0.3 + 0.464×0.5  =  0.090 + 0.232  =  0.322
  m1: 0.300×0.4 + 0.464×0.2  =  0.120 + 0.093  =  0.213

neigh_msg = [0.322, 0.213]   (same at t=0, 1, 2 — router never changes)
```

### A.3 — SAGEConv update: spatial embedding v_B^t

`v_B^t = ReLU( (h_B_proj^t + neigh_msg) @ W_upd_B )`

**At t=0 (healthy):**
```
sum = [0.980 + 0.322,  0.940 + 0.213] = [1.302,  1.153]

v_B^0:
  v0: 1.302×0.7 + 1.153×0.4  =  0.911 + 0.461  =  1.372
  v1: 1.302×0.3 + 1.153×0.6  =  0.391 + 0.692  =  1.083

v_B^0 = ReLU([1.372, 1.083]) = [1.372, 1.083]
```

**At t=1 (fault — BGP zeroed):**
```
sum = [0.000 + 0.322,  0.000 + 0.213] = [0.322,  0.213]
                                          ↑ only the router's message remains

v_B^1:
  v0: 0.322×0.7 + 0.213×0.4  =  0.225 + 0.085  =  0.310
  v1: 0.322×0.3 + 0.213×0.6  =  0.097 + 0.128  =  0.225

v_B^1 = ReLU([0.310, 0.225]) = [0.310, 0.225]
```

**At t=2 (recovered):**
```
v_B^2 = [1.372, 1.083]   (identical to t=0)
```

**Spatial embedding sequence for pe1_bgp:**
```
t=0: v_B^0 = [1.372, 1.083]   ← healthy
t=1: v_B^1 = [0.310, 0.225]   ← fault  (massive drop — 77% reduction in dim 0)
t=2: v_B^2 = [1.372, 1.083]   ← recovered (features back to normal)
```

**Why the fault is visible here but not after the GRU:** At this point the sequence clearly
shows the dip at t=1. A static HetGNN applied to any *single* step would score v_B^0 and
v_B^2 as healthy (they are identical) and would correctly score v_B^1 as anomalous — but
only if it happened to be running at exactly the moment the session was down. In a 5-second
polling interval, a BGP flap lasting 2 seconds might not land in any single snapshot. The
STGNN avoids this by passing the *entire 3-step sequence* into the GRU, so the fault at
t=1 is preserved in memory even if the collector missed it at the instant it occurred.

A static HetGNN at t=2 sees `v_B^2 = [1.372, 1.083]` and scores 0. The STGNN
feeds the full sequence `[v_B^0, v_B^1, v_B^2]` into the GRU.

---

## Part B — Temporal Step: nn.GRU  (`self.rnns[nt]`)

**What the GRU does:** A GRU is a compact recurrent neural network that maintains a
"hidden state" `m_t` — a fixed-size memory vector that is updated at every time step.
At each step it decides: (1) how much of the current input to absorb into memory (update
gate z), and (2) how much of the old memory to retain when forming the candidate new state
(reset gate r). This gating mechanism is what allows the GRU to selectively hold on to
important past events.

The update gate z controls how much the hidden state changes. When z ≈ 1 the state
replaces itself with the new candidate; when z ≈ 0 it mostly keeps the old memory. After
a healthy baseline is established (t=0, m_1 is stable), the fault at t=1 produces a very
different spatial embedding (v_B^1 = [0.310, 0.225] vs [1.372, 1.083]). The GRU's update
gate will partially absorb this, leaving a displaced hidden state m_2 that does not match
what the GRU would have produced from a fully healthy sequence. That displacement persists
into t=2 even though the input at t=2 has fully recovered.

**Code path:**
```python
h_seq = torch.stack(histories[nt], dim=1)    # [N, T, H] — N nodes, T steps, H hidden
rnn_out, h_n = self.rnns[nt](h_seq, h_state) # GRU: rnn_out [N,T,H], h_n [1,N,H]
final_emb = rnn_out[:, -1, :]                # [N, H] — trajectory summary embedding
```

For pe1_bgp: N=1, T=3, H=2. The GRU treats the N=1 node as a batch of size 1.

### GRU equations

**Gate intuitions:**
- **Update gate z** — "Should I update my memory?" A high z (→1) means the current input
  is important and the hidden state should shift toward it. A low z (→0) means the model
  should mostly preserve what it already remembers. A sudden anomalous input (v_B^1 at
  fault) will produce a different z than during healthy operation, causing the hidden state
  to shift in an unusual direction.
- **Reset gate r** — "How much of my old memory is relevant to forming the new candidate?"
  A high r lets old memory influence the candidate (good for stable sequences). A low r
  ignores old memory (useful for detecting sudden state changes). During the fault step,
  the mismatch between old memory (healthy) and current input (fault) causes the gates to
  produce values they were not trained to produce, compounding the displacement.
- **Candidate h̃** — The proposed new state, computed as a mix of the current input and
  selectively gated old memory.
- **New state m** — A convex blend: keep `(1-z)` of the old state and absorb `z` of the
  candidate. This is the value that accumulates and is passed to the next time step.

```
z_t = σ( v_t @ W_z  +  m_{t-1} @ U_z )          ← update gate:  how much to replace
r_t = σ( v_t @ W_r  +  m_{t-1} @ U_r )          ← reset gate:   how much history to use
h̃_t = tanh( v_t @ W_h  +  (r_t ⊙ m_{t-1}) @ U_h ) ← candidate new state
m_t = (1 - z_t) ⊙ m_{t-1}  +  z_t ⊙ h̃_t        ← new hidden state
```

**GRU weights** (all [2,2]):
```
W_z = [[ 0.4, -0.1],   U_z = [[ 0.5,  0.1],
        [ 0.2,  0.3]]          [-0.1,  0.4]]

W_r = [[ 0.3,  0.2],   U_r = [[ 0.4,  0.1],
        [ 0.1, -0.2]]          [ 0.2,  0.3]]

W_h = [[-0.3,  0.5],   U_h = [[ 0.6,  0.2],
        [ 0.4,  0.2]]          [-0.1,  0.5]]
```

**Initial hidden state:** `m_0 = [0.0, 0.0]`

---

### B.1 — GRU at t=0  (v_B^0 = [1.372, 1.083], m_0 = [0, 0])

**Update gate z_0:**
```
v @ W_z:
  z0: 1.372×0.4 + 1.083×0.2  =  0.549 + 0.217  =  0.766
  z1: 1.372×(-0.1) + 1.083×0.3  =  -0.137 + 0.325  =  0.188

m_0 @ U_z = [0, 0] @ U_z = [0.000, 0.000]

z_0 = σ([0.766, 0.188])
  σ(0.766): e^(-0.766) ≈ 0.465 → 1/1.465 = 0.683
  σ(0.188): e^(-0.188) ≈ 0.828 → 1/1.828 = 0.547

z_0 = [0.683, 0.547]
```

**Reset gate r_0:**
```
v @ W_r:
  r0: 1.372×0.3 + 1.083×0.1  =  0.412 + 0.108  =  0.520
  r1: 1.372×0.2 + 1.083×(-0.2)  =  0.274 - 0.217  =  0.057

m_0 @ U_r = [0.000, 0.000]

r_0 = σ([0.520, 0.057])
  σ(0.520) ≈ 0.627
  σ(0.057) ≈ 0.514

r_0 = [0.627, 0.514]
```

**Candidate state h̃_0:**
```
v @ W_h:
  h0: 1.372×(-0.3) + 1.083×0.4  =  -0.412 + 0.433  =  0.021
  h1: 1.372×0.5    + 1.083×0.2  =   0.686 + 0.217  =  0.903

r_0 ⊙ m_0 = [0.627×0, 0.514×0] = [0.000, 0.000]
[0.000, 0.000] @ U_h = [0.000, 0.000]

h̃_0 = tanh([0.021+0.000, 0.903+0.000]) = tanh([0.021, 0.903])
  tanh(0.021) ≈  0.021
  tanh(0.903) ≈  0.717

h̃_0 = [0.021, 0.717]
```

**New hidden state m_1:**
```
m_1 = (1 - z_0) ⊙ m_0  +  z_0 ⊙ h̃_0
    = [0.317, 0.453] ⊙ [0.000, 0.000]  +  [0.683, 0.547] ⊙ [0.021, 0.717]
    = [0.000, 0.000]  +  [0.014,  0.392]

m_1 = [0.014, 0.392]
```

After t=0 the GRU is in a state consistent with a healthy BGP session. `m_1 = [0.014, 0.392]`
represents the model's "expectation" of what a healthy BGP session looks like in this latent
space. This is the baseline the fault at t=1 will displace.

---

### B.2 — GRU at t=1  (v_B^1 = [0.310, 0.225], m_1 = [0.014, 0.392])

**The fault step.** The input has dropped from [1.372, 1.083] to [0.310, 0.225] — a 77%
reduction in dim 0. The GRU's update gate is driven by both the current input and the
previous hidden state. Because both the input magnitude and the hidden-state magnitude are
now mismatched relative to training, the resulting gates produce a hidden state `m_2` that
is nudged away from the healthy trajectory. The key quantity to watch is dim 1: m_1 was
0.392, and after absorbing the fault m_2 should drop measurably.

**Update gate z_1:**
```
v @ W_z:
  z0: 0.310×0.4 + 0.225×0.2  =  0.124 + 0.045  =  0.169
  z1: 0.310×(-0.1) + 0.225×0.3  =  -0.031 + 0.068  =  0.037

m_1 @ U_z = [0.014, 0.392] @ [[0.5, 0.1], [-0.1, 0.4]]:
  uz0: 0.014×0.5 + 0.392×(-0.1)  =  0.007 - 0.039  =  -0.032
  uz1: 0.014×0.1 + 0.392×0.4    =  0.001 + 0.157  =   0.158

z_1 = σ([0.169+(-0.032),  0.037+0.158]) = σ([0.137, 0.195])
  σ(0.137) ≈ 0.534
  σ(0.195) ≈ 0.549

z_1 = [0.534, 0.549]
```

**Reset gate r_1:**
```
v @ W_r:
  r0: 0.310×0.3 + 0.225×0.1  =  0.093 + 0.023  =  0.116
  r1: 0.310×0.2 + 0.225×(-0.2)  =  0.062 - 0.045  =  0.017

m_1 @ U_r = [0.014, 0.392] @ [[0.4, 0.1], [0.2, 0.3]]:
  ur0: 0.014×0.4 + 0.392×0.2  =  0.006 + 0.078  =  0.084
  ur1: 0.014×0.1 + 0.392×0.3  =  0.001 + 0.118  =  0.119

r_1 = σ([0.116+0.084,  0.017+0.119]) = σ([0.200, 0.136])
  σ(0.200) ≈ 0.550
  σ(0.136) ≈ 0.534

r_1 = [0.550, 0.534]
```

**Candidate state h̃_1:**
```
v @ W_h:
  h0: 0.310×(-0.3) + 0.225×0.4  =  -0.093 + 0.090  =  -0.003
  h1: 0.310×0.5    + 0.225×0.2  =   0.155 + 0.045  =   0.200

r_1 ⊙ m_1 = [0.550×0.014,  0.534×0.392] = [0.008,  0.209]

[0.008, 0.209] @ U_h = [0.008, 0.209] @ [[0.6, 0.2], [-0.1, 0.5]]:
  uh0: 0.008×0.6 + 0.209×(-0.1)  =  0.005 - 0.021  =  -0.016
  uh1: 0.008×0.2 + 0.209×0.5    =  0.002 + 0.105  =   0.107

h̃_1 = tanh([-0.003+(-0.016),  0.200+0.107])
      = tanh([-0.019,  0.307])
  tanh(-0.019) ≈ -0.019
  tanh( 0.307) ≈  0.298

h̃_1 = [-0.019, 0.298]
```

**New hidden state m_2:**
```
m_2 = (1 - z_1) ⊙ m_1  +  z_1 ⊙ h̃_1
    = [0.466, 0.451] ⊙ [0.014, 0.392]  +  [0.534, 0.549] ⊙ [-0.019, 0.298]
    = [0.007,  0.177]  +  [-0.010,  0.164]

m_2 = [-0.003, 0.341]
```

The fault has **pulled dim 1 down**: m_1 was [0.014, 0.392], m_2 is [-0.003, 0.341].
A 13% drop in dim 1 (0.392 → 0.341) because the BGP signal was replaced with near-zero.

The GRU did not fully "forget" — z_1 ≈ 0.54 means it kept about 46% of the old healthy
state and absorbed 54% of the (faulty) new candidate. This partial absorption is exactly
the right behaviour: the model cannot simply ignore the fault, but it also does not
catastrophically overwrite all of its memory in one step. The scar is real but partial.

---

### B.3 — GRU at t=2  (v_B^2 = [1.372, 1.083], m_2 = [-0.003, 0.341])

**The recovery step.** The spatial input is back to its healthy value — identical to t=0.
If the GRU had no memory, m_3 would equal m_1 (same input, same starting state). But the
starting state is now m_2 = [-0.003, 0.341] rather than m_1 = [0.014, 0.392]. Because
`U_z` and `U_r` multiply the hidden state into the gate computations, the same input
produces *different gate values* depending on the starting hidden state. m_3 will
therefore differ from what a purely healthy sequence would produce — and that difference
is the signal the decoder uses to flag the anomaly.

v_B^2 is identical to v_B^0 — the BGP session has recovered. But m_2 is different
from m_0 (m_0 = [0,0], m_2 = [-0.003, 0.341]), so the gating will differ.

**Update gate z_2:**
```
v @ W_z = [0.766, 0.188]   (same as t=0, same input)

m_2 @ U_z = [-0.003, 0.341] @ [[0.5, 0.1], [-0.1, 0.4]]:
  uz0: (-0.003)×0.5 + 0.341×(-0.1)  =  -0.002 - 0.034  =  -0.036
  uz1: (-0.003)×0.1 + 0.341×0.4    =  -0.000 + 0.136  =   0.136

z_2 = σ([0.766+(-0.036),  0.188+0.136]) = σ([0.730, 0.324])
  σ(0.730) ≈ 0.675
  σ(0.324) ≈ 0.580

z_2 = [0.675, 0.580]
```

**Reset gate r_2:**
```
v @ W_r = [0.520, 0.057]   (same as t=0)

m_2 @ U_r = [-0.003, 0.341] @ [[0.4, 0.1], [0.2, 0.3]]:
  ur0: (-0.003)×0.4 + 0.341×0.2  =  -0.001 + 0.068  =  0.067
  ur1: (-0.003)×0.1 + 0.341×0.3  =  -0.000 + 0.102  =  0.102

r_2 = σ([0.520+0.067,  0.057+0.102]) = σ([0.587, 0.159])
  σ(0.587) ≈ 0.643
  σ(0.159) ≈ 0.540

r_2 = [0.643, 0.540]
```

**Candidate state h̃_2:**
```
v @ W_h = [0.021, 0.903]   (same as t=0)

r_2 ⊙ m_2 = [0.643×(-0.003),  0.540×0.341] = [-0.002,  0.184]

[-0.002, 0.184] @ U_h:
  uh0: (-0.002)×0.6 + 0.184×(-0.1)  =  -0.001 - 0.018  =  -0.019
  uh1: (-0.002)×0.2 + 0.184×0.5    =  -0.000 + 0.092  =   0.092

h̃_2 = tanh([0.021+(-0.019),  0.903+0.092])
      = tanh([0.002,  0.995])
  tanh(0.002) ≈  0.002
  tanh(0.995) ≈  0.762

h̃_2 = [0.002, 0.762]
```

**Final hidden state m_3:**
```
m_3 = (1 - z_2) ⊙ m_2  +  z_2 ⊙ h̃_2
    = [0.325, 0.420] ⊙ [-0.003, 0.341]  +  [0.675, 0.580] ⊙ [0.002, 0.762]
    = [-0.001,  0.143]  +  [0.001,  0.442]

m_3 = [0.000, 0.585]
```

---

### B.4 — Hidden state timeline

```
         dim 0    dim 1   Event
m_0  =  [ 0.000,  0.000 ]  initial
m_1  =  [ 0.014,  0.392 ]  t=0 healthy
m_2  =  [-0.003,  0.341 ]  t=1 FAULT    ← dim 1 drops 13% (0.392→0.341)
m_3  =  [ 0.000,  0.585 ]  t=2 recovered (dim 1 = 0.585 vs ~0.490 after pure healthy — overshoots)
```

**Reading the timeline:** The GRU does not return to its "pure healthy after 2 steps"
trajectory. A purely healthy 3-step sequence (same inputs, starting from m_0=[0,0]) would
produce m after step 2 ≈ [0.018, 0.490]. The flap-sequence instead yields m_3 = [0.000, 0.585].

The overshoot in dim 1 (0.585 > 0.490) is not a bug — it reflects how the GRU compensated
for the dip. At t=2, the input is the same healthy value as t=0, but the update gate z_2
computes slightly differently because m_2 is slightly depressed. The candidate h̃_2 is
similar to h̃_0, but its contribution is blended into a smaller m_2, producing a larger
final result. The net displacement — anywhere that m_3 ≠ the healthy m — is the anomaly
signal the decoder picks up.

---

## Part C — Decoder and Anomaly Score

### C.1 — Reconstruction from displaced m_3

**Why the decoder reveals the anomaly even when t=2 looks healthy:** During training the
decoder was shown thousands of healthy sequences. Every time it saw a healthy 3-step BGP
sequence, the GRU produced a final hidden state ≈ [0.018, 0.490] (the "healthy trajectory
endpoint"). The decoder learned exactly one mapping: [0.018, 0.490] → [1.0, 0.50, 0.80].

At inference, m_3 = [0.000, 0.585]. The dim 1 value is 0.585 instead of 0.490 — a 19%
overshoot. The decoder has never been trained on [0.000, 0.585] and its extrapolation
to BGP features will miss the mark. Even though the actual BGP features at t=2 are a
perfect [1.0, 0.50, 0.80], the decoder predicts something wrong — and *that mismatch is
the anomaly score*, not anything directly observable from the t=2 features themselves.

This is the fundamental insight: **the score is about the trajectory, not the snapshot**.

```python
# In forward():
final_emb = rnn_out[:, -1, :]          # m_3 = [0.000, 0.585]
recon['bgp_session'] = self.decoder_dict['bgp_session'](final_emb)
```

The decoder was trained to map the healthy GRU trajectory endpoint (≈[0.018, 0.490])
to healthy BGP features [1.0, 0.50, 0.80].

Faced with the displaced m_3 = [0.000, 0.585]:

```
W_dec_B [2→3]:
  h0 → [[ 0.9,  0.3,  0.4 ]]
  h1 → [[ 0.5,  0.8,  0.6 ]]

b_dec_B = [-0.1,  0.05,  0.10]

recon_B:
  bgp:  0.000×0.9 + 0.585×0.5 + (-0.1)  =  0.000 + 0.293 - 0.100  =  0.193
  pfx:  0.000×0.3 + 0.585×0.8 +  0.05   =  0.000 + 0.468 + 0.050  =  0.518
  upt:  0.000×0.4 + 0.585×0.6 +  0.10   =  0.000 + 0.351 + 0.100  =  0.451

recon_B = [0.193, 0.518, 0.451]
```

### C.2 — Per-feature squared error

Compare against the ACTUAL final snapshot X_B^2 = [1.0, 0.50, 0.80]:

```
Feature       Actual   Predicted   Difference   Error²
bgp_state      1.000      0.193       0.807       0.651
pfx_count      0.500      0.518      -0.018       0.000
uptime_norm    0.800      0.451       0.349       0.122

MSE_bgp = (0.651 + 0.000 + 0.122) / 3 = 0.258
```

### C.3 — Branch scores

Router and Interface had flat healthy sequences — their GRUs are in their
trained stable states:

```
MSE_router      ≈ 0.000   ✓ normal
MSE_interface   ≈ 0.000   ✓ normal
MSE_bgp         = 0.258   ⚠️  FAULT LAYER
```

**The key insight:** The actual feature values at t=2 are [1.0, 0.50, 0.80] — identical
to healthy. A static HetGNN processing only t=2 would reconstruct these correctly and
give MSE ≈ 0. The STGNN's GRU carries the memory of t=1's fault forward, and the
decoder cannot bridge from the displaced hidden state back to the healthy features.

---

## Part D — Training objective

**Why train on the final step only?** The loss is compared against `x_seq[:, -1, :]` — the
actual last snapshot. This trains the GRU to summarise the full T-step history into a single
embedding from which the decoder can recover the most recent state. If the sequence was
stable (all healthy steps), the summary embedding is compact and the decoder reconstructs
accurately. If the sequence contained a disruption, the summary embedding is displaced, and
the decoder's output drifts away from the actual final-step features. Measuring error against
the actual final step makes the loss sensitive to *what the network just did*, not an average
over the whole window.

**The key contrast with the static HetGNN loss:**

| Model | Loss signal | What it is sensitive to |
|---|---|---|
| HetGNN | MSE(recon, x_snapshot) | Current snapshot anomaly |
| STGNN | MSE(recon, x_seq[:,-1,:]) | Trajectory-induced displacement |

For the BGP flap: HetGNN at t=2 computes MSE(decoder(embed_t2), [1.0,0.50,0.80]) ≈ 0
because embed_t2 from a fresh HetGNN pass is healthy. STGNN computes
MSE(decoder(m_3), [1.0,0.50,0.80]) = 0.258 because m_3 was displaced by the fault at t=1.

```python
# In train():
for x_seq in sequences:             # healthy sequences only, [N, T, F]
    recon_dict, _, _ = model(x_seq, edge_index_dict)

    loss_r = MSELoss(recon_dict['router'],      x_seq['router'][:,      -1, :])
    loss_i = MSELoss(recon_dict['interface'],   x_seq['interface'][:,   -1, :])
    loss_b = MSELoss(recon_dict['bgp_session'], x_seq['bgp_session'][:, -1, :])
    loss   = (loss_r + loss_i + loss_b) / 3.0
```

The model predicts the **final time step** features from the full sequence.
During healthy training, the GRU learns to compress the stable trajectory into
a fixed hidden state that the decoder can accurately invert.
Any disruption in the sequence (fault at t=1) displaces this state — the decoder
detects the mismatch.

---

## Summary of every formula

| Step | Formula | PyTorch code |
|------|---------|-------------|
| Typed projection | h_nt = ReLU(x_t @ W_proj_nt) | `self.lin_dict[nt](x_t[nt]).relu()` |
| Spatial conv | HeteroConv(SAGEConv) at each t | `for conv in self.convs: conv(h_t, filtered)` |
| Stack history | h_seq = [v^0, v^1, ..., v^{T-1}] | `torch.stack(histories[nt], dim=1)` → [N,T,H] |
| Update gate | z_t = σ(v_t@W_z + m_{t-1}@U_z) | inside `nn.GRU` |
| Reset gate | r_t = σ(v_t@W_r + m_{t-1}@U_r) | inside `nn.GRU` |
| Candidate | h̃_t = tanh(v_t@W_h + (r_t⊙m_{t-1})@U_h) | inside `nn.GRU` |
| Hidden state | m_t = (1-z_t)⊙m_{t-1} + z_t⊙h̃_t | inside `nn.GRU` |
| Final emb | final_emb = rnn_out[:, -1, :] | `rnn_out[:, -1, :]` |
| Decode | recon = final_emb @ W_dec + b | `self.decoder_dict[nt](final_emb)` |
| Score | MSE(recon[nt], x_seq[nt][:,-1,:]) | `((recon-actual_final)**2).mean(dim=1)` |
| Streaming | pass h_n between windows | `hidden_state_dict` parameter in `forward()` |
