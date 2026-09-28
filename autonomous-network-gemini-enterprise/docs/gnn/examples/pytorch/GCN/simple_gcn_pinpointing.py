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
Simple GCN Failure Pinpointing — PyTorch / PyG
===============================================
Homogeneous GCN autoencoder for baseline failure detection.
Uses torch_geometric GCNConv — the simplest possible PyG model.

    pip install torch torch-geometric
    python simple_gcn_pinpointing.py

Topology:   pe1 ── p1 ── ce1-spoke   (3-node slice of hub-and-spoke lab)
Fault 1:    PE1 MTU mismatch — tx_drops spike, mtu_norm deviates

Architecture mirrors the production model pattern:
  encoder (GCNConv layers)  →  latent embedding h  →  decoder (nn.Linear)
  same (recon, embeddings) return shape as HetGNN/STGNN

Key difference from HetGNN/STGNN:
  All nodes share one feature vector and one set of weights.
  Cannot tell you *which layer* (config / protocol / hardware) the fault is in.
  That is what HetGNN adds.

See: gnn/src/model/hetgnn.py, gnn/src/model/stgnn.py for production code.
"""

import torch
import torch.nn as nn
import numpy as np
from torch_geometric.nn import GCNConv

# ──────────────────────────────────────────────────────────────────────────────
# 1. TOPOLOGY
# ──────────────────────────────────────────────────────────────────────────────

NODE_NAMES = ['pe1', 'p1', 'ce1-spoke']
FEATURE_NAMES = ['tx_drops', 'rx_drops', 'mtu_norm', 'cpu_percent', 'mem_percent', 'ospf_state']

N_NODES = len(NODE_NAMES)       # 3
N_FEATURES = len(FEATURE_NAMES) # 6

# Bidirectional edge index — pe1(0)↔p1(1), p1(1)↔ce1(2)
# GCNConv adds self-loops internally (equivalent to the A+I step in the walkthrough)
EDGE_INDEX = torch.tensor([
    [0, 1, 1, 2],  # sources
    [1, 0, 2, 1],  # destinations
], dtype=torch.long)

# ──────────────────────────────────────────────────────────────────────────────
# 2. HEALTHY BASELINE FEATURES
# ──────────────────────────────────────────────────────────────────────────────

# tx_drops, rx_drops, mtu_norm (1500/9000≈0.167), cpu, mem, ospf_state(1=Full)
BASELINE = torch.tensor([
    [0.01, 0.01, 0.167, 0.22, 0.30, 1.0],  # pe1
    [0.01, 0.01, 0.167, 0.20, 0.30, 1.0],  # p1
    [0.01, 0.01, 0.167, 0.18, 0.25, 1.0],  # ce1-spoke
], dtype=torch.float32)


def generate_normal_snapshots(n: int = 500, noise_std: float = 0.01, seed: int = 42) -> list:
    """Small Gaussian noise around the healthy baseline."""
    torch.manual_seed(seed)
    snapshots = []
    for _ in range(n):
        x = torch.clamp(BASELINE + torch.randn(N_NODES, N_FEATURES) * noise_std, 0.0, 1.0)
        snapshots.append(x)
    return snapshots


def make_fault1_snapshot() -> torch.Tensor:
    """
    Fault 1 — MTU mismatch on PE1's uplink.
    pe1 eth1 set to MTU 1400, p1 stays at 1500. Large packets silently dropped.
    BGP/OSPF control plane stays UP — this is a silent data-plane fault.
    """
    x = BASELINE.clone()
    x[0, 0] = 0.75   # pe1 tx_drops spike
    x[0, 2] = 0.156  # pe1 mtu_norm: 1400/9000
    return x


# ──────────────────────────────────────────────────────────────────────────────
# 3. GCN AUTOENCODER
#    Mirrors the production model structure:
#      - ModuleList of conv layers  (like self.convs in hetgnn.py / stgnn.py)
#      - nn.Linear decoder           (like self.decoder_dict[nt])
#      - forward() returns (recon, embeddings) tuple
# ──────────────────────────────────────────────────────────────────────────────

class GCNAutoencoder(nn.Module):
    """
    Two-layer GCN encoder + linear decoder.

    Structural correspondence to production (gnn/src/model/hetgnn.py):
      self.convs       → self.convs (ModuleList of SAGEConv via HeteroConv)
      self.decoder     → self.decoder_dict[node_type]
      forward return   → (recon_dict, out_embeddings)
    """

    def __init__(self, in_channels: int, hidden_channels: int = 32, num_layers: int = 2):
        super().__init__()
        self.hidden_channels = hidden_channels

        # Stacked GCN layers — matches production's `num_layers` parameter
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            in_ch = in_channels if i == 0 else hidden_channels
            self.convs.append(GCNConv(in_ch, hidden_channels))

        # Linear decoder — reconstructs original feature space for anomaly scoring
        self.decoder = nn.Linear(hidden_channels, in_channels)

    def encode(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        h = x
        for conv in self.convs:
            h = conv(h, edge_index).relu()
        return h  # [N, hidden_channels]

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor):
        """
        Returns:
            recon:      [N, in_channels]  — reconstructed features
            embeddings: [N, hidden_channels] — latent node embeddings
        Mirrors production signature: (recon_dict, out_embeddings)
        """
        embeddings = self.encode(x, edge_index)
        recon = self.decoder(embeddings)
        return recon, embeddings


# ──────────────────────────────────────────────────────────────────────────────
# 4. TRAINING
# ──────────────────────────────────────────────────────────────────────────────

def train(
    model: GCNAutoencoder,
    snapshots: list,
    edge_index: torch.Tensor,
    epochs: int = 100,
    lr: float = 1e-3,
) -> None:
    """
    Self-supervised reconstruction training: minimise MSE(recon, actual).
    No fault labels needed — trains on healthy telemetry only.
    """
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    print(f"\n{'='*54}")
    print(f"  Training GCN Autoencoder — {epochs} epochs")
    print(f"  Nodes: {N_NODES}   Features: {N_FEATURES}   Snapshots: {len(snapshots)}")
    print(f"{'='*54}")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0

        for x in snapshots:
            optimiser.zero_grad()
            recon, _ = model(x, edge_index)
            loss = loss_fn(recon, x)
            loss.backward()
            optimiser.step()
            total_loss += loss.item()

        if epoch % 20 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {total_loss / len(snapshots):.6f}")

    print(f"{'='*54}\n")


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANOMALY SCORING
# ──────────────────────────────────────────────────────────────────────────────

@torch.no_grad()
def compute_scores(
    model: GCNAutoencoder,
    x: torch.Tensor,
    edge_index: torch.Tensor,
) -> tuple:
    """
    Per-node reconstruction MSE — the anomaly score.
    Same scoring pattern as production: MSE over feature dimension per node.
    """
    model.eval()
    recon, embeddings = model(x, edge_index)
    # Per-node MSE: average squared error across all features
    scores = ((recon - x) ** 2).mean(dim=1).numpy()  # [N]
    return scores, recon.numpy(), embeddings.numpy()


def print_report(
    fault_name: str,
    scores: np.ndarray,
    recon: np.ndarray,
    actual: np.ndarray,
) -> None:
    print(f"\n{'='*58}")
    print(f"  {fault_name}")
    print(f"{'='*58}")

    ranked = np.argsort(scores)[::-1]
    print(f"\n  Per-node anomaly scores (ranked):")
    for i in ranked:
        marker = "  ⚠️  ANOMALOUS" if scores[i] == scores[ranked[0]] else ""
        print(f"    {NODE_NAMES[i]:<12}  score={scores[i]:.5f}{marker}")

    top = ranked[0]
    errs = (actual[top] - recon[top]) ** 2
    feat_rank = np.argsort(errs)[::-1]
    print(f"\n  Feature breakdown for '{NODE_NAMES[top]}':")
    print(f"  {'Feature':<16} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*46}")
    for f in feat_rank:
        print(f"  {FEATURE_NAMES[f]:<16} {actual[top,f]:>8.4f} {recon[top,f]:>9.4f} {errs[f]:>8.4f}")
    print(f"\n  Root cause: '{NODE_NAMES[top]}'  |  Top driver: '{FEATURE_NAMES[feat_rank[0]]}'")


# ──────────────────────────────────────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*58)
    print("  GCN Failure Pinpointing — PyTorch / torch_geometric")
    print("  Topology: pe1 ── p1 ── ce1-spoke")
    print("="*58)

    # Train on healthy data
    snapshots = generate_normal_snapshots(n=500)
    model = GCNAutoencoder(in_channels=N_FEATURES, hidden_channels=32, num_layers=2)
    train(model, snapshots, EDGE_INDEX, epochs=100, lr=1e-3)

    # Sanity check — healthy snapshot should score near zero
    print("[Sanity] Healthy snapshot (all scores should be low):")
    x_h = generate_normal_snapshots(n=1, seed=99)[0]
    scores_h, _, _ = compute_scores(model, x_h, EDGE_INDEX)
    for name, s in zip(NODE_NAMES, scores_h):
        print(f"  {name:<12}  {s:.6f}")

    # Fault 1 — MTU mismatch on pe1
    print("\n[Fault 1] MTU mismatch on pe1:")
    x_f1 = make_fault1_snapshot()
    scores_f1, recon_f1, _ = compute_scores(model, x_f1, EDGE_INDEX)
    print_report("Fault 1: MTU mismatch on pe1", scores_f1, recon_f1, x_f1.numpy())

    print("\n  Next steps:")
    print("  • The GCN tells you 'pe1 is anomalous'")
    print("  • The HetGNN adds 'it is the Interface branch, not BGP or Router config'")
    print("  • See: pytorch/HetGNN/simple_hetgnn_pinpointing.py")
    print()


if __name__ == "__main__":
    torch.manual_seed(42)
    main()
