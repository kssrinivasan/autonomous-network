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
Simple HetGNN Failure Pinpointing — PyTorch / PyG
==================================================
Heterogeneous GNN autoencoder demonstrating typed branch anomaly scoring.

    pip install torch torch-geometric
    python simple_hetgnn_pinpointing.py

Topology (subset of hub-and-spoke lab):
    [Router: pe1] ──has_interface──▶ [Interface: pe1_eth1]
    [Router: p1 ] ──has_interface──▶ [Interface: p1_eth3 ]
                                            ↕  connects_to
    [Router: pe1] ──has_bgp──────▶  [BGPSession: pe1_bgp]

This is a SIMPLIFIED version of gnn/src/model/hetgnn.py.
Structural changes from production:
  - Synthetic in-memory data instead of Spanner
  - hidden_channels=32 instead of 64
  - 2 node types used for faults (no OSPF_Adjacency, Config sub-types)

Production code reference: gnn/src/model/hetgnn.py
  - Same HeteroConv + SAGEConv architecture
  - Same metadata tuple: (node_types, edge_types)
  - Same set_input_dims() pattern for lazy weight init
  - Same forward() signature: x_dict, edge_index_dict → (recon_dict, out_embeddings)
  - Same lin_dict / decoder_dict ModuleDict pattern

Key output — branch-level anomaly score:
  Router branch highest    → cpu/mem/ospf fault
  Interface branch highest → MTU mismatch / drops (← Fault 1)
  BGP branch highest       → session teardown     (← Fault 2)

Maths walkthrough cross-reference
----------------------------------
  maths_walkthrough.md §Setup         → NODE_TYPES, EDGE_TYPES, EDGE_INDEX_DICT,
                                         *_BASELINE tensors, INPUT_DIMS
  maths_walkthrough.md §A (Part A)    → lin_dict, set_input_dims(), forward() step 1
  maths_walkthrough.md §B (Part B)    → self.convs (HeteroConv / SAGEConv), forward() step 2
  maths_walkthrough.md §C (Part C)    → decoder_dict, forward() step 3, compute_scores()
  maths_walkthrough.md §D (Part D)    → train(), loss = (loss_r + loss_i + loss_b) / 3
  maths_walkthrough.md §E (Part E)    → scaling: weights shaped by F & H, not N
"""

import torch
import torch.nn as nn
import numpy as np
from torch_geometric.nn import HeteroConv, SAGEConv

# ──────────────────────────────────────────────────────────────────────────────
# 1. TOPOLOGY METADATA
#    Same format as production: (node_types_list, edge_types_list)
#    Used to build HeteroConv dicts and RNN dicts.
#
#    maths_walkthrough.md §Setup — "Node counts and feature dims"
#    The three node types below correspond to the three rows of the Setup table.
#    The three edge types correspond to the three labelled arrows in the topology
#    diagram and drive which SAGEConv modules are created in Part B.
# ──────────────────────────────────────────────────────────────────────────────

NODE_TYPES = ['router', 'interface', 'bgp_session']

EDGE_TYPES = [
    # maths_walkthrough.md §B.1 — edge type ('router', 'has_interface', 'interface')
    ('router',    'has_interface', 'interface'),
    # maths_walkthrough.md §B.2 — edge type ('interface', 'connects_to', 'interface')
    ('interface', 'connects_to',   'interface'),
    # maths_walkthrough.md §B.5 — edge type ('router', 'has_bgp', 'bgp_session')
    ('router',    'has_bgp',       'bgp_session'),
]

METADATA = (NODE_TYPES, EDGE_TYPES)

# Edge indices [2, E] — local indices within each node type.
# SAGEConv in HeteroConv uses (src_local_idx, dst_local_idx).
#
# maths_walkthrough.md §B.1 — "Edge index: pe1(router 0)→pe1_eth1(iface 0),
#                               p1(router 1)→p1_eth3(iface 1)"
# maths_walkthrough.md §B.2 — "Both interfaces → symmetric peer messages"
# maths_walkthrough.md §B.5 — "pe1 → pe1_bgp (1 edge, 1 neighbour)"
EDGE_INDEX_DICT = {
    # pe1(router 0)→pe1_eth1(iface 0),  p1(router 1)→p1_eth3(iface 1)
    ('router', 'has_interface', 'interface'):  torch.tensor([[0, 1], [0, 1]], dtype=torch.long),
    # pe1_eth1(iface 0)↔p1_eth3(iface 1) — bidirectional physical link
    ('interface', 'connects_to', 'interface'): torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
    # pe1(router 0)→pe1_bgp(bgp 0)
    ('router', 'has_bgp', 'bgp_session'):      torch.tensor([[0], [0]], dtype=torch.long),
}

# Node names for reporting
NODE_NAMES = {
    'router':      ['pe1', 'p1'],
    'interface':   ['pe1_eth1', 'p1_eth3'],
    'bgp_session': ['pe1_bgp'],
}

# Feature names per type
FEATURE_NAMES = {
    'router':      ['cpu_percent', 'mem_percent', 'ospf_state'],
    'interface':   ['tx_drops_rate', 'rx_drops_rate', 'mtu_norm'],
    'bgp_session': ['bgp_state', 'pfx_count_norm', 'uptime_norm'],
}

# F (feature count) per node type — shapes W_proj and W_dec but NOT the number
# of nodes N.  maths_walkthrough.md §E.1 — "weight matrices are shaped by F and
# H, not by node count N".
INPUT_DIMS = {nt: len(FEATURE_NAMES[nt]) for nt in NODE_TYPES}  # all 3

# ──────────────────────────────────────────────────────────────────────────────
# 2. HEALTHY BASELINES
#    maths_walkthrough.md §Setup — "Healthy features" X_R, X_I, X_B.
#    These are the exact values used throughout Parts A–C.
# ──────────────────────────────────────────────────────────────────────────────

# maths_walkthrough.md §Setup — X_R
# Router: cpu, mem, ospf_state (1=Full)
ROUTER_BASELINE = torch.tensor([
    [0.22, 0.30, 1.0],  # pe1 — used in §A.1 pe1 projection
    [0.20, 0.30, 1.0],  # p1  — used in §A.1 p1  projection
], dtype=torch.float32)

# maths_walkthrough.md §Setup — X_I
# Interface: tx_drops, rx_drops, mtu_norm (1500/9000 ≈ 0.167)
INTERFACE_BASELINE = torch.tensor([
    [0.01, 0.01, 0.167],  # pe1_eth1 — used in §A.2
    [0.01, 0.01, 0.167],  # p1_eth3  — used in §A.2
], dtype=torch.float32)

# maths_walkthrough.md §Setup — X_B
# BGP: state (1=Established), prefix count/1000, uptime/86400
BGP_BASELINE = torch.tensor([
    [1.0, 0.50, 0.80],  # pe1_bgp: UP, 500 prefixes, ~19h uptime — used in §A.3
], dtype=torch.float32)


def generate_normal_snapshots(n: int = 500, noise_std: float = 0.015, seed: int = 42) -> list:
    """
    Returns list of x_dict snapshots with healthy features + small noise.

    maths_walkthrough.md §D — "trained end-to-end on healthy data only".
    All 500 snapshots passed to train() are generated here; the model therefore
    only ever sees the healthy cluster during training, which is why the decoder
    cannot reconstruct out-of-distribution faulty embeddings correctly.
    """
    torch.manual_seed(seed)
    snapshots = []
    for _ in range(n):
        snapshots.append({
            'router':      torch.clamp(ROUTER_BASELINE    + torch.randn(2, 3) * noise_std, 0, 1),
            'interface':   torch.clamp(INTERFACE_BASELINE + torch.randn(2, 3) * noise_std, 0, 1),
            'bgp_session': torch.clamp(BGP_BASELINE       + torch.randn(1, 3) * noise_std, 0, 1),
        })
    return snapshots


def make_fault1_snapshot() -> dict:
    """
    Fault 1 — MTU mismatch on pe1_eth1 (Interface branch fault).
    TX drops spike. MTU deviates. Router and BGP stay healthy.
    Expected: Interface branch scores highest.

    maths_walkthrough.md §C.2 — "Fault 1: MTU mismatch on pe1_eth1"
    X_I_fault = [[0.75, 0.01, 0.156], [0.01, 0.01, 0.167]]
    """
    return {
        'router':      ROUTER_BASELINE.clone(),
        'interface':   torch.tensor([[0.75, 0.01, 0.156],   # pe1_eth1: fault
                                     [0.01, 0.01, 0.167]],  # p1_eth3: healthy
                                    dtype=torch.float32),
        'bgp_session': BGP_BASELINE.clone(),
    }


def make_fault2_snapshot() -> dict:
    """
    Fault 2 — pe1_bgp session teardown (BGP branch fault).
    Session flips DOWN, prefix count and uptime reset to zero.
    Router and Interface stay healthy.
    Expected: BGP branch scores highest.

    maths_walkthrough.md §C.4 — "Fault 2: BGP session teardown"
    X_B_fault = [[0.0, 0.0, 0.0]]
    """
    return {
        'router':      ROUTER_BASELINE.clone(),
        'interface':   INTERFACE_BASELINE.clone(),
        'bgp_session': torch.tensor([[0.0, 0.0, 0.0]], dtype=torch.float32),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. HetGNN AUTOENCODER
#    Direct simplified port of gnn/src/model/hetgnn.py
#
#    Identical patterns:
#      self.convs       nn.ModuleList of HeteroConv(SAGEConv, aggr='sum')
#      self.lin_dict    nn.ModuleDict: node_type → Linear(F_in, H)
#      self.decoder_dict nn.ModuleDict: node_type → Linear(H, F_in)
#      set_input_dims() called after instantiation (lazy init)
#      forward()        x_dict, edge_index_dict → (recon_dict, out_embeddings)
# ──────────────────────────────────────────────────────────────────────────────

class HetGNNAutoencoder(nn.Module):
    """
    Heterogeneous GNN Autoencoder — simplified gnn/src/model/hetgnn.py.

    Each node type has its own:
      - Input projection (lin_dict)         → maths_walkthrough.md §A
      - Message weights (SAGEConv per edge) → maths_walkthrough.md §B
      - Output decoder (decoder_dict)       → maths_walkthrough.md §C.1

    The branch with the highest reconstruction error names the fault layer.
    """

    def __init__(
        self,
        metadata: tuple,
        hidden_channels: int = 32,
        num_layers: int = 2,
    ):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers

        # ── Spatial graph convolutions ────────────────────────────────────────
        # maths_walkthrough.md §B — "HeteroConv(SAGEConv, aggr='sum')"
        #
        # One SAGEConv module is created per edge type per layer.
        # Each SAGEConv holds its own W_root and W_neigh (see §B intro
        # "SAGEConv differs from GCN").
        #
        # SAGEConv((-1, -1), H) — lazy init: input size inferred on first call.
        # In layer 0 the source nodes have F features; in later layers they have
        # H features (already projected).  The (-1, -1) signature tells PyG to
        # infer both src and dst dims at runtime.
        #
        # aggr='sum' — maths_walkthrough.md §B.3: preserves full message
        # magnitude; safer than 'mean' when the number of edge types per node
        # is fixed by the schema.
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            conv_dict = {}
            for edge_type in metadata[1]:
                if i == 0:
                    # Layer 0: src nodes still have raw F features
                    conv_dict[edge_type] = SAGEConv((-1, -1), hidden_channels)
                else:
                    # Layer 1+: src nodes already have H-dim embeddings
                    conv_dict[edge_type] = SAGEConv(hidden_channels, hidden_channels)
            # aggr='sum' — maths_walkthrough.md §B.3
            self.convs.append(HeteroConv(conv_dict, aggr='sum'))

        # ── Typed projections and decoders ────────────────────────────────────
        # Populated by set_input_dims() below.
        #
        # lin_dict     → maths_walkthrough.md §A (Part A)
        #   Each entry is nn.Linear(F, H), i.e. W_proj_nt [F→H].
        #   Mirrors "Why typed projections?" section: each type gets its own
        #   weights so semantically incompatible features are never mixed.
        #
        # decoder_dict → maths_walkthrough.md §C.1
        #   Each entry is nn.Linear(H, F), i.e. W_dec_nt [H→F].
        #   Separate decoder per type ensures branch errors are isolated.
        self.lin_dict     = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims: dict) -> None:
        """
        Initialise per-type projection and decoder weights.
        Called once after instantiation when feature dimensions are known.
        Identical to production set_input_dims().

        maths_walkthrough.md §A — W_proj_nt [F, H]:
            self.lin_dict['router']      = Linear(3, H)   # cpu,mem,ospf → latent
            self.lin_dict['interface']   = Linear(3, H)   # drops,mtu    → latent
            self.lin_dict['bgp_session'] = Linear(3, H)   # state,pfx,up → latent

        maths_walkthrough.md §C.1 — W_dec_nt [H, F]:
            self.decoder_dict['router']      = Linear(H, 3)
            self.decoder_dict['interface']   = Linear(H, 3)
            self.decoder_dict['bgp_session'] = Linear(H, 3)

        maths_walkthrough.md §E.1 — note that both matrices are shaped [F, H]
        or [H, F], NOT [N, ...].  Adding 1000 more routers does not change
        these shapes at all.
        """
        for node_type, dim in input_dims.items():
            # W_proj_nt [F→H] — maths_walkthrough.md §A
            self.lin_dict[node_type]     = nn.Linear(dim, self.hidden_channels)
            # W_dec_nt  [H→F] — maths_walkthrough.md §C.1
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(self, x_dict: dict, edge_index_dict: dict) -> tuple:
        """
        Forward pass — identical structure to gnn/src/model/hetgnn.py forward().

        Args:
            x_dict:          {node_type: Tensor[N, F]}
            edge_index_dict: {(src, rel, dst): Tensor[2, E]}

        Returns:
            recon_dict:    {node_type: Tensor[N, F]}  — reconstructed features
            out_embeddings:{node_type: Tensor[N, H]}  — latent embeddings

        Three-step pipeline — maps exactly to Parts A, B, C of the walkthrough:

            Step 1  lin_dict[nt](x).relu()      →  Part A: typed projections
            Step 2  HeteroConv(h_dict, edges)    →  Part B: message passing
            Step 3  decoder_dict[nt](h_dict[nt]) →  Part C: per-type decode
        """

        # ── Step 1: Typed Linear Projections ─────────────────────────────────
        # maths_walkthrough.md §A — "h_nt = ReLU(x_nt @ W_proj_nt)"
        #
        # Each node type is projected independently into the H-dim latent space.
        # After this loop every node lives in the same latent space regardless
        # of its original feature semantics (§A intro, "same output size H").
        #
        # Concrete example from the walkthrough:
        #   pe1   = [0.22, 0.30, 1.0]  →  h_R[pe1]     = [0.300, 0.464]  (§A.1)
        #   eth1  = [0.01, 0.01, 0.167] → h_I[eth1]    = [0.077, 0.124]  (§A.2)
        #   bgp   = [1.0,  0.50, 0.80]  → h_B[pe1_bgp] = [0.780, 0.870]  (§A.3)
        h_dict = {}
        for nt, x in x_dict.items():
            if x is not None and x.size(0) > 0:
                # lin_dict[nt] = nn.Linear(F, H) — applies W_proj_nt and bias,
                # then ReLU clips negatives to zero.
                h_dict[nt] = self.lin_dict[nt](x).relu()

        # ── Filter edges to only those with valid src and dst ─────────────────
        # Mirrors production's filtering logic; no walkthrough section needed.
        filtered = {
            et: ei
            for et, ei in edge_index_dict.items()
            if et[0] in h_dict and et[2] in h_dict and ei.size(1) > 0
        }

        # ── Step 2: Heterogeneous Message Passing ─────────────────────────────
        # maths_walkthrough.md §B — "HeteroConv(SAGEConv, aggr='sum')"
        #
        # HeteroConv iterates over every edge type and fires the corresponding
        # SAGEConv.  Each SAGEConv computes (§B intro — SAGEConv formula):
        #
        #   h_dst_new = ReLU( W_root @ h_dst  +  W_neigh @ mean(h_src) )
        #
        # The aggr='sum' at the HeteroConv level then sums contributions from
        # all edge types that write to the same destination type (§B.3).
        #
        # Concrete examples from the walkthrough:
        #
        #   §B.1  has_interface:  pe1→pe1_eth1
        #     self_part  (W_root_RI  @ h_I[eth1]) = [0.071, 0.095]
        #     neigh_part (W_neigh_RI @ h_R[pe1])  = [0.322, 0.213]
        #     msg_RI[eth1] = [0.393, 0.308]
        #
        #   §B.2  connects_to:   p1_eth3→pe1_eth1
        #     self_part  (W_root_II  @ h_I[eth1])   = [0.076, 0.090]
        #     neigh_part (W_neigh_II @ h_I[p1_eth3]) = [0.056, 0.070]
        #     msg_II[eth1] = [0.132, 0.160]
        #
        #   §B.3  HeteroConv sum (aggr='sum'):
        #     h_I_new[eth1] = ReLU([0.393,0.308] + [0.132,0.160]) = [0.525, 0.468]
        #
        #   §B.5  has_bgp:       pe1→pe1_bgp
        #     self_part  = [0.651, 0.678]
        #     neigh_part = [0.246, 0.136]
        #     h_B_new[pe1_bgp] = [0.897, 0.814]
        for conv in self.convs:
            h_updated = conv(h_dict, filtered)
            for nt in h_dict:
                if nt in h_updated:
                    h_dict[nt] = h_updated[nt].relu()
                # Nodes with no incoming edges (e.g. routers in this topology)
                # keep their previous representation — see §B.4.

        # ── Step 3: Per-type Decode ───────────────────────────────────────────
        # maths_walkthrough.md §C.1 — "recon_nt = h_nt @ W_dec_nt + b_nt"
        #
        # Each decoder is a separate nn.Linear(H, F) trained to invert the
        # encoder for its own node type only.  It never sees embeddings from
        # other types, so branch errors are isolated (§C.1 "Why a separate
        # decoder per branch?").
        #
        # Healthy baseline example from the walkthrough (§C.1):
        #   h_I[eth1] = [0.525, 0.468]
        #   recon[tx_drops] = 0.525×0.01 + 0.468×0.01 + 0.000 = 0.010  ✓
        #   recon[mtu_norm] = 0.525×0.10 + 0.468×0.20 + 0.010 = 0.157  ✓
        #   MSE_interface[pe1_eth1] = 0.000
        #
        # Faulty pe1_eth1 (§C.2):
        #   h_I_fault[eth1] = [1.204, 0.729]  (inflated by tx_drops spike)
        #   recon[tx_drops] = 0.019  (decoder expects ~0.010) → large error
        #   MSE_interface = 0.183  ← highest branch score → fault identified
        recon_dict     = {}
        out_embeddings = {}
        for nt in x_dict:
            if nt in h_dict:
                out_embeddings[nt] = h_dict[nt]
                # decoder_dict[nt] = nn.Linear(H, F) — W_dec_nt and bias
                recon_dict[nt]     = self.decoder_dict[nt](h_dict[nt])

        return recon_dict, out_embeddings


# ──────────────────────────────────────────────────────────────────────────────
# 4. TRAINING
#    maths_walkthrough.md §D — "Multi-task Training Loss"
#
#    Multi-task loss: L = (L_R + L_I + L_B) / 3
#    Each branch loss drives only its own W_proj and W_dec gradients.
#    The shared HeteroConv layers receive combined gradients from all three.
#
#    "Gradient isolation by branch" (§D): loss_bgp gradients flow through
#    W_proj_B and W_dec_B only — they do not touch W_proj_I or W_dec_I.
# ──────────────────────────────────────────────────────────────────────────────

def train(
    model: HetGNNAutoencoder,
    snapshots: list,
    edge_index_dict: dict,
    epochs: int = 100,
    lr: float = 1e-3,
) -> None:
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn   = nn.MSELoss()

    print(f"\n{'='*60}")
    print(f"  Training HetGNN Autoencoder — {epochs} epochs")
    print(f"  Branches: router | interface | bgp_session")
    print(f"  Snapshots: {len(snapshots)}   Hidden: {model.hidden_channels}   Layers: {model.num_layers}")
    print(f"{'='*60}")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0

        for x_dict in snapshots:
            optimiser.zero_grad()
            recon_dict, _ = model(x_dict, edge_index_dict)

            # Per-branch reconstruction losses
            # maths_walkthrough.md §D:
            #   loss_r updates: W_proj_R, HeteroConv (shared), W_dec_R
            #   loss_i updates: W_proj_I, HeteroConv (shared), W_dec_I
            #   loss_b updates: W_proj_B, HeteroConv (shared), W_dec_B
            #   W_proj_I / W_dec_I are never touched by loss_b gradients.
            loss_r = loss_fn(recon_dict['router'],      x_dict['router'])
            loss_i = loss_fn(recon_dict['interface'],   x_dict['interface'])
            loss_b = loss_fn(recon_dict['bgp_session'], x_dict['bgp_session'])

            # maths_walkthrough.md §D — "L = (L_R + L_I + L_B) / 3"
            # Equal branch weighting (α=β=γ=1/3).
            loss = (loss_r + loss_i + loss_b) / 3.0

            loss.backward()
            optimiser.step()
            total_loss += loss.item()

        if epoch % 20 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {total_loss / len(snapshots):.6f}")

    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANOMALY SCORING
#    maths_walkthrough.md §C.3 — "Branch scores"
#
#    node_score_i  = mean( (recon_i - x_i)² )   per node
#    branch_score  = max( node_scores_nt )        per type
#
#    Using max (not mean) ensures a single faulty node raises the branch score
#    even when surrounded by many healthy nodes of the same type.
#    See §C.3 "Why max over nodes rather than mean?"
# ──────────────────────────────────────────────────────────────────────────────

@torch.no_grad()
def compute_scores(
    model: HetGNNAutoencoder,
    x_dict: dict,
    edge_index_dict: dict,
) -> tuple:
    """
    Per-node and per-branch reconstruction MSE.
    Branch score = max per-node score within that type.
    The branch with the highest score names the fault layer.

    maths_walkthrough.md §C.3 — branch_score[nt] =
        max over nodes of: mean((recon[nt] - x[nt])², dim=features)

    Formula table in Summary:
        node score:   score_i = mean((recon_i - x_i)²)  →  .mean(dim=1)
        branch score: branch  = max(node_scores_nt)      →  .max()
    """
    model.eval()
    recon_dict, _ = model(x_dict, edge_index_dict)

    node_scores   = {}
    branch_scores = {}

    for nt in x_dict:
        if nt in recon_dict:
            # maths_walkthrough.md §C.3 node score formula:
            # score_i = mean((recon_i - x_i)²)  — averaged across F features
            err = ((recon_dict[nt] - x_dict[nt]) ** 2).mean(dim=1)  # [N]
            node_scores[nt] = err.numpy()

            # maths_walkthrough.md §C.3 branch score formula:
            # branch = max(node_scores_nt) — worst node drives the branch score
            branch_scores[nt] = float(err.max())

    return node_scores, branch_scores, {nt: recon_dict[nt].numpy() for nt in recon_dict}


def print_report(fault_name: str, node_scores: dict, branch_scores: dict, recon: dict, actual: dict) -> None:
    print(f"\n{'='*62}")
    print(f"  Fault: {fault_name}")
    print(f"{'='*62}")

    print(f"\n  Per-node scores:")
    for nt in NODE_TYPES:
        if nt in node_scores:
            for i, (name, score) in enumerate(zip(NODE_NAMES[nt], node_scores[nt])):
                print(f"    {nt:<12} {name:<12}  {score:.5f}")

    max_branch = max(branch_scores, key=branch_scores.get)
    print(f"\n  Branch-level diagnosis:")
    for nt in NODE_TYPES:
        if nt in branch_scores:
            marker = "  ⚠️  FAULT LAYER" if nt == max_branch else ""
            print(f"    {nt:<14}  {branch_scores[nt]:.5f}{marker}")

    print(f"\n  → Root layer: {max_branch}")

    # Feature breakdown for most anomalous node in fault branch
    top_node_idx = int(np.argmax(node_scores[max_branch]))
    top_node_name = NODE_NAMES[max_branch][top_node_idx]
    feat_names = FEATURE_NAMES[max_branch]
    act = actual[max_branch][top_node_idx]
    rec = recon[max_branch][top_node_idx]
    errs = (act - rec) ** 2
    feat_rank = np.argsort(errs)[::-1]

    print(f"\n  Feature breakdown for '{top_node_name}' ({max_branch}):")
    print(f"  {'Feature':<18} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*48}")
    for f in feat_rank:
        print(f"  {feat_names[f]:<18} {act[f]:>8.4f} {rec[f]:>9.4f} {errs[f]:>8.4f}")
    print(f"\n  Top driver: '{feat_names[feat_rank[0]]}'")


# ──────────────────────────────────────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*62)
    print("  HetGNN Failure Pinpointing — PyTorch / torch_geometric")
    print("  Mirrors: gnn/src/model/hetgnn.py")
    print("  Topology: pe1 + p1 routers, pe1_eth1/p1_eth3 interfaces, pe1_bgp")
    print("="*62)

    # Instantiate model and wire up input dims (production pattern).
    # hidden_channels=32 corresponds to H in the walkthrough (toy uses H=2).
    # maths_walkthrough.md §E.5 — production uses H=64; toy uses H=2.
    model = HetGNNAutoencoder(metadata=METADATA, hidden_channels=32, num_layers=2)

    # set_input_dims() creates W_proj [F→H] and W_dec [H→F] per type.
    # maths_walkthrough.md §A and §C.1.
    model.set_input_dims(INPUT_DIMS)

    # Train on healthy snapshots only — maths_walkthrough.md §D
    snapshots = generate_normal_snapshots(n=500)
    train(model, snapshots, EDGE_INDEX_DICT, epochs=100, lr=1e-3)

    # Sanity — healthy snapshot should give low scores on all branches.
    # Mirrors the "Healthy decode baseline" in maths_walkthrough.md §C.1.
    print("[Sanity] Healthy snapshot (all branches should score low):")
    x_h = generate_normal_snapshots(n=1, seed=99)[0]
    _, br_h, _ = compute_scores(model, x_h, EDGE_INDEX_DICT)
    for nt, s in br_h.items():
        print(f"  {nt:<14}  {s:.6f}")

    # Fault 1 — Interface branch
    # maths_walkthrough.md §C.2 — MSE_interface = 0.183; other branches ≈ 0.000
    print("\n[Fault 1] MTU mismatch on pe1_eth1:")
    x_f1 = make_fault1_snapshot()
    ns1, br1, rec1 = compute_scores(model, x_f1, EDGE_INDEX_DICT)
    print_report(
        "MTU mismatch on pe1_eth1 (Fault 1)",
        ns1, br1,
        {nt: rec1[nt] for nt in rec1},
        {nt: x_f1[nt].numpy() for nt in x_f1},
    )

    # Fault 2 — BGP branch
    # maths_walkthrough.md §C.4 — MSE_bgp = 0.036; other branches ≈ 0.000
    print("\n[Fault 2] pe1_bgp session teardown:")
    x_f2 = make_fault2_snapshot()
    ns2, br2, rec2 = compute_scores(model, x_f2, EDGE_INDEX_DICT)
    print_report(
        "pe1_bgp session teardown (Fault 2)",
        ns2, br2,
        {nt: rec2[nt] for nt in rec2},
        {nt: x_f2[nt].numpy() for nt in x_f2},
    )

    # Summary
    print("="*62)
    print("  Summary — HetGNN branch diagnosis")
    print("="*62)
    print(f"  Fault 1 (MTU):     interface {br1.get('interface',0):.4f}  bgp {br1.get('bgp_session',0):.4f}")
    print(f"  Fault 2 (BGP):     interface {br2.get('interface',0):.4f}  bgp {br2.get('bgp_session',0):.4f}")
    print()
    print("  Production wiring:")
    print("  • Replace generate_normal_snapshots() with Spanner NetworkMetrics query")
    print("  • Replace EDGE_INDEX_DICT with query to PhysicalLink / BGPSession nodes")
    print("  • Write branch anomaly scores back to NodeEmbedding table in Spanner")
    print()


if __name__ == "__main__":
    torch.manual_seed(42)
    main()
