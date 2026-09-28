# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Simple GNN Failure Pinpointing Example
=======================================
A minimal, self-contained Graph Convolutional Network (GCN) autoencoder
built with TensorFlow/Keras that demonstrates basic failure pinpointing on
the Spanner lab topology (l3vpn-hub-spoke.yaml).

No Spanner connection needed — topology and features are hardcoded/synthetic
so this runs immediately with:
    pip install tensorflow numpy
    python simple_failure_pinpointing.py

Concepts from failure_pinpointing_research.md demonstrated here:
  - Graph represented as nodes (routers) + adjacency matrix (physical links)
  - Node features: CPU, memory, drops, BGP/OSPF state, MTU
  - GCN autoencoder trained on "normal" snapshots (self-supervised)
  - Reconstruction error = anomaly score: high error → suspicious node
  - Fault 1 simulation: MTU mismatch on PE1 (silent drop scenario)

Architecture:
    X [N=12, F=7]
      → GCN Layer 1: A_hat @ X @ W1 → ReLU  [N, 32]   encoder
      → GCN Layer 2: A_hat @ H @ W2          [N, 7]    decoder (reconstruction)
    Loss = MSE(reconstructed X, original X)

Where A_hat = D^(-1/2) (A+I) D^(-1/2)  — standard GCN normalisation
(Kipf & Welling, 2017 — no library needed, just matrix multiplication)
"""

import numpy as np
import tensorflow as tf
from tensorflow import keras

# ---------------------------------------------------------------------------
# 1. TOPOLOGY — from l3vpn-hub-spoke.yaml
#    12 routers as nodes. Physical p2p links as undirected edges.
# ---------------------------------------------------------------------------

# Node index → router name
NODE_NAMES = [
    "p1",        # 0  — Core P router (London)
    "p2",        # 1  — Core P router (Manchester)
    "p3",        # 2  — Core P router (Edinburgh)
    "p4",        # 3  — Core P router (Leeds)
    "rr1",       # 4  — Route Reflector (Birmingham)
    "rr2",       # 5  — Route Reflector (Bristol)
    "pe1",       # 6  — Provider Edge Spoke (Oxford)  ← fault target
    "pe2",       # 7  — Provider Edge Hub (Cambridge)
    "pe3",       # 8  — Provider Edge Spoke (Brighton)
    "ce1-spoke", # 9  — Customer Edge Spoke 1 (Sheffield)
    "ce1-hub",   # 10 — Customer Edge Hub (Nottingham)
    "ce2-spoke", # 11 — Customer Edge Spoke 2 (Liverpool)
]

N = len(NODE_NAMES)

# Physical links from the YAML networks section.
# Each tuple is (router_a, router_b) by index — undirected.
EDGES = [
    (0, 1),  # p1  ↔ p2   (p1-p2)
    (0, 2),  # p1  ↔ p3   (p1-p3)
    (0, 4),  # p1  ↔ rr1  (p1-rr1)
    (0, 6),  # p1  ↔ pe1  (p1-pe1)  ← the MTU fault link
    (0, 7),  # p1  ↔ pe2  (p1-pe2)
    (1, 3),  # p2  ↔ p4   (p2-p4)
    (1, 4),  # p2  ↔ rr1  (p2-rr1)
    (2, 3),  # p3  ↔ p4   (p3-p4)
    (2, 5),  # p3  ↔ rr2  (p3-rr2)
    (2, 7),  # p3  ↔ pe2  (p3-pe2)
    (3, 5),  # p4  ↔ rr2  (p4-rr2)
    (3, 8),  # p4  ↔ pe3  (p4-pe3)
    (6, 9),  # pe1 ↔ ce1-spoke
    (7, 10), # pe2 ↔ ce1-hub
    (8, 11), # pe3 ↔ ce2-spoke
]


def build_adjacency(n, edges):
    """
    Build the GCN-normalised adjacency matrix A_hat.

    Steps:
      1. Create raw adjacency A (symmetric, with self-loops already absent)
      2. Add self-loops: A_tilde = A + I
      3. Compute degree matrix D_tilde
      4. Normalise: A_hat = D^(-1/2) A_tilde D^(-1/2)

    This normalisation ensures that the message-passing aggregation
    is scale-invariant regardless of each node's degree (number of neighbours).
    """
    A = np.zeros((n, n), dtype=np.float32)
    for (i, j) in edges:
        A[i, j] = 1.0
        A[j, i] = 1.0           # undirected → symmetric

    A_tilde = A + np.eye(n, dtype=np.float32)   # self-loops

    # Degree of each node in A_tilde
    D = np.diag(A_tilde.sum(axis=1))
    # D^(-1/2): replace each diagonal with its inverse square root
    D_inv_sqrt = np.diag(1.0 / np.sqrt(np.diag(D)))

    A_hat = D_inv_sqrt @ A_tilde @ D_inv_sqrt
    return A_hat.astype(np.float32)


A_HAT = build_adjacency(N, EDGES)


# ---------------------------------------------------------------------------
# 2. NODE FEATURES — 7 features per router (all normalised to [0, 1])
#
#    Feature vector layout (fixed order — like a feature registry):
#      [0] cpu_percent       — from `show system cpu`         (0.0–1.0)
#      [1] mem_percent       — from `show system memory`      (0.0–1.0)
#      [2] tx_drops_rate     — TX drops/sec, log-scaled       (0.0–1.0)
#      [3] rx_drops_rate     — RX drops/sec, log-scaled       (0.0–1.0)
#      [4] bgp_state         — 1 = Established, 0 = Down
#      [5] ospf_state        — 1 = Full, 0 = Down/Init
#      [6] mtu_norm          — interface MTU / 9000
# ---------------------------------------------------------------------------

F = 7  # number of features

FEATURE_NAMES = [
    "cpu_percent",
    "mem_percent",
    "tx_drops_rate",
    "rx_drops_rate",
    "bgp_state",
    "ospf_state",
    "mtu_norm",
]

# "Healthy baseline" feature vector for each node.
# In production these would come from Spanner NetworkMetrics rows.
# Conventions:
#   - P routers:   OSPF=1, BGP=0 (they run OSPF/MPLS, not BGP VPNv4 directly)
#   - RRs:         OSPF=1, BGP=1 (they are the route reflectors)
#   - PEs:         OSPF=1, BGP=1 (iBGP to RRs + eBGP VRF to CEs)
#   - CEs:         OSPF=0, BGP=1 (only eBGP to PE)
#
# Index:            cpu   mem   txd   rxd   bgp  ospf  mtu
HEALTHY_BASELINE = np.array([
    [0.20, 0.30, 0.01, 0.01, 0.0, 1.0, 0.167],  # 0  p1
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 1  p2
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 2  p3
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 3  p4
    [0.25, 0.35, 0.01, 0.01, 1.0, 1.0, 0.167],  # 4  rr1
    [0.25, 0.35, 0.01, 0.01, 1.0, 1.0, 0.167],  # 5  rr2
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 6  pe1  ← 1500/9000 ≈ 0.167 = normal MTU
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 7  pe2
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 8  pe3
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 9  ce1-spoke
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 10 ce1-hub
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 11 ce2-spoke
], dtype=np.float32)


def generate_normal_snapshots(n_snapshots=500, noise_std=0.03, seed=42):
    """
    Synthetic training data: Gaussian noise around the healthy baseline.

    In production this would be real telemetry fetched from Spanner
    NetworkMetrics rows for the last 30+ days of clean operation.

    Returns: np.array of shape [n_snapshots, N, F]
    """
    rng = np.random.default_rng(seed)
    base = HEALTHY_BASELINE[np.newaxis, :, :]          # [1, N, F]
    noise = rng.normal(0, noise_std, (n_snapshots, N, F)).astype(np.float32)
    snapshots = np.clip(base + noise, 0.0, 1.0)        # keep in [0, 1]
    return snapshots


def make_fault1_snapshot():
    """
    Simulate Fault 1: MTU mismatch on PE1's uplink to P1 (pe1 eth1 → 1400, P1 eth3 → 1500).

    From the research doc:
      - PE1 eth1 MTU set to 1400 (vs P1 eth3 at 1500)
      - Large packets silently dropped on PE1→P1 direction
      - TX drops spike on PE1 eth1
      - OSPF/BGP control plane stays UP (small packets still pass)
      - MTU on PE1 changes: 1400/9000 ≈ 0.156 (was 0.167)

    P1 also sees slightly elevated RX drops because some frames it sends
    back to PE1 are also affected by the asymmetric MTU window.
    """
    snap = HEALTHY_BASELINE.copy()

    # PE1 (index 6): MTU drops to 1400, TX drops spike
    snap[6, 2] = 0.75   # tx_drops_rate — large spike (log-scaled)
    snap[6, 6] = 0.156  # mtu_norm: 1400 / 9000

    # P1 (index 0): minor RX drop elevation (neighbour effect)
    snap[0, 3] = 0.20   # rx_drops_rate — mild increase

    return snap


# ---------------------------------------------------------------------------
# 3. GCN AUTOENCODER — implemented as a custom Keras layer
#
#    GCN message-passing equation (Kipf & Welling 2017):
#        H^(l+1) = σ( A_hat @ H^(l) @ W^(l) )
#
#    A_hat is pre-computed and fixed — no trainable parameters in it.
#    Only W (the weight matrix) is learned.
# ---------------------------------------------------------------------------

class GCNLayer(keras.layers.Layer):
    """
    Single Graph Convolutional Network layer.

    Computes: output = activation( A_hat @ inputs @ W + b )

    A_hat is the pre-normalised adjacency matrix passed in as a constant.
    W and b are the trainable parameters.

    This is exactly the same operation as a Dense layer, but the input
    is first "neighbourhood-aggregated" by multiplying with A_hat.
    Think of it as: every node averages its own features with its
    neighbours' features (weighted by A_hat), then applies a linear
    transformation W.
    """

    def __init__(self, units, activation=None, **kwargs):
        super().__init__(**kwargs)
        self.units = units
        self.activation = keras.activations.get(activation)

    def build(self, input_shape):
        # input_shape is [batch, N, F] — but we process [N, F] per snapshot
        in_features = input_shape[-1]
        self.W = self.add_weight(
            name="W",
            shape=(in_features, self.units),
            initializer="glorot_uniform",
            trainable=True,
        )
        self.b = self.add_weight(
            name="b",
            shape=(self.units,),
            initializer="zeros",
            trainable=True,
        )
        super().build(input_shape)

    def call(self, inputs, A_hat):
        """
        Args:
            inputs: node feature matrix [batch, N, F] or [N, F]
            A_hat:  normalised adjacency [N, N] (constant, not batched)
        Returns:
            Updated node embeddings [batch, N, units]
        """
        # Neighbourhood aggregation: A_hat @ inputs  →  [batch, N, F]
        aggregated = tf.matmul(A_hat, inputs)

        # Linear transformation: aggregated @ W + b  →  [batch, N, units]
        out = tf.matmul(aggregated, self.W) + self.b

        if self.activation is not None:
            out = self.activation(out)
        return out


class GCNAutoencoder(keras.Model):
    """
    Two-layer GCN autoencoder for anomaly detection.

    Encoder:
        GCN(F → 32, ReLU)      — learns a compressed representation
    Decoder:
        GCN(32 → F, linear)    — reconstructs the original features

    Training objective: minimise MSE(reconstruction, input).

    At inference time, a node with high reconstruction error is
    "surprising" to the model — it looks unlike anything seen in
    normal training data, flagging it as potentially faulty.
    """

    def __init__(self, n_nodes, n_features, hidden_dim=32, **kwargs):
        super().__init__(**kwargs)
        self.n_nodes = n_nodes
        self.n_features = n_features

        # Encoder: compress F features → hidden_dim
        self.encoder = GCNLayer(hidden_dim, activation="relu", name="encoder")
        # Decoder: reconstruct hidden_dim → F features
        self.decoder = GCNLayer(n_features, activation=None, name="decoder")

    def call(self, X, A_hat):
        """
        Args:
            X:     node features [batch, N, F]
            A_hat: normalised adjacency [N, N]
        Returns:
            X_hat: reconstructed features [batch, N, F]
        """
        H = self.encoder(X, A_hat)       # [batch, N, hidden_dim]
        X_hat = self.decoder(H, A_hat)   # [batch, N, F]
        return X_hat


# ---------------------------------------------------------------------------
# 4. TRAINING
# ---------------------------------------------------------------------------

def train(model, X_train, A_hat, epochs=100, lr=1e-3, batch_size=32):
    """
    Train the GCN autoencoder on normal snapshots using MSE reconstruction loss.

    Following the research doc's training guidelines:
      - Self-supervised: no fault labels needed
      - Loss = MSE(X_hat, X) averaged over all nodes and features
      - Simple Adam optimiser
    """
    optimiser = keras.optimizers.Adam(learning_rate=lr)
    loss_fn = keras.losses.MeanSquaredError()

    n = X_train.shape[0]
    A_hat_tf = tf.constant(A_hat)   # fixed constant, not a variable

    print(f"\n{'='*55}")
    print(f"  Training GCN Autoencoder — {epochs} epochs")
    print(f"  Nodes: {model.n_nodes}  Features: {model.n_features}  Hidden: 32")
    print(f"  Training snapshots: {n}  Batch size: {batch_size}")
    print(f"{'='*55}")

    dataset = tf.data.Dataset.from_tensor_slices(X_train)
    dataset = dataset.shuffle(buffer_size=n, seed=42).batch(batch_size)

    for epoch in range(1, epochs + 1):
        epoch_losses = []
        for X_batch in dataset:
            with tf.GradientTape() as tape:
                X_hat = model(X_batch, A_hat_tf)
                loss = loss_fn(X_batch, X_hat)

            grads = tape.gradient(loss, model.trainable_variables)
            optimiser.apply_gradients(zip(grads, model.trainable_variables))
            epoch_losses.append(float(loss))

        if epoch % 20 == 0 or epoch == 1:
            avg = np.mean(epoch_losses)
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {avg:.6f}")

    print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# 5. ANOMALY SCORING & FAILURE PINPOINTING
# ---------------------------------------------------------------------------

def compute_anomaly_scores(model, snapshot, A_hat):
    """
    Run a single graph snapshot through the trained model and compute
    a per-node reconstruction error (anomaly score).

    Score = mean squared error over all F features for that node.
    A high score means the model was "surprised" by that node's state,
    indicating it looks different from anything seen during training.

    Returns: np.array of shape [N] — one score per node
    """
    A_hat_tf = tf.constant(A_hat)
    X = tf.constant(snapshot[np.newaxis, :, :])   # add batch dimension [1, N, F]

    X_hat = model(X, A_hat_tf)                    # [1, N, F]
    X_hat_np = X_hat.numpy()[0]                   # [N, F]

    # Per-node MSE: average squared error across all F features
    errors = np.mean((snapshot - X_hat_np) ** 2, axis=1)  # [N]
    return errors


def print_anomaly_report(errors, threshold_factor=3.0):
    """
    Print a ranked anomaly table.

    Threshold = mean + threshold_factor * std of all node scores.
    Nodes above the threshold are flagged as anomalous.

    In production this anomaly score would be written back to Spanner
    as a NodeEmbedding row (the anomaly_score field).
    """
    ranked_idx = np.argsort(errors)[::-1]  # highest error first

    mean_err = np.mean(errors)
    std_err = np.std(errors)
    threshold = mean_err + threshold_factor * std_err

    print(f"\n{'='*60}")
    print(f"  Anomaly Scores (per-node reconstruction error)")
    print(f"  Threshold: {threshold:.4f}  (mean + {threshold_factor}×std)")
    print(f"{'='*60}")
    print(f"  {'Rank':<5} {'Node':<12} {'Score':>8}  {'Status'}")
    print(f"  {'-'*48}")

    for rank, idx in enumerate(ranked_idx, start=1):
        name = NODE_NAMES[idx]
        score = errors[idx]
        status = "⚠️  ANOMALOUS" if score > threshold else "✓ normal"
        print(f"  {rank:<5} {name:<12} {score:>8.4f}  {status}")

    print(f"{'='*60}")

    # Root cause prediction: the single node with the highest anomaly score
    top_idx = ranked_idx[0]
    print(f"\n  🔍 Predicted root cause: {NODE_NAMES[top_idx]}")
    print(f"     Score {errors[top_idx]:.4f} — check interface MTU, TX drops,")
    print(f"     and asymmetric traffic counters on this node.")
    print()

    return NODE_NAMES[ranked_idx[0]]


# ---------------------------------------------------------------------------
# 6. FEATURE EXPLANATION — which features drove the anomaly?
#
#    A simple per-feature error breakdown (precursor to Integrated Gradients).
#    Shows which features deviated most on the flagged node.
# ---------------------------------------------------------------------------

def explain_node(model, snapshot, A_hat, node_idx):
    """
    For the flagged node, show per-feature reconstruction error.
    This mimics the output of Integrated Gradients / SHAP:
    the feature with the highest error directly drove the anomaly signal.
    """
    A_hat_tf = tf.constant(A_hat)
    X = tf.constant(snapshot[np.newaxis, :, :])
    X_hat = model(X, A_hat_tf).numpy()[0]

    node_name = NODE_NAMES[node_idx]
    actual = snapshot[node_idx]
    predicted = X_hat[node_idx]
    feature_errors = (actual - predicted) ** 2

    ranked = np.argsort(feature_errors)[::-1]

    print(f"  Feature breakdown for node '{node_name}':")
    print(f"  {'Feature':<18} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*50}")
    for i in ranked:
        print(f"  {FEATURE_NAMES[i]:<18} {actual[i]:>8.4f} {predicted[i]:>9.4f} {feature_errors[i]:>8.4f}")
    print()
    print(f"  Top driver: '{FEATURE_NAMES[ranked[0]]}' — this is the feature the")
    print(f"  model found most surprising. In Fault 1, this should be")
    print(f"  'tx_drops_rate' or 'mtu_norm' on pe1.\n")


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def main():
    print("\n" + "="*60)
    print("  GNN Failure Pinpointing — Simple TensorFlow Example")
    print("  Topology: L3VPN Hub-and-Spoke (Spanner lab)")
    print("  Fault:    MTU mismatch on PE1 uplink → P1 (Fault 1)")
    print("="*60)

    # --- Build topology ---
    print(f"\n[1/4] Building graph topology: {N} nodes, {len(EDGES)} edges")
    print(f"      Nodes: {', '.join(NODE_NAMES)}")
    A_hat = A_HAT

    # --- Generate training data (normal snapshots) ---
    print(f"\n[2/4] Generating synthetic normal training data (500 snapshots)...")
    X_train = generate_normal_snapshots(n_snapshots=500)
    print(f"      Shape: {X_train.shape}  (snapshots × nodes × features)")

    # --- Train GCN autoencoder ---
    model = GCNAutoencoder(n_nodes=N, n_features=F, hidden_dim=32)
    train(model, X_train, A_hat, epochs=100, lr=1e-3, batch_size=32)

    # --- Evaluate on a healthy snapshot (sanity check) ---
    print("[3/4] Evaluating on a HEALTHY snapshot (sanity check)...")
    healthy_snap = generate_normal_snapshots(n_snapshots=1, seed=99)[0]
    healthy_errors = compute_anomaly_scores(model, healthy_snap, A_hat)
    print(f"      Mean reconstruction error (healthy): {np.mean(healthy_errors):.6f}")
    print(f"      Max  reconstruction error (healthy): {np.max(healthy_errors):.6f}")
    print(f"      All nodes should score similarly low — no clear outlier.\n")

    # --- Inject Fault 1: MTU mismatch on PE1 ---
    print("[4/4] Injecting FAULT 1: MTU mismatch on PE1 (eth1 MTU=1400, P1 eth3 MTU=1500)...")
    print("      PE1 tx_drops_rate → 0.75 (spike), mtu_norm → 0.156 (1400/9000)")
    fault_snap = make_fault1_snapshot()
    fault_errors = compute_anomaly_scores(model, fault_snap, A_hat)

    # --- Report ---
    root_cause = print_anomaly_report(fault_errors)

    # --- Explain the top flagged node ---
    top_idx = NODE_NAMES.index(root_cause)
    print(f"  Per-feature explanation for the top anomalous node:")
    explain_node(model, fault_snap, A_hat, top_idx)

    # --- Summary ---
    pe1_score = fault_errors[NODE_NAMES.index("pe1")]
    p1_score = fault_errors[NODE_NAMES.index("p1")]
    print("  Summary")
    print("  -------")
    print(f"  PE1 anomaly score:  {pe1_score:.4f}")
    print(f"  P1  anomaly score:  {p1_score:.4f}  (neighbour effect)")
    print(f"  All others < {np.percentile(fault_errors, 75):.4f}")
    print()
    print("  Next steps from failure_pinpointing_research.md:")
    print("  • Add directionality (D-GAT) to detect the asymmetric drop direction")
    print("  • Add temporal dimension (STGNN/LSTM) to track MTU drift over time")
    print("  • Wire in real Spanner NetworkMetrics data instead of synthetic features")
    print("  • Write anomaly scores back to NodeEmbedding rows in Spanner")
    print()


if __name__ == "__main__":
    # Set random seeds for reproducibility
    np.random.seed(42)
    tf.random.set_seed(42)
    main()
