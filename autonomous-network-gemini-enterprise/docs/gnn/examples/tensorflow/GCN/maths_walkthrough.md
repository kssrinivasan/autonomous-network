# GCN Maths Walkthrough — Worked Example

A step-by-step walk through every equation in `simple_failure_pinpointing.py`
using **concrete numbers** from the hub-and-spoke topology.

To keep the matrices readable, we work with a **3-node slice** of the graph:

```
  pe1  ──  p1  ──  ce1-spoke
```

Nodes:
- Node 0 = `pe1`       (Provider Edge — the faulty router)
- Node 1 = `p1`        (Core P router)
- Node 2 = `ce1-spoke` (Customer Edge)

Features:
- We keep only **2 features** per node to make matrices fit on screen:
  - `tx_drops_rate` (TX drops, log-scaled)
  - `mtu_norm`      (MTU / 9000)

---

## Step 1 — Raw Adjacency Matrix A

We describe the graph structure as a matrix.
`A[i][j] = 1` if there is a physical link between node i and node j, else 0.

```
          pe1  p1  ce1-spoke
pe1   →  [ 0    1    0  ]
p1    →  [ 1    0    1  ]
ce1   →  [ 0    1    0  ]
```

Observations:
- `pe1` connects to `p1`       → A[0][1] = A[1][0] = 1
- `p1`  connects to `ce1-spoke`→ A[1][2] = A[2][1] = 1
- No direct link pe1 ↔ ce1-spoke

Matrix form:

```
    ⎡ 0  1  0 ⎤
A = ⎢ 1  0  1 ⎥
    ⎣ 0  1  0 ⎦
```

---

## Step 2 — Add Self-Loops: A_tilde = A + I

### The problem: neighbourhood aggregation erases a node's own features

The whole point of a GCN is that each node aggregates information from its
neighbours. But look at what happens if we multiply the **raw** adjacency A
(no self-loops) by the feature matrix X:

```
         ⎡ 0  1  0 ⎤   ⎡ 0.01  0.167 ⎤
A · X  = ⎢ 1  0  1 ⎥ · ⎢ 0.01  0.167 ⎥
         ⎣ 0  1  0 ⎦   ⎣ 0.01  0.167 ⎦
```

Row 0 (pe1):  `0×pe1 + 1×p1 + 0×ce1`
```
  tx_drops: 0×0.01 + 1×0.01 + 0×0.01 = 0.01   (p1's value only)
  mtu_norm: 0×0.167+ 1×0.167+ 0×0.167= 0.167  (p1's value only)
```

**pe1's own features have completely vanished.** The output row for pe1 is
100% p1's values, because A[0][0] = 0 (no self-connection in the raw graph).

Row 1 (p1):  `1×pe1 + 0×p1 + 1×ce1`
```
  tx_drops: 1×0.01 + 0×0.01 + 1×0.01 = 0.02   (pe1 + ce1, but p1 itself gone)
  mtu_norm: ...                        = 0.334  (pe1 + ce1, p1 itself gone)
```

After multiplying by A, **no node retains any of its own information** — it
only sees what its neighbours look like. That makes training impossible:
the encoder cannot form a stable latent embedding for a node if the node's
own state is never in the input.

---

### The fix: add self-loops (A + I)

Adding the identity matrix puts a 1 on every diagonal — effectively adding a
"wire from each node to itself". Now every node is its own neighbour.

```
         ⎡ 0  1  0 ⎤   ⎡ 1  0  0 ⎤   ⎡ 1  1  0 ⎤
A_tilde= ⎢ 1  0  1 ⎥ + ⎢ 0  1  0 ⎥ = ⎢ 1  1  1 ⎥
         ⎣ 0  1  0 ⎦   ⎣ 0  0  1 ⎦   ⎣ 0  1  1 ⎦
```

Now re-run the aggregation with A_tilde:

Row 0 (pe1):  `1×pe1 + 1×p1 + 0×ce1`
```
  tx_drops: 1×0.01 + 1×0.01 + 0×0.01 = 0.02   (pe1 + p1)
  mtu_norm: 1×0.167+ 1×0.167+ 0×0.167= 0.334  (pe1 + p1)
```

pe1's own features are now **included** in its aggregated representation.
After the normalisation in Step 4, this becomes a weighted blend of pe1 and p1
rather than p1 alone.

---

### Why this matters for anomaly detection

If a node's own features are erased by the aggregation, a fault on pe1
(e.g. tx_drops = 0.75) would only be "felt" by its neighbours — the
reconstruction error would land on p1, not on pe1. The fault would be
mis-attributed to the neighbour rather than the root cause.

With self-loops, pe1's own anomalous features are always present in its
own row of the aggregated matrix. When the decoder tries to reconstruct
that row, it fails — and the high reconstruction error is correctly assigned
to **pe1**, not to p1.

In summary:

| Without self-loop | With self-loop |
|---|---|
| pe1's row = only p1's features | pe1's row = pe1's own + p1's features |
| Fault on pe1 invisible at pe1's embedding | Fault on pe1 visible in pe1's embedding |
| Anomaly score mis-attributed to neighbours | Anomaly score correctly on pe1 |

---

## Step 3 — Degree Matrix D_tilde

The degree of a node is how many connections it has (after adding self-loops).
We put those degrees on a diagonal matrix D_tilde.

Row sums of A_tilde:
- pe1  (row 0): 1+1+0 = **2**  (connected to itself + p1)
- p1   (row 1): 1+1+1 = **3**  (connected to pe1, itself, ce1-spoke)
- ce1  (row 2): 0+1+1 = **2**  (connected to p1 + itself)

```
            ⎡ 2  0  0 ⎤
D_tilde =   ⎢ 0  3  0 ⎥
            ⎣ 0  0  2 ⎦
```

---

## Step 4 — Normalised Adjacency: A_hat

Raw averaging (A_tilde @ X) has a problem: high-degree nodes (like p1 with 3
connections) contribute very large values; low-degree nodes contribute small ones.
The network would learn different magnitude features depending solely on topology.

The fix is **symmetric normalisation** (Kipf & Welling, 2017):

```
A_hat = D^(-½) · A_tilde · D^(-½)
```

First compute D^(-½): replace each diagonal entry d with 1/√d.

```
            ⎡ 1/√2   0     0   ⎤   ⎡ 0.707   0      0   ⎤
D^(-½) =    ⎢  0    1/√3   0   ⎥ = ⎢  0     0.577   0   ⎥
            ⎣  0     0    1/√2 ⎦   ⎣  0      0     0.707 ⎦
```

Now compute D^(-½) · A_tilde · D^(-½):

```
Step A: D^(-½) · A_tilde
        ⎡ 0.707   0      0   ⎤   ⎡ 1  1  0 ⎤   ⎡ 0.707  0.707   0   ⎤
      = ⎢  0     0.577   0   ⎥ · ⎢ 1  1  1 ⎥ = ⎢ 0.577  0.577  0.577 ⎥
        ⎣  0      0     0.707 ⎦   ⎣ 0  1  1 ⎦   ⎣  0     0.707  0.707 ⎦

Step B: (result) · D^(-½)
        ⎡ 0.707  0.707   0   ⎤   ⎡ 0.707   0      0   ⎤
      = ⎢ 0.577  0.577  0.577 ⎥ · ⎢  0     0.577   0   ⎥
        ⎣  0     0.707  0.707 ⎦   ⎣  0      0     0.707 ⎦

        ⎡ 0.500  0.408    0   ⎤
A_hat = ⎢ 0.408  0.333  0.408 ⎥
        ⎣  0     0.408  0.500 ⎦
```

**What A_hat means, entry by entry:**
- `A_hat[0][0] = 0.500` — pe1 aggregates **half** of its own features
- `A_hat[0][1] = 0.408` — pe1 receives **41%** of p1's features
- `A_hat[1][1] = 0.333` — p1 aggregates **33%** of its own features (more neighbours → less self-weight)
- `A_hat[1][0] = 0.408` — p1 receives **41%** of pe1's features
- `A_hat[1][2] = 0.408` — p1 receives **41%** of ce1's features
- `A_hat[2][1] = 0.408` — ce1 receives **41%** of p1's features
- `A_hat[0][2] = 0.000` — pe1 receives **nothing** from ce1 (not connected)

Higher-degree nodes (p1 with degree 3) receive proportionally less from each
neighbour; lower-degree nodes (pe1, ce1 with degree 2) receive proportionally
more. This keeps the magnitudes balanced.

---

## Step 5 — Feature Matrix X

Now define the node features. Each row is one node; each column is one feature.

**Healthy state** (what training data looks like):

```
             tx_drops  mtu_norm
pe1   →  X = [ 0.01     0.167 ]
p1    →      [ 0.01     0.167 ]
ce1   →      [ 0.01     0.167 ]
```

**Fault state** (pe1's MTU changed to 1400, TX drops spiked):

```
             tx_drops  mtu_norm
pe1   →      [ 0.75     0.156 ]    ← CHANGED
p1    →      [ 0.01     0.167 ]
ce1   →      [ 0.01     0.167 ]
```

---

## Step 6 — GCN Layer 1: Neighbourhood Aggregation

The core GCN operation is:

```
H = ReLU( A_hat · X · W₁ )
```

Let's work through it in two sub-steps.

### Sub-step 6a: A_hat · X  (neighbourhood aggregation)

This is where the **graph structure is applied**: each node's feature row
becomes a weighted average of its own features plus its neighbours' features.

```
        ⎡ 0.500  0.408   0   ⎤   ⎡ 0.01  0.167 ⎤
A_hat·X=⎢ 0.408  0.333  0.408⎥ · ⎢ 0.01  0.167 ⎥
        ⎣  0     0.408  0.500⎦   ⎣ 0.01  0.167 ⎦
```

Row 0 (pe1):
```
  tx_drops:  0.500×0.01 + 0.408×0.01 + 0×0.01   = 0.00908
  mtu_norm:  0.500×0.167+ 0.408×0.167+ 0×0.167  = 0.15161
```

Row 1 (p1):
```
  tx_drops:  0.408×0.01 + 0.333×0.01 + 0.408×0.01 = 0.01149
  mtu_norm:  0.408×0.167+ 0.333×0.167+ 0.408×0.167 = 0.19215
```

Row 2 (ce1):
```
  tx_drops:  0×0.01 + 0.408×0.01 + 0.500×0.01   = 0.00908
  mtu_norm:  0×0.167+ 0.408×0.167+ 0.500×0.167  = 0.15161
```

Result for healthy data — all rows are similar (as expected: everyone healthy):

```
           ⎡ 0.00908  0.15161 ⎤   ← pe1 aggregated
A_hat · X =⎢ 0.01149  0.19215 ⎥   ← p1 aggregated (higher because degree 3)
           ⎣ 0.00908  0.15161 ⎦   ← ce1 aggregated
```

### Sub-step 6b: (A_hat · X) · W₁  (linear transformation)

#### What is W₁?

W₁ is a weight matrix of shape `[F_in → F_hidden]` — in our toy example
`[2 → 4]`. It is the only thing the model actually **learns**. Before training
it is initialised randomly (Glorot/Xavier uniform: values drawn from a small
range based on layer size). Training repeatedly nudges each entry of W₁ via
gradient descent to reduce the reconstruction loss.

Think of each **column** of W₁ as a "detector":
- Column h0 might learn to weight `tx_drops` heavily — it becomes a "drop detector"
- Column h1 might learn to weight `mtu_norm` heavily — an "MTU state detector"
- Column h2 and h3 capture other combinations the model finds useful

After training on healthy data, W₁ encodes the normal relationship between
features. When a fault flips `tx_drops` from 0.01 to 0.75, W₁ produces a
latent vector that has never appeared in training — which the decoder cannot
reconstruct, producing a high anomaly score.

Here is an example W₁ after converging on healthy data:

```
       hidden:   h0      h1      h2      h3
W₁ =  tx_dr  [ 1.20  -0.30   0.80   0.10 ]
       mtu    [ 0.50   1.10  -0.20   0.90 ]
```

---

#### Full calculation: all 3 nodes

We have the aggregated features from Step 6a (recall these are A_hat · X):

```
           ⎡ 0.00908  0.15161 ⎤   ← pe1
A_hat · X =⎢ 0.01149  0.19215 ⎥   ← p1
           ⎣ 0.00908  0.15161 ⎦   ← ce1
```

Now multiply each row by W₁. Each row gives a 4-dimensional result:

**Node 0 — pe1:**
```
agg = [0.00908, 0.15161]

h0: 0.00908×1.20 + 0.15161×0.50 = 0.01090 + 0.07581 =  0.08671
h1: 0.00908×(-0.30) + 0.15161×1.10 = -0.00272 + 0.16677 = 0.16405
h2: 0.00908×0.80 + 0.15161×(-0.20) = 0.00726 - 0.03032 = -0.02306
h3: 0.00908×0.10 + 0.15161×0.90   = 0.00091 + 0.13645 =  0.13736

Pre-ReLU: [ 0.08671,  0.16405, -0.02306,  0.13736]
```

**Node 1 — p1:**
```
agg = [0.01149, 0.19215]

h0: 0.01149×1.20 + 0.19215×0.50 = 0.01379 + 0.09608 =  0.10987
h1: 0.01149×(-0.30) + 0.19215×1.10 = -0.00345 + 0.21137 = 0.20792
h2: 0.01149×0.80 + 0.19215×(-0.20) = 0.00919 - 0.03843 = -0.02924
h3: 0.01149×0.10 + 0.19215×0.90   = 0.00115 + 0.17294 =  0.17409

Pre-ReLU: [ 0.10987,  0.20792, -0.02924,  0.17409]
```

**Node 2 — ce1-spoke:**
```
agg = [0.00908, 0.15161]   (same as pe1 because they're symmetric in healthy state)

Pre-ReLU: [ 0.08671,  0.16405, -0.02306,  0.13736]
```

---

#### Applying ReLU

ReLU(x) = max(0, x). Any negative value becomes 0. Positive values pass through unchanged.

The h2 unit produced a small negative value for all three nodes. After ReLU it
becomes 0 — that hidden unit is "switched off" for this input. This is
intentional: ReLU introduces non-linearity, which lets the network represent
more complex patterns than any purely linear transformation could.

```
H = ReLU( A_hat · X · W₁ )

H[0] pe1:  [ 0.08671,  0.16405,  0.00000,  0.13736]
H[1] p1:   [ 0.10987,  0.20792,  0.00000,  0.17409]
H[2] ce1:  [ 0.08671,  0.16405,  0.00000,  0.13736]
```

---

#### What does this hidden matrix H mean?

H is the **latent embedding** — a compressed fingerprint of each node's state
after accounting for its graph neighbourhood. It is the "vocabulary" the decoder
uses to reconstruct the original features.

Key observations about H in the healthy case:
- pe1 and ce1 have **identical** embeddings — they have the same features and
  equivalent neighbourhood structure (both connect only to p1 with degree 2).
  The GCN has correctly learned they are in the same "state class".
- p1's embedding is slightly larger in all dimensions — it has more neighbours
  and slightly different aggregated values, so it occupies a different region
  of the latent space.
- h2 is 0 for all nodes — that hidden dimension found nothing useful to encode
  in this particular healthy snapshot. In a more complex network with 32 hidden
  units (as in the actual script), most units fire and capture subtle patterns.

---

#### Now contrast with the fault case

When pe1's tx_drops = 0.75 (fault), its aggregated row becomes `[0.37908, 0.14614]`
(computed in Step 9). Multiplying through W₁:

```
pe1 fault pre-ReLU:
h0: 0.37908×1.20 + 0.14614×0.50 = 0.45490 + 0.07307 =  0.52797
h1: 0.37908×(-0.30) + 0.14614×1.10 = -0.11372 + 0.16075 = 0.04703
h2: 0.37908×0.80 + 0.14614×(-0.20) = 0.30326 - 0.02923 =  0.27403
h3: 0.37908×0.10 + 0.14614×0.90   = 0.03791 + 0.13153 =  0.16944

Post-ReLU: [0.52797,  0.04703,  0.27403,  0.16944]
```

Compare pe1's healthy vs fault embedding:

```
           h0       h1       h2       h3
Healthy: [0.087,  0.164,   0.000,  0.137]
Fault:   [0.528,  0.047,   0.274,  0.169]
```

The fault embedding is in a completely different region of latent space — it
has never appeared in training. When the decoder tries to map this back to
feature space, it produces values near the healthy baseline (what it was
trained to output), not the actual fault values. That mismatch is exactly
the reconstruction error we use as the anomaly score.

---

## Step 7 — GCN Layer 2: Reconstruction (Decoder)

The second layer maps from the hidden space back to the original feature space.
Same operation, different weight matrix W₂ (shape [4 → 2]):

```
X_hat = A_hat · H · W₂
```

After training on healthy data, the decoder learns to produce feature values
close to the healthy baseline. For a healthy pe1 it reconstructs:

```
pe1 reconstructed:  tx_drops ≈ 0.010,  mtu_norm ≈ 0.167
p1  reconstructed:  tx_drops ≈ 0.010,  mtu_norm ≈ 0.167
ce1 reconstructed:  tx_drops ≈ 0.010,  mtu_norm ≈ 0.167
```

---

## Step 8 — Loss Function: MSE

During training, the loss is Mean Squared Error between the reconstruction X_hat
and the actual input X, averaged over all nodes and features:

```
Loss = (1 / N×F) × Σ_nodes Σ_features (X_hat[i,f] - X[i,f])²
```

For the healthy snapshot (N=3 nodes, F=2 features, loss calculated per batch):

```
pe1: (0.010-0.010)² + (0.167-0.167)² = 0.000000 + 0.000000 = 0.000000
p1:  (0.010-0.010)² + (0.167-0.167)² = 0.000000 + 0.000000 = 0.000000
ce1: (0.010-0.010)² + (0.167-0.167)² = 0.000000 + 0.000000 = 0.000000

Loss = (0 + 0 + 0) / (3×2) ≈ 0.000000
```

(In practice, loss is small but non-zero due to imperfect learning.)

Gradient descent then nudges W₁ and W₂ to reduce this loss over 100 epochs.

---

## Step 9 — Anomaly Scoring at Inference Time

Now inject **Fault 1** (pe1 MTU mismatch). The model is frozen — W₁ and W₂
do not change. Only the input X changes:

```
X_fault:
  pe1   →  [ 0.75    0.156 ]    ← tx_drops spiked, MTU wrong
  p1    →  [ 0.01    0.167 ]
  ce1   →  [ 0.01    0.167 ]
```

### 9a: Neighbourhood aggregation with fault data

Row 0 (pe1):
```
  tx_drops: 0.500×0.75 + 0.408×0.01 + 0×0.01 = 0.375 + 0.00408 = 0.37908
  mtu_norm: 0.500×0.156+ 0.408×0.167+ 0×0.167 = 0.078 + 0.06814 = 0.14614
```

Row 1 (p1) — p1 sees pe1's fault bleed through its neighbourhood:
```
  tx_drops: 0.408×0.75 + 0.333×0.01 + 0.408×0.01 = 0.306 + 0.00333 + 0.00408 = 0.31341
  mtu_norm: 0.408×0.156+ 0.333×0.167+ 0.408×0.167 = 0.06365+0.05561+0.06814 = 0.18740
```

Row 2 (ce1) — no direct link to pe1, so barely affected:
```
  tx_drops: 0×0.75 + 0.408×0.01 + 0.500×0.01 = 0 + 0.00408 + 0.005 = 0.00908
  mtu_norm: 0×0.156+ 0.408×0.167+ 0.500×0.167 = 0 + 0.06814 + 0.0835 = 0.15161
```

The fault embedding for pe1 (row 0) is completely different from what it was
during training. The model's decoder W₂ tries to reconstruct from these
unfamiliar latent features — and fails.

### 9b: Per-node reconstruction error

```
                  Actual     Reconstructed  Squared Error
pe1  tx_drops:   0.7500       0.0100        (0.74)²  = 0.5476
pe1  mtu_norm:   0.1560       0.1670        (0.011)² = 0.0001
     ─────────────────────────────────────────────────────────
pe1  MSE  =  (0.5476 + 0.0001) / 2  =  0.2739  ← HIGH

p1   tx_drops:   0.0100       0.0180        (0.008)² = 0.0001
p1   mtu_norm:   0.1670       0.1790        (0.012)² = 0.0001
     ─────────────────────────────────────────────────────────
p1   MSE  =  (0.0001 + 0.0001) / 2  =  0.0001  ← small (neighbour bleed)

ce1  tx_drops:   0.0100       0.0100        (0.000)² = 0.0000
ce1  mtu_norm:   0.1670       0.1670        (0.000)² = 0.0000
     ─────────────────────────────────────────────────────────
ce1  MSE  =  0.0000                                  ← normal
```

### 9c: Ranking

```
Rank  Node   Score    Status
 1    pe1    0.2739   ⚠️  ANOMALOUS  ← correctly identified root cause
 2    p1     0.0001   ✓ normal       ← minor neighbour contamination
 3    ce1    0.0000   ✓ normal
```

**Predicted root cause: pe1** ✓

---

## Summary of Every Formula Used

| Step | Formula | What it does |
|------|---------|--------------|
| 1 | `A[i,j] = 1 if edge(i,j) else 0` | Encode topology as matrix |
| 2 | `A_tilde = A + I` | Add self-loops so nodes keep their own features |
| 3 | `D[i,i] = Σⱼ A_tilde[i,j]` | Count connections per node |
| 4 | `A_hat = D^(-½) · A_tilde · D^(-½)` | Normalise so node degree doesn't distort magnitudes |
| 5 | `agg = A_hat · X` | Each node averages its neighbours' features (message passing) |
| 6 | `H = ReLU(agg · W₁)` | Encoder: non-linear compression to hidden space |
| 7 | `X_hat = A_hat · H · W₂` | Decoder: reconstruct original features |
| 8 | `Loss = MSE(X_hat, X) = mean((X_hat - X)²)` | Training signal: how wrong is the reconstruction? |
| 9 | `score[i] = mean((X_hat[i] - X[i])²)` | Per-node anomaly score at inference |

---

## Why the GCN Outperforms a Simple Threshold

A naive approach would alert when `pe1.tx_drops > 0.5`.
The GCN does something smarter:

1. **Graph context**: pe1's reconstruction is not just its own features —
   it is A_hat-weighted against p1 and ce1. The model learns the *expected
   relationship* between pe1 and its neighbours. If pe1's TX drops spike but
   p1 and ce1 are normal, that directional anomaly is amplified.

2. **Feature correlation**: the encoder compresses 7 features into 32 hidden
   dimensions, learning cross-feature patterns (e.g., "high tx_drops usually
   correlates with elevated CPU"). A single feature threshold misses these
   correlations entirely.

3. **No threshold to tune**: anomaly score is relative — always compared to
   what the model learned as normal from months of telemetry. The threshold
   adapts automatically to each router's typical behaviour.

---

## The Key Intuition in One Sentence

> The GCN autoencoder learns what every router's features should look like
> *given its neighbours' state*. When a router's actual features don't match
> what the model expects based on the graph, it scores high — and that mismatch
> directly points to the fault.
