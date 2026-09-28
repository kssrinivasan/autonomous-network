# GCN Maths Walkthrough — PyTorch / torch_geometric

Every arithmetic step shown with concrete numbers from the 3-node topology.

Code reference: `simple_gcn_pinpointing.py`  →  `GCNAutoencoder`

---

## Setup

```
  pe1 (0) ── p1 (1) ── ce1-spoke (2)
```

**6 features per node** (N=3, F=6):
```
         tx_drops  rx_drops  mtu_norm  cpu    mem    ospf
pe1      0.01      0.01      0.167     0.22   0.30   1.0
p1       0.01      0.01      0.167     0.20   0.30   1.0
ce1      0.01      0.01      0.167     0.18   0.25   1.0
```

**Hidden dim H=2** (code uses H=32; 2 keeps every matrix on-screen).

---

## Part A — GCNConv Layer 1

**What this step does:** Each node gathers a weighted average of its neighbours' features and
mixes that with its own features. After one round of this, each node's embedding reflects
not just itself but the context of its immediate neighbourhood. A second round lets information
travel two hops — so pe1 can "feel" what is happening at ce1-spoke even though they are not
directly connected.

The operation is a matrix multiplication, so all nodes are updated simultaneously in one
batched operation — there is no sequential loop over nodes.

`GCNConv` in PyG implements:

```
H^(l+1) = ReLU( D̂^{-½} Â D̂^{-½} H^(l) W )
```

where Â = A + I (adjacency + self-loops) and D̂ is the degree matrix of Â.

**Note:** `GCNConv(add_self_loops=True)` is the default — Â and D̂ are computed
internally, so you never build them by hand in PyTorch.

### A.1 — Adjacency matrix with self-loops: Â = A + I

**Why self-loops?** Without a self-loop, a node only aggregates its *neighbours* and loses
track of its own current state. Adding `I` (identity) puts the node itself in its own
neighbourhood, so the aggregation blends its own features with those of its neighbours
rather than replacing them. This is equivalent to saying "my new state is a mix of what I
was and what my neighbours told me."

Original edges: pe1↔p1 (0↔1) and p1↔ce1 (1↔2), bidirectional.

```
       pe1  p1  ce1
pe1  [  1    1    0  ]   ← self-loop (pe1→pe1) added
p1   [  1    1    1  ]   ← self-loop (p1→p1) added
ce1  [  0    1    1  ]   ← self-loop (ce1→ce1) added
```

### A.2 — Degree matrix and normalisation

**Why normalise?** Without normalisation, a high-degree node like p1 (3 neighbours) would
receive a much larger summed signal than pe1 (2 neighbours), making the scale of embeddings
depend on topology rather than content. The symmetric normalisation `D̂^{-½} Â D̂^{-½}` scales
each message by `1/(√d_src × √d_dst)`, so high-degree nodes neither dominate nor are drowned
out. This keeps all embeddings on a comparable scale regardless of how many links a router has.

Row degrees: d_pe1=2, d_p1=3, d_ce1=2

```
D̂^{-½} = diag(1/√2, 1/√3, 1/√2)
        = diag(0.707, 0.577, 0.707)
```

Symmetric normalisation  Â_norm[i,j] = Â[i,j] / (√d_i × √d_j):

```
         pe1       p1       ce1
pe1   [  0.500    0.408    0.000  ]
p1    [  0.408    0.333    0.408  ]
ce1   [  0.000    0.408    0.500  ]
```

Derivation of the three unique values:
```
pe1→pe1:  1 / (√2 × √2)  =  1/2   = 0.500
pe1→p1:   1 / (√2 × √3)  =  1/√6  = 0.408
p1→p1:    1 / (√3 × √3)  =  1/3   = 0.333
```

### A.3 — Neighbourhood aggregation: Â_norm @ X

**Intuition:** This is the "gossip" step. Each node broadcasts its current features to its
neighbours. Every node then forms a new representation by taking a weighted average of what
it received (including from itself). After this single matrix multiply, pe1's new features
are a blend of pe1 and p1; p1's are a blend of all three; ce1's are a blend of p1 and itself.
This is how topology enters the model — two nodes that are connected influence each other's
embeddings; disconnected nodes do not.

**For pe1 (row 0):**
```
agg[pe1] = 0.500 × X[pe1] + 0.408 × X[p1] + 0.000 × X[ce1]

cpu:   0.500×0.22 + 0.408×0.20 = 0.110 + 0.082 = 0.192
mem:   0.500×0.30 + 0.408×0.30 = 0.150 + 0.122 = 0.272
ospf:  0.500×1.00 + 0.408×1.00 = 0.500 + 0.408 = 0.908
mtu:   0.500×0.167 + 0.408×0.167 = 0.084 + 0.068 = 0.152
drops: 0.500×0.01 + 0.408×0.01 = 0.005 + 0.004 = 0.009  (both drop features)
```

**For p1 (row 1):**
```
agg[p1] = 0.408 × X[pe1] + 0.333 × X[p1] + 0.408 × X[ce1]

cpu:   0.408×0.22 + 0.333×0.20 + 0.408×0.18 = 0.090 + 0.067 + 0.073 = 0.230
mem:   0.408×0.30 + 0.333×0.30 + 0.408×0.25 = 0.122 + 0.100 + 0.102 = 0.324
ospf:  0.408×1.00 + 0.333×1.00 + 0.408×1.00 = 0.408 + 0.333 + 0.408 = 1.149
```

**For ce1-spoke (row 2):**
```
agg[ce1] = 0.000 × X[pe1] + 0.408 × X[p1] + 0.500 × X[ce1]

cpu:   0.408×0.20 + 0.500×0.18 = 0.082 + 0.090 = 0.172
mem:   0.408×0.30 + 0.500×0.25 = 0.122 + 0.125 = 0.247
```

### A.4 — Weight projection: agg @ W1

**What the weights learn:** `W1` is the only learned parameter in this layer. It is a
`[F, H]` matrix that projects the 6-dimensional aggregated feature vector into a
2-dimensional (H=2) latent representation. During training, gradient descent adjusts W1 so
that the latent representation can be decoded back to the original features with minimum error.
In practice W1 learns which combinations of features are most informative for distinguishing
healthy from unhealthy states — for example, it learns to weight `tx_drops` heavily because
that feature varies most between normal and fault conditions.

Assume a small W1 [6, 2] (code has W [6, 32]):

```
W1 = [[ 0.3,   0.1 ],   ← tx_drops
      [ 0.2,   0.1 ],   ← rx_drops
      [ 0.5,   0.3 ],   ← mtu_norm
      [ 0.4,   0.6 ],   ← cpu_percent
      [ 0.2,   0.5 ],   ← mem_percent
      [ 0.1,   0.2 ]]   ← ospf_state
```

**H^(1) for pe1 (using agg[pe1] ≈ [0.009, 0.009, 0.152, 0.192, 0.272, 0.908]):**
```
h0: 0.009×0.3 + 0.009×0.2 + 0.152×0.5 + 0.192×0.4 + 0.272×0.2 + 0.908×0.1
  = 0.003 + 0.002 + 0.076 + 0.077 + 0.054 + 0.091
  = 0.303

h1: 0.009×0.1 + 0.009×0.1 + 0.152×0.3 + 0.192×0.6 + 0.272×0.5 + 0.908×0.2
  = 0.001 + 0.001 + 0.046 + 0.115 + 0.136 + 0.182
  = 0.481

H^(1)[pe1] = ReLU([0.303, 0.481]) = [0.303, 0.481]
```

**Healthy node — all three nodes have similar feature vectors, so similar embeddings:**
```
H^(1)[pe1]  ≈ [0.303, 0.481]
H^(1)[p1]   ≈ [0.280, 0.476]   (slightly different due to 3 neighbours vs 2)
H^(1)[ce1]  ≈ [0.268, 0.453]
```

Notice that all three healthy embeddings cluster tightly in the latent space — dim 0 spans
0.268–0.303 and dim 1 spans 0.453–0.481. The decoder is trained on this tight cluster.
When a fault arrives the embedding will move outside this cluster, and the decoder — which
only knows the cluster — will produce a reconstruction that does not match the actual features.

---

## Part B — GCNConv Layer 2

**What the second layer adds:** Layer 1 gave each node a view of its 1-hop neighbourhood.
Layer 2 runs the same aggregation over the *layer-1 embeddings* — effectively giving each
node a 2-hop view. After layer 2, pe1's embedding encodes not just pe1 and p1 (1-hop)
but also ce1-spoke (2-hop, reached via p1). In a real 12-router topology, 2 layers gives
every node a view 2 links deep — enough to capture most relevant context without over-smoothing
(where too many layers cause all embeddings to converge to the same value).

Layer 2 applies the same normalised aggregation but over the H=2 embeddings from layer 1.
Input H^(1) is already [N, H] so the weight matrix is W2 [2, 2].

```
H^(2) = ReLU( Â_norm @ H^(1) @ W2 )
```

This produces the **final node embeddings** returned by `encode()` — the `out_embeddings`
in the code.

---

## Part C — Decoder and Anomaly Score

### C.1 — Decoder

**Why reconstruct instead of classify?** A classifier needs labelled fault examples — "this
snapshot is faulty" — which are rare and expensive to collect. A *reconstruction* autoencoder
needs only healthy data (which is abundant). The decoder learns to invert the encoder:
given a compressed embedding, output the original features. When a healthy node is encoded,
the embedding lands inside the region the decoder knows, and reconstruction is accurate.
When a faulty node is encoded, its embedding lands *outside* that region, and the decoder
— which only knows healthy territory — produces a prediction that misses the actual faulty
features. The reconstruction error is the anomaly signal.

The `nn.Linear(H, F)` decoder reconstructs the original F=6 feature vector from the
H=2 embedding:

```
recon[i] = H^(2)[i] @ W_dec + b_dec
```

During training on healthy data, the decoder learns to map the compressed embedding
back to the healthy features. At inference on a fault, the embedding is out of distribution
and the decoder produces a wrong reconstruction.

### C.2 — Fault 1: MTU mismatch on pe1

Fault features for pe1:
```
X_fault[pe1] = [0.75, 0.01, 0.156, 0.22, 0.30, 1.0]
                 ↑                   ↑
             tx_drops spike       mtu_norm deviates
```

The aggregation step for pe1 now sees its own anomalous tx_drops and mtu_norm:
```
agg_fault[pe1] cpu:   same as healthy (cpu unchanged)
agg_fault[pe1] mtu:   0.500×0.156 + 0.408×0.167 = 0.078 + 0.068 = 0.146  (was 0.152)
agg_fault[pe1] drops: 0.500×0.75  + 0.408×0.01  = 0.375 + 0.004 = 0.379  (was 0.009 !)
```

The tx_drops feature dominates — the aggregation value jumps 42× (0.009 → 0.379).
This pushes pe1's embedding far from its trained healthy position.

### C.3 — Per-node anomaly scores

```
score[i] = mean( (recon[i] - X_fault[i])² )   over all F features
```

```
Node        Actual       Expected    MSE
pe1         [0.75,...]   [0.01,...]  HIGH ⚠️    ← tx_drops prediction misses by ~0.74²
p1          [0.01,...]   [0.01,...]  ~0.000     ← unchanged, reconstructs correctly
ce1         [0.01,...]   [0.01,...]  ~0.000     ← unchanged
```

**Why p1 also shifts slightly:**
p1's aggregation mixes in pe1's faulty embedding (weighted 0.408 × H^(2)[pe1]).
A small secondary signal appears at p1 — the graph is propagating the fault signal one hop.
pe1's primary score remains much higher, correctly identifying it as the root cause.

This one-hop propagation is actually useful in larger topologies: a router that is *adjacent*
to a failing link will have a mildly elevated anomaly score, which gives the operator a
"fault neighbourhood" view rather than a single binary alert. The node with the highest score
is the primary suspect; elevated neighbours confirm its connectivity context.

---

## Part D — Training objective

**Self-supervised learning — why it works:** The model is trained to compress and then
reconstruct its own input. The only signal is "how wrong was my reconstruction?". Because
training uses only healthy snapshots, the model becomes very good at representing healthy
states in latent space and very bad at representing faults (which it has never seen). At
inference time this asymmetry is the alarm: low MSE = the model recognised this state;
high MSE = the model was surprised by something it has never encountered during training.

**Why reconstruction beats threshold-based monitoring:** A hard threshold on `tx_drops` (e.g.,
"alert if > 1000") ignores context — maybe this router always has slightly elevated drops after
a route refresh. The autoencoder learns the *normal baseline for each specific node in its
topology context*, not a global threshold. If pe1 normally runs at 0.01 drops/sec, the model
is tuned to that; if p1 normally runs at 0.03, the model knows that too.

```python
# In train():
for x in snapshots:                   # healthy snapshots only
    recon, _ = model(x, edge_index)
    loss = MSELoss(recon, x)          # self-supervised reconstruction
    loss.backward()
```

No labels. The model learns "what healthy looks like" purely from reconstruction.
At inference, high MSE = the model was surprised = anomaly.

---

## Summary of formulas

| Step | Formula | PyTorch code |
|------|---------|-------------|
| Self-loop | Â = A + I | `GCNConv(add_self_loops=True)` (default) |
| Normalise | Â_norm[i,j] = Â[i,j] / (√d_i √d_j) | inside `GCNConv.forward()` |
| Aggregate | AGG = Â_norm @ H | inside `GCNConv.forward()` |
| Project | H^{new} = ReLU(AGG @ W) | `conv(h, edge_index).relu()` |
| Decode | recon = H @ W_dec + b | `self.decoder(h)` |
| Score | score[i] = mean((recon[i] - x[i])²) | `((recon - x)**2).mean(dim=1)` |
