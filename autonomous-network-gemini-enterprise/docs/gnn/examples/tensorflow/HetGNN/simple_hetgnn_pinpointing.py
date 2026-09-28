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
Simple HetGNN Failure Pinpointing Example
==========================================
A minimal, self-contained Heterogeneous Graph Neural Network (HetGNN)
autoencoder built with TensorFlow/Keras that demonstrates typed
failure pinpointing on a subset of the hub-and-spoke topology.

No Spanner connection needed — topology and features are hardcoded/synthetic.

    pip install tensorflow numpy
    python simple_hetgnn_pinpointing.py

Key difference from the GCN example (gnn/examples/GCN/):
  ┌─────────────────────────────────────────────────────────────┐
  │  GCN:    one node type, one feature vector per node         │
  │  HetGNN: TYPED nodes — Router / Interface / BGPSession      │
  │          Each type has its own feature set and its own       │
  │          encoder, message weights, and decoder branch.       │
  └─────────────────────────────────────────────────────────────┘

The payoff: after detecting an anomaly, the model tells you *which
component layer* the fault lives in:
  - Router branch anomalous    → CPU/memory/OSPF hardware or config issue
  - Interface branch anomalous → MTU mismatch, drops, physical problem  ← Fault 1
  - BGPSession branch anomalous→ protocol fault, session teardown        ← Fault 2

This maps directly to the research doc's "Component Level Decomposition"
embedding and the HetGNN Spanner entity model:
  PhysicalRouter → HasInterface → PhysicalInterface
  PhysicalRouter → HasBGP      → BGPSession

Architecture:
    Typed inputs  →  Typed projections  →  Typed message passing
                  →  Typed update       →  Typed decoders
                  →  Per-branch MSE anomaly scores

    Router branch:    X_R [N_R, 3] → W_R → h_R [N_R, H]
    Interface branch: X_I [N_I, 3] → W_I → h_I [N_I, H]  + msgs from Router & peers
    BGP branch:       X_B [N_B, 3] → W_B → h_B [N_B, H]  + msg from Router

    Decoders reconstruct each type back to its original feature space.
    Loss = (loss_router + loss_interface + loss_bgp) / 3
"""

import numpy as np
import tensorflow as tf
from tensorflow import keras

# ---------------------------------------------------------------------------
# 1. TYPED TOPOLOGY
#    Subset of the hub-and-spoke lab focused on the PE1↔P1 fault link.
# ---------------------------------------------------------------------------

# --- Node names per type ---
ROUTER_NAMES    = ["pe1", "p1"]
INTERFACE_NAMES = ["pe1_eth1", "p1_eth3"]   # the PE1↔P1 p2p link interfaces
BGP_NAMES       = ["pe1_bgp"]               # PE1's iBGP session to RR1

N_R = len(ROUTER_NAMES)    # 2
N_I = len(INTERFACE_NAMES) # 2
N_B = len(BGP_NAMES)       # 1

# --- Feature names per type (3 features each for simplicity) ---
ROUTER_FEATURE_NAMES    = ["cpu_percent", "mem_percent",    "ospf_state"]
INTERFACE_FEATURE_NAMES = ["tx_drops_rate", "rx_drops_rate", "mtu_norm"]
BGP_FEATURE_NAMES       = ["bgp_state",  "pfx_count_norm", "uptime_norm"]

F_R = len(ROUTER_FEATURE_NAMES)    # 3
F_I = len(INTERFACE_FEATURE_NAMES) # 3
F_B = len(BGP_FEATURE_NAMES)       # 3

# ---------------------------------------------------------------------------
# 2. INCIDENCE MATRICES (encode graph structure per edge type)
#
#    These are the heterogeneous equivalent of A_hat in the GCN.
#    Each matrix encodes ONE relationship type between TWO node types.
#    P[dst, src] = 1 means "dst receives a message from src".
# ---------------------------------------------------------------------------

# HasInterface: Router → Interface
#   P_RI[i, r] = 1 if interface i belongs to router r
#   pe1_eth1 ← pe1 (row 0, col 0)
#   p1_eth3  ← p1  (row 1, col 1)
P_RI = np.array([
    [1.0, 0.0],  # pe1_eth1 ← pe1
    [0.0, 1.0],  # p1_eth3  ← p1
], dtype=np.float32)

# ConnectsTo: Interface ↔ Interface (physical link, bidirectional)
#   P_II[i_dst, i_src] = 1 if i_src sends to i_dst
#   pe1_eth1 ← p1_eth3  (row 0, col 1)
#   p1_eth3  ← pe1_eth1 (row 1, col 0)
P_II = np.array([
    [0.0, 1.0],  # pe1_eth1 ← p1_eth3
    [1.0, 0.0],  # p1_eth3  ← pe1_eth1
], dtype=np.float32)

# HasBGP: Router → BGPSession
#   P_RB[b, r] = 1 if BGP session b is hosted on router r
#   pe1_bgp ← pe1 (row 0, col 0)
P_RB = np.array([
    [1.0, 0.0],  # pe1_bgp ← pe1
], dtype=np.float32)

# ---------------------------------------------------------------------------
# 3. HEALTHY BASELINE FEATURES
# ---------------------------------------------------------------------------

# Router: cpu, mem, ospf_state (1=Full)
ROUTER_BASELINE = np.array([
    [0.22, 0.30, 1.0],  # pe1
    [0.20, 0.30, 1.0],  # p1
], dtype=np.float32)

# Interface: tx_drops_rate (log-scaled), rx_drops_rate, mtu_norm (mtu/9000)
INTERFACE_BASELINE = np.array([
    [0.01, 0.01, 0.167],  # pe1_eth1  (1500/9000 ≈ 0.167)
    [0.01, 0.01, 0.167],  # p1_eth3
], dtype=np.float32)

# BGP: state (1=Established), prefix count / 1000, session uptime / 86400 sec
BGP_BASELINE = np.array([
    [1.0, 0.50, 0.80],  # pe1_bgp: UP, 500 prefixes, uptime ~19h
], dtype=np.float32)


def generate_normal_snapshots(n_snapshots=500, noise_std=0.02, seed=42):
    """
    Synthetic training data: small Gaussian noise around healthy baselines.
    Returns three arrays shaped [n_snapshots, N_type, F_type].
    """
    rng = np.random.default_rng(seed)

    def noisy(base):
        n = rng.normal(0, noise_std, (n_snapshots,) + base.shape).astype(np.float32)
        return np.clip(base[np.newaxis] + n, 0.0, 1.0)

    return noisy(ROUTER_BASELINE), noisy(INTERFACE_BASELINE), noisy(BGP_BASELINE)


def make_fault1_snapshot():
    """
    Fault 1 — MTU mismatch on PE1's uplink (pe1_eth1 → 1400 bytes, p1_eth3 → 1500).
    Interface branch fault: TX drops spike, MTU deviates.
    BGP and Router branches remain healthy.
    """
    xr = ROUTER_BASELINE.copy()
    xi = INTERFACE_BASELINE.copy()
    xb = BGP_BASELINE.copy()

    # pe1_eth1 (interface index 0): TX drops spike, MTU wrong
    xi[0, 0] = 0.75   # tx_drops_rate spiked
    xi[0, 2] = 0.156  # mtu_norm: 1400 / 9000

    return xr, xi, xb


def make_fault2_snapshot():
    """
    Fault 2 — PE2↔CE1-HUB BGP session teardown (adapted here to pe1_bgp going down).
    BGP branch fault: session flips to Down, prefix count drops to zero, uptime resets.
    Router and Interface branches remain healthy.
    """
    xr = ROUTER_BASELINE.copy()
    xi = INTERFACE_BASELINE.copy()
    xb = BGP_BASELINE.copy()

    # pe1_bgp (BGP index 0): session torn down
    xb[0, 0] = 0.0   # bgp_state: 0 = Down
    xb[0, 1] = 0.0   # pfx_count_norm: 0 prefixes
    xb[0, 2] = 0.0   # uptime_norm: timer reset

    return xr, xi, xb


# ---------------------------------------------------------------------------
# 4. HetGNN AUTOENCODER
#
#    Every matrix W below corresponds to ONE node type or ONE edge type.
#    Nothing is shared across types — that is what makes it heterogeneous.
#
#    Message passing equations:
#      h_R  = ReLU(X_R  @ W_proj_R)                           — typed projection
#      h_I  = ReLU(X_I  @ W_proj_I)
#      h_B  = ReLU(X_B  @ W_proj_B)
#
#      m_RI = P_RI @ (h_R @ W_msg_RI)   ← HasInterface message
#      m_II = P_II @ (h_I @ W_msg_II)   ← ConnectsTo  message
#      m_RB = P_RB @ (h_R @ W_msg_RB)   ← HasBGP      message
#
#      h_I' = ReLU((h_I + m_RI + m_II) @ W_upd_I)  — interface update
#      h_B' = ReLU((h_B + m_RB)        @ W_upd_B)  — BGP update
#      h_R' = h_R                                    — no incoming edges here
#
#      X_R_hat  = h_R'  @ W_dec_R     — typed reconstruction
#      X_I_hat  = h_I'  @ W_dec_I
#      X_B_hat  = h_B'  @ W_dec_B
# ---------------------------------------------------------------------------

class HetGNNAutoencoder(keras.Model):
    """
    Heterogeneous GNN Autoencoder with three typed branches:
      Router | Interface | BGPSession

    Each branch has its own projection, message weights, update weights,
    and decoder — nothing is shared between node types.
    """

    def __init__(self, hidden_dim=16, **kwargs):
        super().__init__(**kwargs)
        H = hidden_dim

        # --- Typed input projections (encoder) ---
        # Each projects its node type's raw features into the shared hidden space H.
        self.proj_R = keras.layers.Dense(H, activation="relu", name="proj_router")
        self.proj_I = keras.layers.Dense(H, activation="relu", name="proj_interface")
        self.proj_B = keras.layers.Dense(H, activation="relu", name="proj_bgp")

        # --- Per-edge-type message weights ---
        # Separate W per edge type so the model learns that "a Router→Interface
        # message" carries different information than "an Interface→Interface message".
        self.msg_RI = keras.layers.Dense(H, name="msg_router_to_interface")
        self.msg_II = keras.layers.Dense(H, name="msg_interface_to_interface")
        self.msg_RB = keras.layers.Dense(H, name="msg_router_to_bgp")

        # --- Per-type update weights ---
        # After aggregating all incoming messages, each node type applies its own
        # update transformation — again, not shared.
        self.upd_I = keras.layers.Dense(H, activation="relu", name="update_interface")
        self.upd_B = keras.layers.Dense(H, activation="relu", name="update_bgp")

        # --- Per-type decoders ---
        # Each decoder maps from the shared hidden space back to that type's
        # original feature space. F_R=3, F_I=3, F_B=3 in this example.
        self.dec_R = keras.layers.Dense(F_R, name="decoder_router")
        self.dec_I = keras.layers.Dense(F_I, name="decoder_interface")
        self.dec_B = keras.layers.Dense(F_B, name="decoder_bgp")

    def call(self, x_r, x_i, x_b, P_ri, P_ii, P_rb):
        """
        Args:
            x_r:  Router features    [batch, N_R, F_R]
            x_i:  Interface features [batch, N_I, F_I]
            x_b:  BGP features       [batch, N_B, F_B]
            P_ri: HasInterface incidence [N_I, N_R]  (constant)
            P_ii: ConnectsTo incidence  [N_I, N_I]  (constant)
            P_rb: HasBGP incidence      [N_B, N_R]  (constant)
        Returns:
            x_r_hat, x_i_hat, x_b_hat — reconstructed features per type
        """
        # ── Step 1: Typed projections ─────────────────────────────────────
        h_r = self.proj_R(x_r)  # [batch, N_R, H]
        h_i = self.proj_I(x_i)  # [batch, N_I, H]
        h_b = self.proj_B(x_b)  # [batch, N_B, H]

        # ── Step 2: Typed messages ────────────────────────────────────────
        # HasInterface: routers → interfaces
        #   W_RI first transforms h_r → [batch, N_R, H]
        #   Then P_RI[None] @ result aggregates at each interface
        #   P_RI[None] shape [1, N_I, N_R] broadcasts with [batch, N_R, H]
        m_ri = tf.matmul(P_ri[np.newaxis], self.msg_RI(h_r))  # [batch, N_I, H]

        # ConnectsTo: interfaces → interfaces (peer messages across the physical link)
        m_ii = tf.matmul(P_ii[np.newaxis], self.msg_II(h_i))  # [batch, N_I, H]

        # HasBGP: routers → BGP sessions
        m_rb = tf.matmul(P_rb[np.newaxis], self.msg_RB(h_r))  # [batch, N_B, H]

        # ── Step 3: Typed updates ─────────────────────────────────────────
        # Interface: receives messages from its parent router AND its peer interface
        h_i_new = self.upd_I(h_i + m_ri + m_ii)  # [batch, N_I, H]

        # BGP: receives message from its parent router
        h_b_new = self.upd_B(h_b + m_rb)          # [batch, N_B, H]

        # Router: no incoming edges in this graph — keeps its own projection
        h_r_new = h_r                              # [batch, N_R, H]

        # ── Step 4: Typed decoders ────────────────────────────────────────
        x_r_hat = self.dec_R(h_r_new)  # [batch, N_R, F_R]
        x_i_hat = self.dec_I(h_i_new)  # [batch, N_I, F_I]
        x_b_hat = self.dec_B(h_b_new)  # [batch, N_B, F_B]

        return x_r_hat, x_i_hat, x_b_hat


# ---------------------------------------------------------------------------
# 5. TRAINING
# ---------------------------------------------------------------------------

def train(model, Xr, Xi, Xb, epochs=100, lr=1e-3, batch_size=32):
    """
    Multi-task reconstruction loss:
        Loss = (MSE_router + MSE_interface + MSE_bgp) / 3

    Equal weights here (α = β = γ = 1/3). The research doc recommends
    tuning these — e.g. increase the BGP weight if protocol faults are
    the primary concern.
    """
    optimiser = keras.optimizers.Adam(learning_rate=lr)
    mse = keras.losses.MeanSquaredError()

    # Pre-cast incidence matrices to TF constants
    P_ri_tf = tf.constant(P_RI)
    P_ii_tf = tf.constant(P_II)
    P_rb_tf = tf.constant(P_RB)

    n = Xr.shape[0]
    rng = np.random.default_rng(0)

    print(f"\n{'='*58}")
    print(f"  Training HetGNN Autoencoder — {epochs} epochs")
    print(f"  Branches: Router({F_R}f) | Interface({F_I}f) | BGP({F_B}f)")
    print(f"  Hidden dim: {16}   Snapshots: {n}   Batch: {batch_size}")
    print(f"{'='*58}")

    for epoch in range(1, epochs + 1):
        idx = rng.permutation(n)
        epoch_losses = []

        for start in range(0, n, batch_size):
            b = idx[start:start + batch_size]
            xr = tf.constant(Xr[b])
            xi = tf.constant(Xi[b])
            xb = tf.constant(Xb[b])

            with tf.GradientTape() as tape:
                xr_hat, xi_hat, xb_hat = model(xr, xi, xb, P_ri_tf, P_ii_tf, P_rb_tf)
                loss_r = mse(xr, xr_hat)
                loss_i = mse(xi, xi_hat)
                loss_b = mse(xb, xb_hat)
                loss = (loss_r + loss_i + loss_b) / 3.0

            grads = tape.gradient(loss, model.trainable_variables)
            optimiser.apply_gradients(zip(grads, model.trainable_variables))
            epoch_losses.append(float(loss))

        if epoch % 20 == 0 or epoch == 1:
            avg = np.mean(epoch_losses)
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {avg:.6f}")

    print(f"{'='*58}\n")


# ---------------------------------------------------------------------------
# 6. ANOMALY SCORING
# ---------------------------------------------------------------------------

def compute_scores(model, xr, xi, xb):
    """
    Compute per-node, per-type reconstruction error (anomaly score).

    Returns:
        router_scores    [N_R]  — one MSE per router node
        interface_scores [N_I]  — one MSE per interface node
        bgp_scores       [N_B]  — one MSE per BGP session node
        branch_scores    dict   — max score per type (the diagnostic signal)
    """
    P_ri_tf = tf.constant(P_RI)
    P_ii_tf = tf.constant(P_II)
    P_rb_tf = tf.constant(P_RB)

    xr_t = tf.constant(xr[np.newaxis])
    xi_t = tf.constant(xi[np.newaxis])
    xb_t = tf.constant(xb[np.newaxis])

    xr_hat, xi_hat, xb_hat = model(xr_t, xi_t, xb_t, P_ri_tf, P_ii_tf, P_rb_tf)

    xr_hat = xr_hat.numpy()[0]
    xi_hat = xi_hat.numpy()[0]
    xb_hat = xb_hat.numpy()[0]

    router_scores    = np.mean((xr - xr_hat) ** 2, axis=1)  # [N_R]
    interface_scores = np.mean((xi - xi_hat) ** 2, axis=1)  # [N_I]
    bgp_scores       = np.mean((xb - xb_hat) ** 2, axis=1)  # [N_B]

    branch_scores = {
        "Router    (cpu/mem/ospf)  ": float(np.max(router_scores)),
        "Interface (drops/mtu)     ": float(np.max(interface_scores)),
        "BGPSession (state/prefixes)": float(np.max(bgp_scores)),
    }

    return router_scores, interface_scores, bgp_scores, branch_scores


def print_report(fault_name, router_scores, interface_scores, bgp_scores, branch_scores):
    """
    Print the per-branch anomaly report.
    The branch with the highest score tells you WHICH layer the fault is in.
    """
    print(f"\n{'='*62}")
    print(f"  Fault: {fault_name}")
    print(f"{'='*62}")

    print(f"\n  Per-node scores:")
    for name, score in zip(ROUTER_NAMES, router_scores):
        print(f"    Router     {name:<12}  {score:.5f}")
    for name, score in zip(INTERFACE_NAMES, interface_scores):
        print(f"    Interface  {name:<12}  {score:.5f}")
    for name, score in zip(BGP_NAMES, bgp_scores):
        print(f"    BGP        {name:<12}  {score:.5f}")

    print(f"\n  Branch-level diagnosis (max score per branch):")
    max_branch = max(branch_scores, key=branch_scores.get)
    for branch, score in branch_scores.items():
        marker = "  ⚠️  FAULT LAYER" if branch == max_branch else ""
        print(f"    {branch}  {score:.5f}{marker}")

    print(f"\n  → Root layer: {max_branch.strip()}")


def explain_node(model, xr, xi, xb, node_type, node_idx):
    """Per-feature breakdown for the most anomalous node."""
    P_ri_tf = tf.constant(P_RI)
    P_ii_tf = tf.constant(P_II)
    P_rb_tf = tf.constant(P_RB)

    xr_hat, xi_hat, xb_hat = model(
        tf.constant(xr[np.newaxis]),
        tf.constant(xi[np.newaxis]),
        tf.constant(xb[np.newaxis]),
        P_ri_tf, P_ii_tf, P_rb_tf,
    )

    if node_type == "interface":
        actual = xi[node_idx]
        recon  = xi_hat.numpy()[0][node_idx]
        names  = INTERFACE_FEATURE_NAMES
        node_name = INTERFACE_NAMES[node_idx]
    elif node_type == "bgp":
        actual = xb[node_idx]
        recon  = xb_hat.numpy()[0][node_idx]
        names  = BGP_FEATURE_NAMES
        node_name = BGP_NAMES[node_idx]
    else:
        actual = xr[node_idx]
        recon  = xr_hat.numpy()[0][node_idx]
        names  = ROUTER_FEATURE_NAMES
        node_name = ROUTER_NAMES[node_idx]

    errs = (actual - recon) ** 2
    ranked = np.argsort(errs)[::-1]

    print(f"\n  Feature breakdown for '{node_name}' ({node_type}):")
    print(f"  {'Feature':<20} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*52}")
    for i in ranked:
        print(f"  {names[i]:<20} {actual[i]:>8.4f} {recon[i]:>9.4f} {errs[i]:>8.4f}")
    print(f"\n  Top driver: '{names[ranked[0]]}'\n")


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def main():
    print("\n" + "="*62)
    print("  HetGNN Failure Pinpointing — Simple TensorFlow Example")
    print("  Topology: PE1↔P1 link (hub-and-spoke lab)")
    print("  Node types: Router | Interface | BGPSession")
    print("="*62)

    print(f"\n[1/5] Topology")
    print(f"      Routers:    {ROUTER_NAMES}")
    print(f"      Interfaces: {INTERFACE_NAMES}")
    print(f"      BGP:        {BGP_NAMES}")
    print(f"      Edge types: HasInterface, ConnectsTo, HasBGP")

    # --- Training data ---
    print(f"\n[2/5] Generating normal training data (500 snapshots)...")
    Xr_train, Xi_train, Xb_train = generate_normal_snapshots(500)
    print(f"      Router    {Xr_train.shape}   Interface {Xi_train.shape}   BGP {Xb_train.shape}")

    # --- Train ---
    model = HetGNNAutoencoder(hidden_dim=16)
    train(model, Xr_train, Xi_train, Xb_train, epochs=100, lr=1e-3, batch_size=32)

    # --- Sanity check on healthy snapshot ---
    print("[3/5] Sanity check — HEALTHY snapshot:")
    xr_h, xi_h, xb_h = generate_normal_snapshots(1, seed=99)
    rs, is_, bs, br = compute_scores(model, xr_h[0], xi_h[0], xb_h[0])
    print(f"      Router branch max:    {max(rs):.6f}  (all should be low)")
    print(f"      Interface branch max: {max(is_):.6f}")
    print(f"      BGP branch max:       {max(bs):.6f}\n")

    # --- Fault 1: MTU mismatch → Interface branch ---
    print("[4/5] FAULT 1 — MTU mismatch on pe1_eth1 (Interface layer fault)")
    xr1, xi1, xb1 = make_fault1_snapshot()
    rs1, is1, bs1, br1 = compute_scores(model, xr1, xi1, xb1)
    print_report("MTU mismatch on pe1_eth1 (Fault 1)", rs1, is1, bs1, br1)
    top_i_idx = int(np.argmax(is1))
    explain_node(model, xr1, xi1, xb1, "interface", top_i_idx)

    # --- Fault 2: BGP session teardown → BGP branch ---
    print("[5/5] FAULT 2 — pe1_bgp session teardown (Protocol layer fault)")
    xr2, xi2, xb2 = make_fault2_snapshot()
    rs2, is2, bs2, br2 = compute_scores(model, xr2, xi2, xb2)
    print_report("BGP session teardown on pe1_bgp (Fault 2)", rs2, is2, bs2, br2)
    top_b_idx = int(np.argmax(bs2))
    explain_node(model, xr2, xi2, xb2, "bgp", top_b_idx)

    # --- Summary ---
    print("="*62)
    print("  Summary — HetGNN branch diagnosis")
    print("="*62)
    print(f"  Fault 1 (MTU mismatch):")
    print(f"    Interface branch  {max(is1):.5f}  ← highest  → physical/config layer")
    print(f"    BGP branch        {max(bs1):.5f}")
    print(f"    Router branch     {max(rs1):.5f}")
    print(f"  Fault 2 (BGP teardown):")
    print(f"    BGP branch        {max(bs2):.5f}  ← highest  → protocol layer")
    print(f"    Interface branch  {max(is2):.5f}")
    print(f"    Router branch     {max(rs2):.5f}")
    print()
    print("  This is the HetGNN's 'Component Level Decomposition':")
    print("  the branch that fires highest directly names the fault layer,")
    print("  without needing fault labels or manual threshold tuning.")
    print()
    print("  Next steps:")
    print("  • Add Config node type to separate MTU misconfiguration from hardware drops")
    print("  • Add OSPF_Adjacency node type for finer protocol decomposition")
    print("  • Wire in real Spanner data (PhysicalRouter, PhysicalInterface, BGPSession)")
    print("  • Write branch anomaly scores to NodeEmbedding rows in Spanner")
    print()


if __name__ == "__main__":
    np.random.seed(42)
    tf.random.set_seed(42)
    main()
