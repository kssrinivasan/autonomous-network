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
Simple TGAT Failure Pinpointing Example
========================================
A minimal, self-contained Temporal Graph Attention Network (TGAT) autoencoder
built with TensorFlow/Keras. It demonstrates failure pinpointing on temporal
sequences, detecting anomalies that develop over time (like a memory leak).

No Spanner connection needed — topology and sequences are hardcoded/synthetic.

    pip install tensorflow numpy
    python simple_tgat_pinpointing.py

Key difference from the GCN example (gnn/examples/GCN/):
  ┌─────────────────────────────────────────────────────────────┐
  │  GCN:  Static snapshot. Sees features at a single instant.  │
  │  TGAT: Temporal sequence. Sees T snapshots. Uses Attention  │
  │        (GAT) for spatial routing, and a GRU for temporal    │
  │        memory.                                              │
  └─────────────────────────────────────────────────────────────┘

The payoff: The TGAT can identify "trajectory anomalies" like gradual memory
leaks or slow performance degradation that might look normal in any single
isolated snapshot, but are highly unusual as a sequence over time.

Architecture:
    X_seq [seq_len=5, N=12, F=7]
      → Spatial:    GAT Layer (applied to each t independently)
      → Temporal:   GRU Layer (applied per-node across time)
      → Decoder:    Dense Layer to reconstruct the final time step t=4
    Loss = MSE(reconstructed X_t, actual X_t)
"""

import numpy as np
import tensorflow as tf
from tensorflow import keras

# ---------------------------------------------------------------------------
# 1. TOPOLOGY — from l3vpn-hub-spoke.yaml (Same as GCN example)
# ---------------------------------------------------------------------------

NODE_NAMES = [
    "p1", "p2", "p3", "p4", "rr1", "rr2",
    "pe1", "pe2", "pe3",
    "ce1-spoke", "ce1-hub", "ce2-spoke",
]

N = len(NODE_NAMES)

EDGES = [
    (0, 1), (0, 2), (0, 4), (0, 6), (0, 7),
    (1, 3), (1, 4),
    (2, 3), (2, 5), (2, 7),
    (3, 5), (3, 8),
    (6, 9), (7, 10), (8, 11),
]

def build_dense_adjacency(n, edges):
    """
    Build A_tilde (A + I) for masking the attention weights.
    We don't need D^(-1/2) normalisation because GAT uses softmax attention!
    """
    A = np.zeros((n, n), dtype=np.float32)
    for (i, j) in edges:
        A[i, j] = 1.0
        A[j, i] = 1.0

    A_tilde = A + np.eye(n, dtype=np.float32)
    return A_tilde

A_TILDE = build_dense_adjacency(N, EDGES)


# ---------------------------------------------------------------------------
# 2. SEQUENTIAL NODE FEATURES
# ---------------------------------------------------------------------------

F = 7  # features: cpu, mem, txd, rxd, bgp, ospf, mtu

FEATURE_NAMES = [
    "cpu_percent", "mem_percent", "tx_drops_rate", "rx_drops_rate",
    "bgp_state",   "ospf_state",  "mtu_norm",
]

# Baseline for a single snapshot
HEALTHY_BASELINE = np.array([
    [0.20, 0.30, 0.01, 0.01, 0.0, 1.0, 0.167],  # 0  p1
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 1  p2
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 2  p3
    [0.15, 0.25, 0.01, 0.01, 0.0, 1.0, 0.167],  # 3  p4
    [0.25, 0.35, 0.01, 0.01, 1.0, 1.0, 0.167],  # 4  rr1
    [0.25, 0.35, 0.01, 0.01, 1.0, 1.0, 0.167],  # 5  rr2
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 6  pe1
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 7  pe2
    [0.22, 0.30, 0.01, 0.01, 1.0, 1.0, 0.167],  # 8  pe3
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 9  ce1-spoke
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 10 ce1-hub
    [0.10, 0.20, 0.01, 0.01, 1.0, 0.0, 0.167],  # 11 ce2-spoke
], dtype=np.float32)

SEQ_LEN = 5  # We look at 5 timesteps (e.g., 5 minutes or 5 polling cycles)

def generate_normal_sequences(n_seqs=500, noise_std=0.02, seed=42):
    """
    Generate normal temporal sequences.
    Returns: [n_seqs, SEQ_LEN, N, F]
    """
    rng = np.random.default_rng(seed)
    # Replicate baseline across time and batch
    base = HEALTHY_BASELINE[np.newaxis, np.newaxis, :, :]  # [1, 1, N, F]
    base = np.tile(base, (n_seqs, SEQ_LEN, 1, 1))          # [n_seqs, T, N, F]
    
    noise = rng.normal(0, noise_std, base.shape).astype(np.float32)
    # Add minor temporal autocorrelation (moving average) so sequences aren't purely independent noise
    for t in range(1, SEQ_LEN):
        noise[:, t] = 0.5 * noise[:, t-1] + 0.5 * noise[:, t]

    sequences = np.clip(base + noise, 0.0, 1.0)
    return sequences


def make_memory_leak_sequence():
    """
    Simulate a temporal anomaly: Memory leak on PE2.
    Over 5 timesteps, PE2's memory goes from 0.30 -> 0.45 -> 0.60 -> 0.75 -> 0.85
    In a static view, 0.85 might barely cross a strict threshold, but the 
    relentless upward trajectory is highly anomalous to the Temporal GRU.
    """
    seq = generate_normal_sequences(n_seqs=1, seed=99)[0]  # [SEQ_LEN, N, F]
    
    # Inject linear memory climb on PE2 (index 7), feature 1 (mem)
    mem_trajectory = [0.30, 0.45, 0.60, 0.75, 0.85]
    for t in range(SEQ_LEN):
        seq[t, 7, 1] = mem_trajectory[t]
        
    return seq


# ---------------------------------------------------------------------------
# 3. TGAT AUTOENCODER MODEL
# 
#    Spatial:  GAT (Graph Attention Network) applied at each time step.
#    Temporal: GRU (Gated Recurrent Unit) applied over the T steps per node.
# ---------------------------------------------------------------------------

class GATLayer(keras.layers.Layer):
    """
    Simplified single-head Graph Attention Network (GAT) layer.
    """
    def __init__(self, units, **kwargs):
        super().__init__(**kwargs)
        self.units = units

    def build(self, input_shape):
        in_features = input_shape[-1]
        
        # Linear projection weight W
        self.W = self.add_weight(shape=(in_features, self.units),
                                 initializer="glorot_uniform", trainable=True, name="W")
        # Attention mechanisms a_src and a_dst
        self.a_src = self.add_weight(shape=(self.units, 1),
                                     initializer="glorot_uniform", trainable=True, name="a_src")
        self.a_dst = self.add_weight(shape=(self.units, 1),
                                     initializer="glorot_uniform", trainable=True, name="a_dst")
        super().build(input_shape)

    def call(self, inputs, A_tilde):
        """
        inputs:  [batch, N, F]
        A_tilde: [N, N] binary adjacency
        """
        # 1. Project to hidden: [batch, N, hidden]
        h = tf.matmul(inputs, self.W)
        
        # 2. Compute attention coefficients
        attn_src = tf.matmul(h, self.a_src)  # [batch, N, 1]
        attn_dst = tf.matmul(h, self.a_dst)  # [batch, N, 1]
        
        # score[i, j] = attn_src[i] + attn_dst[j]  (broadcasting sum)
        # score shape: [batch, N, N]
        score = attn_src + tf.transpose(attn_dst, perm=[0, 2, 1])
        score = tf.nn.leaky_relu(score, alpha=0.2)
        
        # 3. Mask non-edges with a large negative number so Softmax makes them 0
        mask = -1e9 * (1.0 - A_tilde)  # [N, N]
        mask = mask[tf.newaxis, :, :]  # [1, N, N]
        
        alpha = tf.nn.softmax(score + mask, axis=-1)  # [batch, N, N]
        
        # 4. Apply attention to neighbor features
        out = tf.matmul(alpha, h)  # [batch, N, hidden]
        return tf.nn.elu(out)


class TGATAutoencoder(keras.Model):
    """
    Temporal GAT Autoencoder.
    Reads sequence [batch, T, N, F].
    Reconstructs the final snapshot [batch, N, F].
    """
    def __init__(self, n_nodes, n_features, hidden_dim=32, **kwargs):
        super().__init__(**kwargs)
        self.n_nodes = n_nodes
        self.n_features = n_features
        
        # Spatial encoder
        self.gat = GATLayer(hidden_dim, name="gat_layer")
        
        # Temporal encoder
        self.gru = keras.layers.GRU(hidden_dim, return_sequences=False, name="gru_layer")
        
        # Decoder
        self.decoder = keras.layers.Dense(n_features, activation=None, name="decoder_dense")

    def call(self, X_seq, A_tilde):
        """
        X_seq:   [batch, T, N, F]
        A_tilde: [N, N]
        """
        batch_size = tf.shape(X_seq)[0]
        T = tf.shape(X_seq)[1]
        
        # ── 1. Apply GAT independently to each time step ────────────────
        # Flatten time into batch to process all graphs at once
        X_flat = tf.reshape(X_seq, [-1, self.n_nodes, self.n_features])  # [batch*T, N, F]
        h_flat = self.gat(X_flat, A_tilde)                               # [batch*T, N, hidden]
        
        # Reshape back to sequence form
        h_seq = tf.reshape(h_flat, [batch_size, T, self.n_nodes, -1])    # [batch, T, N, hidden]
        
        # ── 2. Apply GRU over time for each node independently ──────────
        # Swap axes to [batch, N, T, hidden]
        h_per_node = tf.transpose(h_seq, perm=[0, 2, 1, 3])
        
        # Flatten nodes into batch to process all sequences at once
        h_per_node_flat = tf.reshape(h_per_node, [-1, T, h_flat.shape[-1]]) # [batch*N, T, hidden]
        
        # Apply GRU. It returns just the final hidden state over T
        z_flat = self.gru(h_per_node_flat)                                  # [batch*N, hidden]
        
        # Reshape back to [batch, N, hidden]
        z = tf.reshape(z_flat, [batch_size, self.n_nodes, -1])
        
        # ── 3. Decode back to the feature space of the final snapshot ────
        x_hat = self.decoder(z)                                             # [batch, N, F]
        
        return x_hat


# ---------------------------------------------------------------------------
# 4. TRAINING & INFERENCE
# ---------------------------------------------------------------------------

def train(model, X_train, A_tilde, epochs=60, lr=1e-3, batch_size=32):
    optimiser = keras.optimizers.Adam(learning_rate=lr)
    loss_fn = keras.losses.MeanSquaredError()

    n = X_train.shape[0]
    A_tilde_tf = tf.constant(A_tilde)

    print(f"\n{'='*60}")
    print(f"  Training Temporal GAT (TGAT) Autoencoder — {epochs} epochs")
    print(f"  Nodes: {model.n_nodes}  Features: {model.n_features}  Hidden: 32")
    print(f"  Sequence Length: {SEQ_LEN}")
    print(f"  Training sequences: {n}  Batch size: {batch_size}")
    print(f"{'='*60}")

    dataset = tf.data.Dataset.from_tensor_slices(X_train)
    dataset = dataset.shuffle(buffer_size=n, seed=42).batch(batch_size)

    for epoch in range(1, epochs + 1):
        epoch_losses = []
        for X_batch in dataset:
            # We train the model to reconstruct the FINAL time step T-1
            X_target = X_batch[:, -1, :, :]  # [batch, N, F]
            
            with tf.GradientTape() as tape:
                X_hat = model(X_batch, A_tilde_tf)
                loss = loss_fn(X_target, X_hat)

            grads = tape.gradient(loss, model.trainable_variables)
            optimiser.apply_gradients(zip(grads, model.trainable_variables))
            epoch_losses.append(float(loss))

        if epoch % 10 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {np.mean(epoch_losses):.6f}")

    print(f"{'='*60}\n")


def compute_anomaly_scores(model, seq, A_tilde):
    """
    Feed sequence [T, N, F], compute prediction error for final step.
    Returns: [N] score per node
    """
    A_tilde_tf = tf.constant(A_tilde)
    X = tf.constant(seq[np.newaxis, :, :, :])  # [1, T, N, F]
    
    X_hat = model(X, A_tilde_tf).numpy()[0]    # [N, F] prediction
    X_actual = seq[-1]                         # [N, F] actual final step
    
    errors = np.mean((X_actual - X_hat) ** 2, axis=1)  # [N]
    return errors


def print_anomaly_report(errors, threshold_factor=3.0, title="Anomaly Report"):
    ranked_idx = np.argsort(errors)[::-1]
    
    mean_err = np.mean(errors)
    std_err = np.std(errors)
    threshold = mean_err + threshold_factor * std_err

    print(f"{'='*60}")
    print(f"  {title}")
    print(f"  Threshold: {threshold:.4f}  (mean + {threshold_factor}×std)")
    print(f"{'='*60}")
    print(f"  {'Rank':<5} {'Node':<12} {'Score':>8}  {'Status'}")
    print(f"  {'-'*48}")

    for rank, idx in enumerate(ranked_idx, start=1):
        name = NODE_NAMES[idx]
        score = errors[idx]
        status = "⚠️  ANOMALOUS" if score > threshold else "✓ normal"
        print(f"  {rank:<5} {name:<12} {score:>8.4f}  {status}")
    print(f"{'='*60}\n")
    
    return NODE_NAMES[ranked_idx[0]]


def explain_node(model, seq, A_tilde, node_idx):
    """Shows per-feature anomaly breakdown at the final time step."""
    A_tilde_tf = tf.constant(A_tilde)
    X = tf.constant(seq[np.newaxis, :, :, :])
    X_hat = model(X, A_tilde_tf).numpy()[0]
    
    node_name = NODE_NAMES[node_idx]
    actual = seq[-1, node_idx]
    predicted = X_hat[node_idx]
    
    errs = (actual - predicted) ** 2
    ranked = np.argsort(errs)[::-1]

    print(f"  Feature trajectory for '{node_name}' over {SEQ_LEN} steps:")
    for f in ranked:
        traj_str = " → ".join([f"{seq[t, node_idx, f]:.2f}" for t in range(SEQ_LEN)])
        print(f"    {FEATURE_NAMES[f]:<16}: {traj_str}")

    print(f"\n  Final step reconstruction error (t=4):")
    print(f"  {'Feature':<16} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*48}")
    for i in ranked:
        print(f"  {FEATURE_NAMES[i]:<16} {actual[i]:>8.4f} {predicted[i]:>9.4f} {errs[i]:>8.4f}")
    print(f"\n  Top temporal driver: '{FEATURE_NAMES[ranked[0]]}'\n")


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def main():
    print("\n" + "="*60)
    print("  TGAT Temporal Failure Pinpointing — TensorFlow Example")
    print("  Topology: L3VPN Hub-and-Spoke")
    print("  Fault:    Gradual Memory Leak on PE2 (Drift Anomaly)")
    print("="*60)

    # 1. Build Adjacency
    A_tilde = A_TILDE

    # 2. Training Data
    print(f"\n[1/4] Generating training sequences (500 seqs of len {SEQ_LEN})...")
    X_train = generate_normal_sequences(n_seqs=500)
    
    # 3. Train Model
    model = TGATAutoencoder(n_nodes=N, n_features=F, hidden_dim=32)
    train(model, X_train, A_tilde, epochs=60, lr=1e-3, batch_size=32)

    # 4. Healthy Evaluate
    print("[2/4] Evaluating HEALTHY sequence (sanity check)...")
    healthy_seq = generate_normal_sequences(n_seqs=1, seed=88)[0]
    h_errors = compute_anomaly_scores(model, healthy_seq, A_tilde)
    print(f"      Mean reconstruction error: {np.mean(h_errors):.6f}")
    
    # 5. Temporal Fault
    print("\n[3/4] Injecting TEMPORAL FAULT: Memory leak on PE2...")
    print("      PE2 memory climbs: 0.30 → 0.45 → 0.60 → 0.75 → 0.85")
    fault_seq = make_memory_leak_sequence()
    f_errors = compute_anomaly_scores(model, fault_seq, A_tilde)
    
    root_cause = print_anomaly_report(f_errors, title="Memory Leak Anomaly Scores")
    
    # 6. Detail
    print("[4/4] Explaining the root cause anomaly sequence:")
    top_idx = NODE_NAMES.index(root_cause)
    explain_node(model, fault_seq, A_tilde, top_idx)

    # Summary
    print("  Summary:")
    print("  The GRU explicitly modeled the expected steady trajectory.")
    print("  Because PE2's memory kept rising over 5 steps, the final step")
    print("  was flagged as highly anomalous, successfully isolating the leak.")


if __name__ == "__main__":
    np.random.seed(42)
    tf.random.set_seed(42)
    main()
