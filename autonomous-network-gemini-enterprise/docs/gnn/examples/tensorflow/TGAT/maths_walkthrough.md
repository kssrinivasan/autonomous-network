# TGAT Maths Walkthrough — Worked Example

A step-by-step walk through the mathematical operations that power the simple TGAT (Temporal Graph Attention Network) example, using **concrete numbers** from the hub-and-spoke topology.

To keep the matrices readable, we work with a **3-node slice** of the graph over a **3-timestep sequence**:

```
  pe2  ──  p1  ──  ce1-hub
```

Nodes:
- Node 0 = `pe2`       (Provider Edge — the router with the memory leak)
- Node 1 = `p1`        (Core P router)
- Node 2 = `ce1-hub`   (Customer Edge)

Features:
- We keep only **2 features** per node:
  - `mem_percent`   (Memory utilization, 0.0 to 1.0)
  - `tx_drops_rate` (TX drops, log-scaled, 0.0 to 1.0)

Time steps ($T=3$):
- $t=1$, $t=2$, $t=3$

---

## The Simulated Anomaly (Data)

**Healthy baseline [mem, drops]:**
- `pe2` = [0.30, 0.01]
- `p1`  = [0.30, 0.01]
- `ce1` = [0.20, 0.01]

**Memory Leak Sequence on `pe2`:**
Over $t=1 \dots 3$, `pe2`'s memory steadily climbs, while others stay flat. `tx_drops_rate` stays flat for all.

$X_1 = \begin{bmatrix} 0.30 & 0.01 \\ 0.30 & 0.01 \\ 0.20 & 0.01 \end{bmatrix}$ (Healthy)

$X_2 = \begin{bmatrix} 0.45 & 0.01 \\ 0.30 & 0.01 \\ 0.20 & 0.01 \end{bmatrix}$ (Leak starting)

$X_3 = \begin{bmatrix} 0.60 & 0.01 \\ 0.30 & 0.01 \\ 0.20 & 0.01 \end{bmatrix}$ (Leak progressing)

---

## 1. Spatial Aggregation: Graph Attention (GAT) Layer

At each time step $t$, the GAT layer computes a new spatial embedding $\vec{h}_i^{(t)}$ for each node by dynamically weighing its neighbors. We use $t=3$ as our example.

### Adjacency (A_tilde)
We use the raw adjacency plus self-loops ($\mathbf{A} + \mathbf{I}$). No degree normalization is needed because the GAT uses a softmax over neighbors.
$$ \mathbf{A}_{tilde} = \begin{bmatrix} 1 & 1 & 0 \\ 1 & 1 & 1 \\ 0 & 1 & 1 \end{bmatrix} $$

### A. Linear Projection
We project the 2D feature into a hidden space. Let's say our hidden dimension $H=2$, and our trained weight matrix $\mathbf{W} \in \mathbb{R}^{2 \times 2}$ is:
$$ \mathbf{W} = \begin{bmatrix} 0.8 & -0.5 \\ 0.1 & 1.2 \end{bmatrix} $$

For $t=3$:
$$ \mathbf{Z}_3 = \mathbf{X}_3 \mathbf{W} = \begin{bmatrix} 0.60 & 0.01 \\ 0.30 & 0.01 \\ 0.20 & 0.01 \end{bmatrix} \begin{bmatrix} 0.8 & -0.5 \\ 0.1 & 1.2 \end{bmatrix} = \begin{bmatrix} 0.481 & -0.288 \\ 0.241 & -0.138 \\ 0.161 & -0.088 \end{bmatrix} $$
Row 0 ($\vec{z}_{pe2}$) = $[0.481, -0.288]$

### B. Attention Coefficients
We compute the attention score $e_{ij}$ indicating how important node $j$'s features are to node $i$.
$$ e_{ij} = \text{LeakyReLU} \left( \vec{\mathbf{a}}^T [ \vec{z}_i \ || \ \vec{z}_j ] \right) $$

Assume our trained attention vector $\vec{\mathbf{a}} = \begin{bmatrix} 1.0 & 0.5 & -0.2 & 0.8 \end{bmatrix}^T$.

For $i=pe2$ (Node 0) looking at $j=p1$ (Node 1):
1. Concatenate: $[ \vec{z}_0 \ || \ \vec{z}_1 ] = [0.481, -0.288, 0.241, -0.138]$
2. Dot product: $0.481(1) - 0.288(0.5) + 0.241(-0.2) - 0.138(0.8) = 0.481 - 0.144 - 0.0482 - 0.1104 = 0.1784$
3. LeakyReLU: $\max(0.2 \times 0.1784, 0.1784) = 0.1784$
So, $\mathbf{e}_{0,1} = 0.1784$.

For $i=pe2$ (Node 0) looking at itself $j=pe2$ (Node 0):
1. Concatenate: $[ \vec{z}_0 \ || \ \vec{z}_0 ] = [0.481, -0.288, 0.481, -0.288]$
2. Dot product: $0.481(1) - 0.288(0.5) + 0.481(-0.2) + -0.288(0.8) = 0.481 - 0.144 - 0.0962 - 0.2304 = 0.0104$
$\mathbf{e}_{0,0} = \text{LeakyReLU}(\dots) = 0.0104$

### C. Masking and Normalization (Softmax)
Since $pe2$ is only connected to itself and $p1$, we mask out $ce1$ ($e_{0,2} = -\infty$).
We apply softmax over the valid neighbors:
$$ \alpha_{0,0} = \frac{\exp(0.0104)}{\exp(0.0104) + \exp(0.1784)} = 0.458 $$
$$ \alpha_{0,1} = \frac{\exp(0.1784)}{\exp(0.0104) + \exp(0.1784)} = 0.542 $$

Notice how $pe2$ dynamically placed more attention (54.2%) on $p1$'s normal state than on its own high-memory state (45.8%).

### D. Message Passing
Calculate the new spatial embedding $\vec{h}_0^{(3)}$ for $pe2$:
$$ \vec{h}_{pe2}^{(3)} = \text{ELU} \left( \alpha_{0,0} \vec{z}_0 + \alpha_{0,1} \vec{z}_1 \right) $$
$$ = \text{ELU} \left( 0.458 [0.481, -0.288] + 0.542 [0.241, -0.138] \right) $$
$$ = \text{ELU} \left( [0.220 + 0.131, -0.132 - 0.075] \right) $$
$$ = \text{ELU} ( [0.351, -0.207] ) \approx [0.351, -0.187] $$

This gives us the spatial representation $\vec{h}_i^{(t)}$ for all nodes at all time steps.

---

## 2. Temporal Aggregation: Gated Recurrent Unit (GRU)

After the spatial aggregation, $pe2$ has a sequence of three embeddings:
$[ \vec{h}_{pe2}^{(1)}, \vec{h}_{pe2}^{(2)}, \vec{h}_{pe2}^{(3)} ]$. 

Because its memory is steadily climbing, the vectors $\vec{h}^{(t)}$ are also shifting dramatically over the sequence.

We pass this sequence through a GRU. The GRU maintains a hidden state $\vec{m}_t$ for each time step.

### GRU Update (Conceptual)
For $t=1$ to $3$, the GRU updates its internal hidden state $\vec{m}_t$:
$$ \vec{z}_t = \sigma(\mathbf{W}_z \vec{h}_{pe2}^{(t)} + \mathbf{U}_z \vec{m}_{t-1}) $$ Update Gate
$$ \vec{r}_t = \sigma(\mathbf{W}_r \vec{h}_{pe2}^{(t)} + \mathbf{U}_r \vec{m}_{t-1}) $$ Reset Gate
$$ \tilde{\vec{m}}_t = \tanh( \mathbf{W}_h \vec{h}_{pe2}^{(t)} + \mathbf{U}_h (\vec{r}_t \odot \vec{m}_{t-1})) $$ Candidate
$$ \vec{m}_t = (1 - \vec{z}_t) \odot \vec{m}_{t-1} + \vec{z}_t \odot \tilde{\vec{m}}_t $$ Final State

**During normal operational data (training):** The GRU learns that sequences are usually flat (e.g., memory stays around 0.30). The hidden state $\vec{m}_3$ reaches an attractor state corresponding to "normal operations".

**During the Memory Leak (anomaly):** The constantly shifting inputs $\vec{h}^{(1)}, \vec{h}^{(2)}, \vec{h}^{(3)}$ force the GRU into an unfamiliar internal state. The final temporal embedding $\mathbf{M}^{(3)}$ for $pe2$ is highly saturated.

---

## 3. Decoder and Reconstruction Loss

The final temporal embedding $\mathbf{M}^{(3)}$ contains both the spatial neighborhood context (from the GAT) and the temporal trajectory context (from the GRU).

The decoder is a standard Dense layer that attempts to reconstruct the original snapshot at time $t=3$ ($\mathbf{X}_3$):
$$ \hat{\mathbf{X}}_3 = \mathbf{W}_{dec} \mathbf{M}^{(3)} + \vec{b}_{dec} $$

The loss function is the Mean Squared Error (MSE) between the predicted final state $\hat{\mathbf{X}}_3$ and the actual final state $\mathbf{X}_3$:
$$ \mathcal{L} = \frac{1}{N \cdot F} \sum_{i=0}^{N-1} \sum_{k=0}^{F-1} \left( X_{3, i, k} - \hat{X}_{3, i, k} \right)^2 $$

### Anomaly Pinpointing
At inference time, the decoder $\mathbf{W}_{dec}$ tries to output a prediction for $pe2$.
Because the temporal trajectory pushed the GRU into an unfamiliar state, the decoder outputs a "safe" prediction, expecting the memory to have flattened out normally, and the drops to be healthy: 
$$ \hat{\mathbf{X}}_{3, pe2} \approx [0.32, 0.010] $$
But the actual value is:
$$ \mathbf{X}_{3, pe2} = [0.60, 0.010] $$

The Squared Error for $pe2$ averaged across both features is: 
$$ \text{MSE}_{pe2} = \frac{(0.60 - 0.32)^2 + (0.010 - 0.010)^2}{2} = \frac{0.0784 + 0}{2} = 0.0392 $$

Meanwhile, $p1$ and $ce1$ had flat histories and their actual final states match the prediction closely (Error $\approx 0.0001$).

The anomaly score for $pe2$ is dramatically higher than the rest of the network, accurately pinpointing the memory leak right down to the specific node.
