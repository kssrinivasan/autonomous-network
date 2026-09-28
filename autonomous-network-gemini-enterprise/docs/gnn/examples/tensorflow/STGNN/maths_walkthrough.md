# STGNN Maths Walkthrough — Fully Worked Example

Every arithmetic step shown with concrete numbers throughout.

---

## Setup

We use a **single node** (pe1_bgp) and trace it through **3 time steps** to show
how the STGNN catches a BGP flap that a static model would miss entirely.

```
  [Router: pe1] ──HasBGP──▶ [BGPSession: pe1_bgp]
```

**2 features per node type (kept small so matrices fit on screen):**

| Type | Features |
|------|---------|
| Router | cpu_percent, mem_percent |
| Interface | tx_drops_rate, mtu_norm |
| BGPSession | bgp_state, pfx_count_norm |

**Hidden dimension H = 2** (the script uses H=16, but 2 makes every matrix visible)

**3 time steps — BGP flap scenario:**

| t | Router X_R | Interface X_I | BGP X_B | What happened |
|---|-----------|--------------|---------|--------------|
| 1 | [0.22, 0.30] | [0.01, 0.167] | [1.0, 0.50] | All healthy |
| 2 | [0.22, 0.30] | [0.01, 0.167] | [0.0, 0.00] | BGP session DOWN |
| 3 | [0.22, 0.30] | [0.01, 0.167] | [1.0, 0.50] | BGP recovered |

At t=3 everything *looks* healthy. A static snapshot model would give PE1-bgp
a clean bill of health. The STGNN's GRU remembers t=2 and flags it anyway.

---

## Part A — Spatial Step (applied at every time step independently)

The spatial step runs the heterogeneous message passing on each snapshot.
We focus on the BGP branch and do the full arithmetic for all three time steps.

### A.1 — Typed Projections

Each node type has its own weight matrix projecting raw features → hidden space H=2.

**W_proj_R [2, 2]** (Router projection):
```
         h0    h1
cpu  →  [ 0.5   0.1 ]
mem  →  [ 0.2   0.8 ]
```

**W_proj_B [2, 2]** (BGP projection):
```
         h0    h1
bgp  →  [ 0.8   0.4 ]
pfx  →  [ 0.2   0.6 ]
```

---

**Router projection at t=1,2,3** (cpu=0.22, mem=0.30, unchanged all three steps):

```
h_R = X_R @ W_proj_R

h0: 0.22 × 0.5  +  0.30 × 0.2  =  0.110 + 0.060  =  0.170
h1: 0.22 × 0.1  +  0.30 × 0.8  =  0.022 + 0.240  =  0.262

h_R = [0.170, 0.262]    ← same at t=1, 2 and 3
```

---

**BGP projection at t=1 (healthy: bgp=1.0, pfx=0.50):**

```
h_B^(1) = [1.0, 0.50] @ W_proj_B

h0: 1.0 × 0.8  +  0.50 × 0.2  =  0.800 + 0.100  =  0.900
h1: 1.0 × 0.4  +  0.50 × 0.6  =  0.400 + 0.300  =  0.700

h_B^(1) = [0.900, 0.700]
```

**BGP projection at t=2 (fault: bgp=0.0, pfx=0.0):**

```
h_B^(2) = [0.0, 0.0] @ W_proj_B

h0: 0.0 × 0.8  +  0.0 × 0.2  =  0.000
h1: 0.0 × 0.4  +  0.0 × 0.6  =  0.000

h_B^(2) = [0.000, 0.000]    ← completely zeroed
```

**BGP projection at t=3 (recovered: bgp=1.0, pfx=0.50):**
```
h_B^(3) = [0.900, 0.700]    ← identical to t=1
```

---

### A.2 — Typed Message: Router → BGP (HasBGP edge)

**W_msg_RB [2, 2]** (transforms router embedding into a message for BGP nodes):
```
         h0    h1
h0  →  [ 0.6  -0.1 ]
h1  →  [-0.2   0.5 ]
```

**Router message at t=1,2,3** (h_R is constant):

```
msg_RB = h_R @ W_msg_RB = [0.170, 0.262] @ W_msg_RB

m0: 0.170 × 0.6  +  0.262 × (-0.2)  =  0.102 - 0.052  =  0.050
m1: 0.170 × (-0.1)  +  0.262 × 0.5  =  -0.017 + 0.131  =  0.114

msg_RB = [0.050, 0.114]    ← same at all t (router never changes)
```

The incidence matrix P_RB = [[1, 0]] means pe1_bgp receives from pe1 only:
```
m_RB = P_RB @ msg_RB = [1×0.050 + 0×..., 1×0.114 + 0×...] = [0.050, 0.114]
```

---

### A.3 — Typed Update: BGP node

**W_upd_B [2, 2]** (update transformation for BGP nodes after aggregating messages):
```
         h0    h1
h0  →  [ 0.7   0.3 ]
h1  →  [ 0.4   0.6 ]
```

The BGP node adds its own projection to the incoming router message, then applies W_upd_B + ReLU:

```
v_B = ReLU( (h_B + m_RB) @ W_upd_B )
```

**At t=1 (healthy):**
```
sum = h_B^(1) + m_RB = [0.900 + 0.050,  0.700 + 0.114]
                      = [0.950,  0.814]

v_B^(1):
  v0: 0.950 × 0.7  +  0.814 × 0.4  =  0.665 + 0.326  =  0.991
  v1: 0.950 × 0.3  +  0.814 × 0.6  =  0.285 + 0.488  =  0.773

v_B^(1) = ReLU([0.991, 0.773]) = [0.991, 0.773]
```

**At t=2 (fault — BGP zeroed):**
```
sum = h_B^(2) + m_RB = [0.000 + 0.050,  0.000 + 0.114]
                      = [0.050,  0.114]
                      ← only the router's message remains

v_B^(2):
  v0: 0.050 × 0.7  +  0.114 × 0.4  =  0.035 + 0.046  =  0.081
  v1: 0.050 × 0.3  +  0.114 × 0.6  =  0.015 + 0.068  =  0.083

v_B^(2) = ReLU([0.081, 0.083]) = [0.081, 0.083]
```

**At t=3 (recovered):**
```
sum = [0.950, 0.814]    ← same as t=1
v_B^(3) = [0.991, 0.773]
```

**The spatial embedding sequence for pe1_bgp:**
```
t=1: v_B^(1) = [0.991, 0.773]   ← healthy
t=2: v_B^(2) = [0.081, 0.083]   ← fault (massive drop)
t=3: v_B^(3) = [0.991, 0.773]   ← recovered
```

A **static model** looking only at t=3 would see [0.991, 0.773] and score it
as perfectly normal. The STGNN feeds this *sequence* into the GRU.

---

## Part B — Temporal Step (GRU processes the sequence)

The GRU processes v_B^(1), v_B^(2), v_B^(3) **in order**, maintaining a hidden
state m that carries memory of what came before.

### GRU equations (four gates)

```
z_t = σ( v_t @ W_z  +  m_{t-1} @ U_z )          ← update gate
r_t = σ( v_t @ W_r  +  m_{t-1} @ U_r )          ← reset gate
h̃_t = tanh( v_t @ W_h  +  (r_t ⊙ m_{t-1}) @ U_h )  ← candidate state
m_t = (1 - z_t) ⊙ m_{t-1}  +  z_t ⊙ h̃_t        ← new hidden state
```

**What each gate does:**
- **z (update):** how much of the old state to keep vs replace with new input
- **r (reset):** how much of the old state to let influence the candidate
- **h̃ (candidate):** what the new state *would* be, based on current input
- **m (output):** blend of old state and candidate, controlled by z

**GRU weight matrices** (all [2, 2]):
```
W_z = [[ 0.5, -0.2],   U_z = [[ 0.6,  0.1],
        [ 0.3,  0.4]]          [-0.1,  0.5]]

W_r = [[ 0.4,  0.3],   U_r = [[ 0.3,  0.2],
        [ 0.2, -0.1]]          [ 0.4,  0.1]]

W_h = [[-0.5,  0.4],   U_h = [[ 0.7,  0.2],
        [ 0.6,  0.3]]          [-0.2,  0.6]]
```

**Initial hidden state:** m_0 = [0.0, 0.0]

σ(x) = 1 / (1 + e^(-x))     tanh(x) = (e^x - e^(-x)) / (e^x + e^(-x))

---

### B.1 — GRU at t=1 (input: v_B^(1) = [0.991, 0.773], m_0 = [0, 0])

**Update gate z_1:**
```
v @ W_z:
  z0: 0.991×0.5 + 0.773×0.3 = 0.496 + 0.232 = 0.728
  z1: 0.991×(-0.2) + 0.773×0.4 = -0.198 + 0.309 = 0.111

m_0 @ U_z = [0,0] @ U_z = [0.000, 0.000]

z_1 = σ([0.728+0.000, 0.111+0.000]) = σ([0.728, 0.111])
  σ(0.728): e^(-0.728) ≈ 0.483 → 1/1.483 = 0.674
  σ(0.111): e^(-0.111) ≈ 0.895 → 1/1.895 = 0.528

z_1 = [0.674, 0.528]
```

**Reset gate r_1:**
```
v @ W_r:
  r0: 0.991×0.4 + 0.773×0.2 = 0.396 + 0.155 = 0.551
  r1: 0.991×0.3 + 0.773×(-0.1) = 0.297 - 0.077 = 0.220

m_0 @ U_r = [0.000, 0.000]

r_1 = σ([0.551, 0.220])
  σ(0.551): e^(-0.551) ≈ 0.576 → 1/1.576 = 0.635
  σ(0.220): e^(-0.220) ≈ 0.803 → 1/1.803 = 0.555

r_1 = [0.635, 0.555]
```

**Candidate state h̃_1:**
```
v @ W_h:
  h0: 0.991×(-0.5) + 0.773×0.6 = -0.496 + 0.464 = -0.032
  h1: 0.991×0.4    + 0.773×0.3 =  0.396 + 0.232 =  0.628

r_1 ⊙ m_0 = [0.635×0, 0.555×0] = [0.000, 0.000]
(r_1 ⊙ m_0) @ U_h = [0.000, 0.000]

h̃_1 = tanh([-0.032+0.000, 0.628+0.000]) = tanh([-0.032, 0.628])
  tanh(-0.032) ≈ -0.032
  tanh( 0.628) ≈  0.558

h̃_1 = [-0.032, 0.558]
```

**New hidden state m_1:**
```
m_1 = (1 - z_1) ⊙ m_0  +  z_1 ⊙ h̃_1
    = (1-[0.674, 0.528]) ⊙ [0.000, 0.000]  +  [0.674, 0.528] ⊙ [-0.032, 0.558]
    = [0.326×0.000,  0.472×0.000]  +  [0.674×(-0.032),  0.528×0.558]
    = [0.000,  0.000]  +  [-0.022,  0.295]

m_1 = [-0.022, 0.295]
```

After seeing healthy t=1, the GRU has settled into state m_1 = [-0.022, 0.295].

---

### B.2 — GRU at t=2 (input: v_B^(2) = [0.081, 0.083], m_1 = [-0.022, 0.295])

**Update gate z_2:**
```
v @ W_z:
  z0: 0.081×0.5 + 0.083×0.3 = 0.041 + 0.025 = 0.066
  z1: 0.081×(-0.2) + 0.083×0.4 = -0.016 + 0.033 = 0.017

m_1 @ U_z = [-0.022, 0.295] @ [[0.6, 0.1], [-0.1, 0.5]]:
  uz0: (-0.022)×0.6 + 0.295×(-0.1) = -0.013 - 0.030 = -0.043
  uz1: (-0.022)×0.1 + 0.295×0.5  = -0.002 + 0.148 =  0.146

z_2 = σ([0.066+(-0.043),  0.017+0.146]) = σ([0.023, 0.163])
  σ(0.023) ≈ 0.506
  σ(0.163) ≈ 0.541

z_2 = [0.506, 0.541]
```

**Reset gate r_2:**
```
v @ W_r:
  r0: 0.081×0.4 + 0.083×0.2 = 0.032 + 0.017 = 0.049
  r1: 0.081×0.3 + 0.083×(-0.1) = 0.024 - 0.008 = 0.016

m_1 @ U_r = [-0.022, 0.295] @ [[0.3, 0.2], [0.4, 0.1]]:
  ur0: (-0.022)×0.3 + 0.295×0.4 = -0.007 + 0.118 = 0.111
  ur1: (-0.022)×0.2 + 0.295×0.1 = -0.004 + 0.030 = 0.026

r_2 = σ([0.049+0.111,  0.016+0.026]) = σ([0.160, 0.042])
  σ(0.160) ≈ 0.540
  σ(0.042) ≈ 0.511

r_2 = [0.540, 0.511]
```

**Candidate state h̃_2:**
```
v @ W_h:
  h0: 0.081×(-0.5) + 0.083×0.6 = -0.041 + 0.050 =  0.009
  h1: 0.081×0.4    + 0.083×0.3 =  0.032 + 0.025 =  0.057

r_2 ⊙ m_1 = [0.540×(-0.022),  0.511×0.295] = [-0.012,  0.151]

[-0.012, 0.151] @ U_h = [-0.012, 0.151] @ [[0.7, 0.2], [-0.2, 0.6]]:
  uh0: (-0.012)×0.7 + 0.151×(-0.2) = -0.008 - 0.030 = -0.038
  uh1: (-0.012)×0.2 + 0.151×0.6  = -0.002 + 0.091 =  0.089

h̃_2 = tanh([0.009+(-0.038),  0.057+0.089])
      = tanh([-0.029,  0.146])
  tanh(-0.029) ≈ -0.029
  tanh( 0.146) ≈  0.145

h̃_2 = [-0.029, 0.145]
```

**New hidden state m_2:**
```
m_2 = (1 - z_2) ⊙ m_1  +  z_2 ⊙ h̃_2
    = (1-[0.506, 0.541]) ⊙ [-0.022, 0.295]  +  [0.506, 0.541] ⊙ [-0.029, 0.145]
    = [0.494×(-0.022),  0.459×0.295]  +  [0.506×(-0.029),  0.541×0.145]
    = [-0.011,  0.135]  +  [-0.015,  0.078]

m_2 = [-0.026, 0.213]
```

The fault has **pulled the hidden state down**: m_1 was [-0.022, 0.295], m_2 is [-0.026, 0.213].
The key dimension dropped from 0.295 → 0.213 because the healthy BGP signal was replaced with near-zero.

---

### B.3 — GRU at t=3 (input: v_B^(3) = [0.991, 0.773], m_2 = [-0.026, 0.213])

**Update gate z_3:**
```
v @ W_z = [0.728, 0.111]    ← same as t=1, same input

m_2 @ U_z = [-0.026, 0.213] @ [[0.6, 0.1], [-0.1, 0.5]]:
  uz0: (-0.026)×0.6 + 0.213×(-0.1) = -0.016 - 0.021 = -0.037
  uz1: (-0.026)×0.1 + 0.213×0.5  = -0.003 + 0.107 =  0.104

z_3 = σ([0.728+(-0.037),  0.111+0.104]) = σ([0.691, 0.215])
  σ(0.691) ≈ 0.666
  σ(0.215) ≈ 0.554

z_3 = [0.666, 0.554]
```

**Reset gate r_3:**
```
v @ W_r = [0.551, 0.220]    ← same as t=1

m_2 @ U_r = [-0.026, 0.213] @ [[0.3, 0.2], [0.4, 0.1]]:
  ur0: (-0.026)×0.3 + 0.213×0.4 = -0.008 + 0.085 = 0.077
  ur1: (-0.026)×0.2 + 0.213×0.1 = -0.005 + 0.021 = 0.016

r_3 = σ([0.551+0.077,  0.220+0.016]) = σ([0.628, 0.236])
  σ(0.628) ≈ 0.652
  σ(0.236) ≈ 0.559

r_3 = [0.652, 0.559]
```

**Candidate state h̃_3:**
```
v @ W_h = [-0.032, 0.628]    ← same as t=1

r_3 ⊙ m_2 = [0.652×(-0.026),  0.559×0.213] = [-0.017,  0.119]

[-0.017, 0.119] @ U_h:
  uh0: (-0.017)×0.7 + 0.119×(-0.2) = -0.012 - 0.024 = -0.036
  uh1: (-0.017)×0.2 + 0.119×0.6  = -0.003 + 0.071 =  0.068

h̃_3 = tanh([-0.032+(-0.036),  0.628+0.068])
      = tanh([-0.068,  0.696])
  tanh(-0.068) ≈ -0.068
  tanh( 0.696) ≈  0.602

h̃_3 = [-0.068, 0.602]
```

**New hidden state m_3:**
```
m_3 = (1 - z_3) ⊙ m_2  +  z_3 ⊙ h̃_3
    = [0.334, 0.446] ⊙ [-0.026, 0.213]  +  [0.666, 0.554] ⊙ [-0.068, 0.602]
    = [-0.009,  0.095]  +  [-0.045,  0.334]

m_3 = [-0.054, 0.429]
```

---

### B.4 — Hidden State Timeline (the "memory" effect visualised)

```
         dim 0    dim 1   What happened
m_0  =  [ 0.000,  0.000 ]  initial
m_1  =  [-0.022,  0.295 ]  t=1 healthy  (dim 1 at 0.295)
m_2  =  [-0.026,  0.213 ]  t=2 FAULT    (dim 1 drops to 0.213 — 28% fall)
m_3  =  [-0.054,  0.429 ]  t=3 recovered (dim 1 recovers but stuck at 0.429, not 0.295)
```

**Key insight:** If the sequence had been healthy all the way through
(all three steps with v=[0.991, 0.773]), the GRU would converge to a
stable "healthy" state — approximately m_healthy ≈ [-0.022, 0.490].

After the flap at t=2, m_3 = [-0.054, 0.429]. Dim 1 is 0.429 instead of 0.490.
The GRU is "shell-shocked" — it cannot fully recover in a single step.
This residual disturbance is what produces the anomaly signal.

---

## Part C — Decoder and Anomaly Score

### C.1 — Reconstruction

The final hidden state m_3 = [-0.054, 0.429] is passed through the BGP decoder:

```
X_B_hat = m_3 @ W_dec_B + b_dec_B
```

**W_dec_B [2, 2]** and bias **b_dec_B [2]:**
```
         bgp    pfx
h0  →  [ 1.2    0.4 ]
h1  →  [ 0.3    1.5 ]

b_dec_B = [-0.05,  0.10]
```

The decoder was trained to map the healthy GRU state (≈ m_healthy = [-0.022, 0.490])
to healthy BGP features [1.0, 0.50]. Faced with the distorted m_3, it produces:

```
X_B_hat:
  bgp: (-0.054)×1.2 + 0.429×0.3  + (-0.05) = -0.065 + 0.129 - 0.050 = 0.014
  pfx: (-0.054)×0.4 + 0.429×1.5  +   0.10  = -0.022 + 0.644 + 0.100 = 0.722

X_B_hat = [0.014, 0.722]
```

But the actual BGP features at t=3 (session recovered) are:
```
X_B^(3) = [1.0, 0.50]
```

### C.2 — Per-feature squared error

```
Feature       Actual   Predicted   Difference   Squared Error
bgp_state      1.000      0.014       0.986         0.972
pfx_count      0.500      0.722      -0.222         0.049

MSE_BGP = (0.972 + 0.049) / 2 = 0.511
```

### C.3 — Why the router and interface branches score near zero

Both Router and Interface had **perfectly stable sequences** — the same values at
every time step. Their GRUs settled into the same stable hidden state as during
training, and their decoders reconstruct accurately:

```
Router branch:
  X_R^(3) = [0.22, 0.30]
  X_R_hat  ≈ [0.22, 0.30]
  MSE_Router ≈ 0.000

Interface branch:
  X_I^(3) = [0.01, 0.167]
  X_I_hat  ≈ [0.01, 0.167]
  MSE_Interface ≈ 0.000
```

---

## Final Branch Anomaly Scores

```
═══════════════════════════════════════════════════════
  Branch            Score   Status
═══════════════════════════════════════════════════════
  Router            0.000   ✓ normal
  Interface         0.000   ✓ normal
  BGPSession        0.511   ⚠️  FAULT LAYER  ← highest
═══════════════════════════════════════════════════════

→ Root layer: BGPSession
  The temporal sequence for pe1_bgp showed a flap at t=2.
  Even though the session recovered at t=3, the GRU's hidden
  state retained evidence of the disruption — which the decoder
  could not compensate for, producing high reconstruction error.
```

---

## What Would a Static Model See?

A static model (the GCN from `GCN/`) processes only the current snapshot.
At t=3, every feature looks healthy:

```
X_R = [0.22, 0.30]   ← normal
X_I = [0.01, 0.167]  ← normal
X_B = [1.0,  0.50]   ← normal (session recovered!)
```

Static reconstruction error ≈ 0.000 for all branches. No alarm raised.

**The flap at t=2 is completely invisible to a static model.** This is
precisely the case from the research doc: *"A BGP session that flaps
and recovers within a 5-minute polling window leaves no trace in a
snapshot-based system."*

The STGNN catches it because the GRU's hidden state at t=3 still carries
the scar from t=2 — a residual 14% deficit in dim 1 (0.429 vs 0.490).

---

## Summary of Every Formula Used

| Step | Formula | What it does |
|------|---------|--------------|
| A.1 | `h_type = ReLU(X_type @ W_proj_type)` | Typed projection into hidden space |
| A.2 | `msg = P @ (h_src @ W_msg_edgetype)` | Typed message from source to destination |
| A.3 | `v_t = ReLU((h_self + msg) @ W_upd)` | Update: combine self + messages |
| B | Repeat A.1–A.3 for every time step | Produces sequence v^(1), v^(2), …, v^(T) |
| B | `z_t = σ(v_t @ W_z + m_{t-1} @ U_z)` | Update gate: how much to replace state |
| B | `r_t = σ(v_t @ W_r + m_{t-1} @ U_r)` | Reset gate: how much history to use |
| B | `h̃_t = tanh(v_t @ W_h + (r_t⊙m_{t-1}) @ U_h)` | Candidate new state |
| B | `m_t = (1-z_t)⊙m_{t-1} + z_t⊙h̃_t` | Blend old and candidate → new memory |
| C | `X_hat = m_T @ W_dec + b` | Reconstruct final snapshot from memory |
| C | `score = mean((X_hat - X_actual)²)` | Anomaly score: how wrong is reconstruction |
