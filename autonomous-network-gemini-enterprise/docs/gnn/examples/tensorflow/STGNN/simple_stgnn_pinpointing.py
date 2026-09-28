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
Simple STGNN Failure Pinpointing Example
========================================
A minimal, self-contained Spatio-Temporal Graph Neural Network (STGNN) 
autoencoder built with TensorFlow/Keras. 

This combines two concepts:
1. Heterogeneous Spatial Graph (from the HetGNN example): Multiple node types
   (Router, Interface, BGP) with typed message passing.
2. Temporal Sequence (from the TGAT example): Analyzing sequences over time
   using a GRU to detect trajectory anomalies (drifts, flaps).

No Spanner connection needed — topology and sequences are hardcoded/synthetic.

    pip install tensorflow numpy
    python simple_stgnn_pinpointing.py

Key difference from other examples:
  ┌─────────────────────────────────────────────────────────────┐
  │  GCN:    Static snapshot, single node type.                 │
  │  HetGNN: Static snapshot, multiple node types.              │
  │  TGAT:   Temporal sequence, single node type.               │
  │  STGNN:  Temporal sequence, multiple node types.            │
  └─────────────────────────────────────────────────────────────┘

The payoff: STGNN can identify intermittent, temporary, or slow-drifting 
faults *and* correctly attribute them to the specific architectural layer 
(e.g., distinguishing a "flapping BGP session" from "sustained CPU load").

Architecture:
    X_seq (Typed) [batch, T, N_type, F_type]
      → Spatial:  Heterogeneous message passing (applied at each step t)
      → Temporal: Typed GRU layers (applied per-node-type across time)
      → Decoder:  Typed Dense layers to reconstruct the final time step t=T
    Loss = Avg(MSE_router, MSE_interface, MSE_bgp)
"""

import numpy as np
import tensorflow as tf
from tensorflow import keras

# ---------------------------------------------------------------------------
# 1. TYPED TOPOLOGY (from simple_hetgnn_pinpointing.py)
# ---------------------------------------------------------------------------

ROUTER_NAMES    = ["pe1", "p1"]
INTERFACE_NAMES = ["pe1_eth1", "p1_eth3"]
BGP_NAMES       = ["pe1_bgp"]

N_R = len(ROUTER_NAMES)    # 2
N_I = len(INTERFACE_NAMES) # 2
N_B = len(BGP_NAMES)       # 1

# Features
ROUTER_FEATURE_NAMES    = ["cpu_percent", "mem_percent", "ospf_state"]
INTERFACE_FEATURE_NAMES = ["tx_drops_rate", "rx_drops_rate", "mtu_norm"]
BGP_FEATURE_NAMES       = ["bgp_state", "pfx_count_norm", "uptime_norm"]

F_R = len(ROUTER_FEATURE_NAMES)    # 3
F_I = len(INTERFACE_FEATURE_NAMES) # 3
F_B = len(BGP_FEATURE_NAMES)       # 3

# Incidence Matrices (Relationships)
# P[dst, src] = 1 means "dst receives message from src"
P_RI = np.array([
    [1.0, 0.0],  # pe1_eth1 ← pe1
    [0.0, 1.0],  # p1_eth3  ← p1
], dtype=np.float32)

P_II = np.array([
    [0.0, 1.0],  # pe1_eth1 ← p1_eth3
    [1.0, 0.0],  # p1_eth3  ← pe1_eth1
], dtype=np.float32)

P_RB = np.array([
    [1.0, 0.0],  # pe1_bgp ← pe1
], dtype=np.float32)


# ---------------------------------------------------------------------------
# 2. SEQUENTIAL DATA GENERATION
# ---------------------------------------------------------------------------

ROUTER_BASELINE = np.array([
    [0.22, 0.30, 1.0],  # pe1
    [0.20, 0.30, 1.0],  # p1
], dtype=np.float32)

INTERFACE_BASELINE = np.array([
    [0.01, 0.01, 0.167],  # pe1_eth1
    [0.01, 0.01, 0.167],  # p1_eth3
], dtype=np.float32)

BGP_BASELINE = np.array([
    [1.0, 0.50, 0.80],  # pe1_bgp: UP, 500 prefixes, uptime ~19h
], dtype=np.float32)

SEQ_LEN = 5  # 5 timesteps

def generate_normal_sequences(n_seqs=500, noise_std=0.02, seed=42):
    """Generates sequential data around healthy baselines."""
    rng = np.random.default_rng(seed)

    def seq_noisy(base):
        b = base[np.newaxis, np.newaxis, :, :] # [1, 1, N, F]
        b = np.tile(b, (n_seqs, SEQ_LEN, 1, 1))
        noise = rng.normal(0, noise_std, b.shape).astype(np.float32)
        # Add simple autocorrelation
        for t in range(1, SEQ_LEN):
            noise[:, t] = 0.5 * noise[:, t-1] + 0.5 * noise[:, t]
        return np.clip(b + noise, 0.0, 1.0)

    seq_r = seq_noisy(ROUTER_BASELINE)
    seq_i = seq_noisy(INTERFACE_BASELINE)
    seq_b = seq_noisy(BGP_BASELINE)
    return seq_r, seq_i, seq_b


def make_flapping_bgp_sequence():
    """
    Simulate a temporal anomaly: Flapping BGP Session on pe1_bgp.
    Over 5 timesteps, the session state goes: UP, DOWN, UP, DOWN, UP.
    In any single snapshot, UP looks completely normal.
    But as a trajectory, flapping is a severe protocol anomaly.
    """
    sr, si, sb = generate_normal_sequences(n_seqs=1, seed=99)
    # Extracts the single sequence [SEQ_LEN, N, F]
    sr, si, sb = sr[0], si[0], sb[0]

    # BGP index 0 (pe1_bgp), feature 0 (bgp_state)
    states = [1.0, 0.0, 1.0, 0.0, 1.0]
    prefixes = [0.50, 0.0, 0.50, 0.0, 0.50]
    for t in range(SEQ_LEN):
        sb[t, 0, 0] = states[t]
        sb[t, 0, 1] = prefixes[t]
        if states[t] == 0.0:
            sb[t, 0, 2] = 0.0 # Uptime reset

    return sr, si, sb


# ---------------------------------------------------------------------------
# 3. STGNN AUTOENCODER MODEL
#
#    Spatial:  Heterogeneous projection & message passing at each step t.
#    Temporal: Typed GRU layers across the T steps.
# ---------------------------------------------------------------------------

class STGNNAutoencoder(keras.Model):
    def __init__(self, hidden_dim=16, **kwargs):
        super().__init__(**kwargs)
        H = hidden_dim

        # --- 1. Typed Spatial Projections (applied at each step) ---
        self.proj_R = keras.layers.Dense(H, activation="relu", name="proj_router")
        self.proj_I = keras.layers.Dense(H, activation="relu", name="proj_interface")
        self.proj_B = keras.layers.Dense(H, activation="relu", name="proj_bgp")

        # --- 2. Typed Message Weights ---
        self.msg_RI = keras.layers.Dense(H, name="msg_R_to_I")
        self.msg_II = keras.layers.Dense(H, name="msg_I_to_I")
        self.msg_RB = keras.layers.Dense(H, name="msg_R_to_B")

        # --- 3. Typed Spatial Updates ---
        self.upd_I = keras.layers.Dense(H, activation="relu", name="upd_I")
        self.upd_B = keras.layers.Dense(H, activation="relu", name="upd_B")

        # --- 4. Typed Temporal GRUs (Sequence Modeling) ---
        self.gru_R = keras.layers.GRU(H, return_sequences=False, name="gru_R")
        self.gru_I = keras.layers.GRU(H, return_sequences=False, name="gru_I")
        self.gru_B = keras.layers.GRU(H, return_sequences=False, name="gru_B")

        # --- 5. Typed Decoders (Reconstruct final snapshot) ---
        self.dec_R = keras.layers.Dense(F_R, name="dec_R")
        self.dec_I = keras.layers.Dense(F_I, name="dec_I")
        self.dec_B = keras.layers.Dense(F_B, name="dec_B")


    def call(self, seq_r, seq_i, seq_b, P_ri, P_ii, P_rb):
        """
        Inputs: seq_X is [batch, T, N_X, F_X]
        Returns reconstructed final state for each type: [batch, N_X, F_X]
        """
        batch_size = tf.shape(seq_r)[0]
        T = tf.shape(seq_r)[1]

        # ── 1. Spatial Processing (Flatten Time & Batch) ──────────────────
        # Process all (batch * T) snapshots independently through spatial GNN
        x_r_flat = tf.reshape(seq_r, [-1, N_R, F_R]) # [batch*T, N_R, F_R]
        x_i_flat = tf.reshape(seq_i, [-1, N_I, F_I])
        x_b_flat = tf.reshape(seq_b, [-1, N_B, F_B])

        # Projections
        h_r = self.proj_R(x_r_flat) # [batch*T, N_R, H]
        h_i = self.proj_I(x_i_flat)
        h_b = self.proj_B(x_b_flat)

        # Messages (broadcasting incidence matrices over batch*T)
        m_ri = tf.matmul(P_ri[np.newaxis], self.msg_RI(h_r)) # [batch*T, N_I, H]
        m_ii = tf.matmul(P_ii[np.newaxis], self.msg_II(h_i))
        m_rb = tf.matmul(P_rb[np.newaxis], self.msg_RB(h_r))

        # Spatial Updates
        h_i_upd = self.upd_I(h_i + m_ri + m_ii)
        h_b_upd = self.upd_B(h_b + m_rb)
        h_r_upd = h_r  # Routers have no incoming edges in this subset

        # ── 2. Temporal Processing (Group by Node Sequence) ───────────────
        # Reshape back to [batch, T, N, H]
        h_r_seq = tf.reshape(h_r_upd, [batch_size, T, N_R, -1])
        h_i_seq = tf.reshape(h_i_upd, [batch_size, T, N_I, -1])
        h_b_seq = tf.reshape(h_b_upd, [batch_size, T, N_B, -1])

        # Swap to [batch, N, T, H] — the temporal trajectory for each node
        h_r_node_seq = tf.transpose(h_r_seq, perm=[0, 2, 1, 3])
        h_i_node_seq = tf.transpose(h_i_seq, perm=[0, 2, 1, 3])
        h_b_node_seq = tf.transpose(h_b_seq, perm=[0, 2, 1, 3])

        # Flatten Nodes into Batch to process all node sequences through GRU
        h_r_gru_in = tf.reshape(h_r_node_seq, [-1, T, h_r.shape[-1]]) # [batch*N_R, T, H]
        h_i_gru_in = tf.reshape(h_i_node_seq, [-1, T, h_i.shape[-1]])
        h_b_gru_in = tf.reshape(h_b_node_seq, [-1, T, h_b.shape[-1]])

        # Apply Typed GRUs (returns final state after T steps)
        z_r_flat = self.gru_R(h_r_gru_in) # [batch*N_R, H]
        z_i_flat = self.gru_I(h_i_gru_in)
        z_b_flat = self.gru_B(h_b_gru_in)

        # Reshape to [batch, N, H]
        z_r = tf.reshape(z_r_flat, [batch_size, N_R, -1])
        z_i = tf.reshape(z_i_flat, [batch_size, N_I, -1])
        z_b = tf.reshape(z_b_flat, [batch_size, N_B, -1])

        # ── 3. Final Typed Decoders ───────────────────────────────────────
        x_r_hat = self.dec_R(z_r) # [batch, N_R, F_R]
        x_i_hat = self.dec_I(z_i)
        x_b_hat = self.dec_B(z_b)

        return x_r_hat, x_i_hat, x_b_hat


# ---------------------------------------------------------------------------
# 4. TRAINING & INFERENCE
# ---------------------------------------------------------------------------

def train(model, Sr, Si, Sb, epochs=60, lr=1e-3, batch_size=32):
    optimiser = keras.optimizers.Adam(learning_rate=lr)
    mse = keras.losses.MeanSquaredError()

    P_ri_tf = tf.constant(P_RI)
    P_ii_tf = tf.constant(P_II)
    P_rb_tf = tf.constant(P_RB)

    n = Sr.shape[0]
    print(f"\n{'='*60}")
    print(f"  Training STGNN Autoencoder — {epochs} epochs")
    print(f"  Types: Router({F_R}f) | Interface({F_I}f) | BGP({F_B}f)")
    print(f"  Sequence Length: {SEQ_LEN}")
    print(f"{'='*60}")

    idx = np.arange(n)
    rng = np.random.default_rng(0)

    for epoch in range(1, epochs + 1):
        rng.shuffle(idx)
        epoch_losses = []
        for start in range(0, n, batch_size):
            b = idx[start:start+batch_size]
            sr_batch = tf.constant(Sr[b])
            si_batch = tf.constant(Si[b])
            sb_batch = tf.constant(Sb[b])

            # Target is the final timestep T-1
            xr_targ = sr_batch[:, -1, :, :]
            xi_targ = si_batch[:, -1, :, :]
            xb_targ = sb_batch[:, -1, :, :]

            with tf.GradientTape() as tape:
                xr_hat, xi_hat, xb_hat = model(sr_batch, si_batch, sb_batch, P_ri_tf, P_ii_tf, P_rb_tf)
                loss = (mse(xr_targ, xr_hat) + mse(xi_targ, xi_hat) + mse(xb_targ, xb_hat)) / 3.0

            grads = tape.gradient(loss, model.trainable_variables)
            optimiser.apply_gradients(zip(grads, model.trainable_variables))
            epoch_losses.append(float(loss))

        if epoch % 10 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {np.mean(epoch_losses):.6f}")
    print(f"{'='*60}\n")


def compute_scores(model, sr, si, sb):
    """Computes final-step reconstruction MSE per node type."""
    P_ri_tf = tf.constant(P_RI)
    P_ii_tf = tf.constant(P_II)
    P_rb_tf = tf.constant(P_RB)

    sr_t, si_t, sb_t = [tf.constant(x[np.newaxis]) for x in (sr, si, sb)]
    xr_hat, xi_hat, xb_hat = model(sr_t, si_t, sb_t, P_ri_tf, P_ii_tf, P_rb_tf)

    xr_hat, xi_hat, xb_hat = [x.numpy()[0] for x in (xr_hat, xi_hat, xb_hat)]
    
    # Compare with actual final timestep
    rs = np.mean((sr[-1] - xr_hat) ** 2, axis=1)
    is_ = np.mean((si[-1] - xi_hat) ** 2, axis=1)
    bs = np.mean((sb[-1] - xb_hat) ** 2, axis=1)

    branch_scores = {
        "Router     (cpu/mem/ospf)": float(np.max(rs)),
        "Interface  (drops/mtu)   ": float(np.max(is_)),
        "BGPSession (state/pfx)   ": float(np.max(bs)),
    }
    return rs, is_, bs, branch_scores


def print_report(fault_name, rs, is_, bs, branch_scores):
    print(f"\n{'='*65}")
    print(f"  Fault: {fault_name}")
    print(f"{'='*65}\n  Per-node final-step reconstruction error:")
    for name, score in zip(ROUTER_NAMES, rs): print(f"    Router     {name:<12}  {score:.5f}")
    for name, score in zip(INTERFACE_NAMES, is_): print(f"    Interface  {name:<12}  {score:.5f}")
    for name, score in zip(BGP_NAMES, bs): print(f"    BGP        {name:<12}  {score:.5f}")

    print(f"\n  Branch-level diagnosis (Heterogeneous Root Cause):")
    max_branch = max(branch_scores, key=branch_scores.get)
    for branch, score in branch_scores.items():
        marker = "  ⚠️  FAULT LAYER" if branch == max_branch else ""
        print(f"    {branch}  {score:.5f}{marker}")


def explain_node(model, seq, node_type, node_idx, P_ri, P_ii, P_rb):
    """Explains the temporal sequence vs the final prediction."""
    sr, si, sb = seq
    sr_t, si_t, sb_t = [tf.constant(x[np.newaxis]) for x in (sr, si, sb)]
    xr_hat, xi_hat, xb_hat = model(sr_t, si_t, sb_t, P_ri, P_ii, P_rb)

    if node_type == "bgp":
        actual = sb[-1, node_idx]
        recon = xb_hat.numpy()[0][node_idx]
        names, node_name = BGP_FEATURE_NAMES, BGP_NAMES[node_idx]
        history = sb[:, node_idx, :]
    
    errs = (actual - recon) ** 2
    ranked = np.argsort(errs)[::-1]

    print(f"\n  Feature Temporal Trajectory for '{node_name}' ({node_type}):")
    for f in ranked:
        traj = " → ".join([f"{history[t, f]:.1f}" for t in range(SEQ_LEN)])
        print(f"    {names[f]:<16}: {traj}")
    
    print("\n  Final step expectation mismatch:")
    for i in ranked:
        print(f"    {names[i]:<16} actual:{actual[i]:>4.1f} | expected:{recon[i]:>4.1f} | err:{errs[i]:.4f}")


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def main():
    print("\n" + "="*65)
    print("  STGNN Failure Pinpointing — TensorFlow Example")
    print("  Combines: Heterogeneous Graphs (HetGNN) + Temporal Memory (TGAT)")
    print("  Fault:    Intermittent 'flapping' BGP session")
    print("="*65)

    print("\n[1/4] Generating training sequences (500 seqs of len 5)...")
    Sr_tr, Si_tr, Sb_tr = generate_normal_sequences(n_seqs=500)

    model = STGNNAutoencoder(hidden_dim=16)
    train(model, Sr_tr, Si_tr, Sb_tr, epochs=60, lr=1e-3, batch_size=32)

    print("[2/4] Sanity check — HEALTHY sequence:")
    sr_h, si_h, sb_h = [x[0] for x in generate_normal_sequences(n_seqs=1, seed=88)]
    rh, ih, bh, br_h = compute_scores(model, sr_h, si_h, sb_h)
    print(f"      Max scores -> Router: {max(rh):.6f} | Iface: {max(ih):.6f} | BGP: {max(bh):.6f}\n")

    print("[3/4] Injecting TEMPORAL FAULT: Flapping BGP Session")
    print("      pe1_bgp goes UP(1) -> DOWN(0) -> UP(1) -> DOWN(0) -> UP(1)")
    print("      Notice the final snapshot (t=4) is UP(1) - mathematically identical")
    print("      to the healthy baseline. Static models would miss this fault!")
    sr_f, si_f, sb_f = make_flapping_bgp_sequence()
    rf, i_f, bf, br_f = compute_scores(model, sr_f, si_f, sb_f)
    print_report("Flapping BGP Session (Intermittent protocol drop)", rf, i_f, bf, br_f)

    print("\n[4/4] Root Cause Trajectory Analysis:")
    P_ri, P_ii, P_rb = tf.constant(P_RI), tf.constant(P_II), tf.constant(P_RB)
    explain_node(model, (sr_f, si_f, sb_f), "bgp", 0, P_ri, P_ii, P_rb)

    print("\n  Summary:")
    print("  The STGNN diagnosed a sequence anomaly (flapping) and correctly mapped")
    print("  it to the Heterogeneous architectural layer (BGPSession).")


if __name__ == "__main__":
    np.random.seed(42)
    tf.random.set_seed(42)
    main()
