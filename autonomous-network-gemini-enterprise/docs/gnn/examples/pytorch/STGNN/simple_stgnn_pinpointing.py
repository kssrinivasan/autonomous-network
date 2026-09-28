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
Simple STGNN Failure Pinpointing — PyTorch / PyG
=================================================
Spatio-Temporal GNN autoencoder that catches transient faults
(BGP flaps, CRC spikes) invisible to static snapshot models.

    pip install torch torch-geometric
    python simple_stgnn_pinpointing.py

Topology (same as HetGNN example):
    [Router: pe1] ──has_bgp──▶ [BGPSession: pe1_bgp]
    [Router: pe1] ──has_interface──▶ [Interface: pe1_eth1]
    [Router: p1 ] ──has_interface──▶ [Interface: p1_eth3 ]

Fault — BGP flap:
  t=0 healthy → t=1 BGP DOWN → t=2 recovered
  At t=2 the snapshot looks normal. A static model gives 0 anomaly score.
  The STGNN's GRU remembers t=1 and flags it anyway.

Production code reference: gnn/src/model/stgnn.py
  - Same HeteroConv + SAGEConv spatial layers
  - Same per-node-type nn.GRU (self.rnns ModuleDict)
  - Same x_dict_seq shape: {node_type: Tensor[N, T, F]}
  - Same forward() signature: x_dict_seq, edge_index_dict, hidden_state_dict
                           → (recon_dict, out_embeddings, new_hidden_states)
  - Same set_input_dims() lazy init pattern
  - Same final-step reconstruction loss
"""

import torch
import torch.nn as nn
import numpy as np
from torch_geometric.nn import HeteroConv, SAGEConv

# ──────────────────────────────────────────────────────────────────────────────
# 1. TOPOLOGY METADATA  (identical format to hetgnn.py example)
# ──────────────────────────────────────────────────────────────────────────────

NODE_TYPES = ['router', 'interface', 'bgp_session']

EDGE_TYPES = [
    ('router',    'has_interface', 'interface'),
    ('interface', 'connects_to',   'interface'),
    ('router',    'has_bgp',       'bgp_session'),
]

METADATA = (NODE_TYPES, EDGE_TYPES)

# Static topology — same at every time step
EDGE_INDEX_DICT = {
    ('router', 'has_interface', 'interface'):  torch.tensor([[0, 1], [0, 1]], dtype=torch.long),
    ('interface', 'connects_to', 'interface'): torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
    ('router', 'has_bgp', 'bgp_session'):      torch.tensor([[0], [0]],       dtype=torch.long),
}

FEATURE_NAMES = {
    'router':      ['cpu_percent', 'mem_percent', 'ospf_state'],
    'interface':   ['tx_drops_rate', 'rx_drops_rate', 'mtu_norm'],
    'bgp_session': ['bgp_state', 'pfx_count_norm', 'uptime_norm'],
}

NODE_NAMES = {
    'router':      ['pe1', 'p1'],
    'interface':   ['pe1_eth1', 'p1_eth3'],
    'bgp_session': ['pe1_bgp'],
}

INPUT_DIMS = {nt: len(FEATURE_NAMES[nt]) for nt in NODE_TYPES}  # 3 features each

T = 3  # time steps per sequence window

# ──────────────────────────────────────────────────────────────────────────────
# 2. HEALTHY BASELINES
# ──────────────────────────────────────────────────────────────────────────────

ROUTER_BASELINE    = torch.tensor([[0.22, 0.30, 1.0], [0.20, 0.30, 1.0]], dtype=torch.float32)
INTERFACE_BASELINE = torch.tensor([[0.01, 0.01, 0.167], [0.01, 0.01, 0.167]], dtype=torch.float32)
BGP_BASELINE       = torch.tensor([[1.0, 0.50, 0.80]], dtype=torch.float32)


def _healthy_seq(base: torch.Tensor, noise_std: float) -> torch.Tensor:
    """Repeat baseline across T time steps + noise → [N, T, F]."""
    seq = base.unsqueeze(1).expand(-1, T, -1).clone()
    return torch.clamp(seq + torch.randn_like(seq) * noise_std, 0.0, 1.0)


def generate_normal_sequences(n: int = 300, noise_std: float = 0.01, seed: int = 42) -> list:
    """
    Training data: each sample is a dict of [N, T, F] tensors — all time steps healthy.
    Mirrors the STGNN production training input format: x_dict_seq.
    """
    torch.manual_seed(seed)
    sequences = []
    for _ in range(n):
        sequences.append({
            'router':      _healthy_seq(ROUTER_BASELINE,    noise_std),
            'interface':   _healthy_seq(INTERFACE_BASELINE, noise_std),
            'bgp_session': _healthy_seq(BGP_BASELINE,       noise_std),
        })
    return sequences


def make_bgp_flap_sequence() -> dict:
    """
    Fault — BGP session flap.

    t=0: healthy (bgp=1.0, pfx=0.50, upt=0.80)
    t=1: DOWN    (bgp=0.0, pfx=0.0,  upt=0.0)
    t=2: recovered (bgp=1.0, pfx=0.50, upt=0.80)

    At t=2 the raw features look identical to healthy.
    A static model at t=2 sees no anomaly.
    The STGNN's GRU carries memory of t=1 through to t=2's final embedding,
    producing high reconstruction error even after the session recovers.

    Returns x_dict_seq: {node_type: Tensor[N, T, F]}
    """
    bgp_seq = torch.stack([
        BGP_BASELINE.squeeze(0),                              # t=0 healthy
        torch.tensor([0.0, 0.0, 0.0]),                        # t=1 DOWN
        BGP_BASELINE.squeeze(0),                              # t=2 recovered
    ], dim=0).unsqueeze(0)  # [1, T, F] = [N_B=1, 3, 3]

    return {
        'router':      ROUTER_BASELINE.unsqueeze(1).expand(-1, T, -1),      # unchanged
        'interface':   INTERFACE_BASELINE.unsqueeze(1).expand(-1, T, -1),   # unchanged
        'bgp_session': bgp_seq,
    }


def make_mtu_fault_sequence() -> dict:
    """
    Fault — MTU mismatch appearing at all 3 time steps (persistent fault).

    Unlike the BGP flap, this is a sustained fault visible at every snapshot.
    Both the static HetGNN (on any single snapshot) and the STGNN catch it.
    The STGNN additionally detects its trajectory (sustained vs transient).

    Returns x_dict_seq: {node_type: Tensor[N, T, F]}
    """
    iface_fault = torch.tensor([[0.75, 0.01, 0.156],   # pe1_eth1: tx_drops spike, mtu=1400
                                 [0.01, 0.01, 0.167]])  # p1_eth3: healthy
    iface_seq = iface_fault.unsqueeze(1).expand(-1, T, -1)  # [N_I, T, F]

    return {
        'router':      ROUTER_BASELINE.unsqueeze(1).expand(-1, T, -1),
        'interface':   iface_seq,
        'bgp_session': BGP_BASELINE.unsqueeze(1).expand(-1, T, -1),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. STGNN AUTOENCODER
#    Direct simplified port of gnn/src/model/stgnn.py
#
#    Identical patterns:
#      self.convs    nn.ModuleList of HeteroConv(SAGEConv, aggr='mean')
#      self.rnns     nn.ModuleDict: node_type → nn.GRU
#      self.lin_dict nn.ModuleDict: node_type → Linear(F_in, H)
#      self.decoder_dict nn.ModuleDict: node_type → Linear(H, F_in)
#      forward()     x_dict_seq, edge_index_dict, hidden_state_dict
#                    → (recon_dict, out_embeddings, new_hidden_states)
# ──────────────────────────────────────────────────────────────────────────────

class STGNNAutoencoder(nn.Module):
    """
    Spatio-Temporal GNN Autoencoder — simplified gnn/src/model/stgnn.py.

    Architecture:
      For each time step t:
        1. Typed projection: x_t[nt] → lin_dict[nt] → h_t[nt]
        2. Spatial message passing: HeteroConv(SAGEConv) over h_t
        3. Collect spatial embedding v_t[nt]
      After all T steps:
        4. Temporal: stack v_t sequences → rnns[nt](sequence) → final state
        5. Decode: decoder_dict[nt](final_state) → recon[nt]
        6. Anomaly score: MSE(recon[nt], x_T[nt])   (last time step)
    """

    def __init__(
        self,
        metadata: tuple,
        hidden_channels: int = 32,
        num_layers: int = 2,
        rnn_type: str = 'gru',
    ):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers

        # ── Spatial layers — same as production ───────────────────────────────
        # aggr='mean' matches production stgnn.py (vs 'sum' in hetgnn.py)
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            conv_dict = {}
            for edge_type in metadata[1]:
                if i == 0:
                    conv_dict[edge_type] = SAGEConv((-1, -1), hidden_channels)
                else:
                    conv_dict[edge_type] = SAGEConv(hidden_channels, hidden_channels)
            self.convs.append(HeteroConv(conv_dict, aggr='mean'))

        # ── Per-type temporal RNNs — identical to production ──────────────────
        # ModuleDict: one GRU per node type, batch_first=True so input is [N, T, H]
        self.rnns = nn.ModuleDict()
        for node_type in metadata[0]:
            if rnn_type.lower() == 'gru':
                self.rnns[node_type] = nn.GRU(
                    input_size=hidden_channels,
                    hidden_size=hidden_channels,
                    num_layers=1,
                    batch_first=True,   # input: [N, T, H], hidden: [1, N, H]
                )
            elif rnn_type.lower() == 'lstm':
                self.rnns[node_type] = nn.LSTM(
                    input_size=hidden_channels,
                    hidden_size=hidden_channels,
                    num_layers=1,
                    batch_first=True,
                )

        # ── Typed projections and decoders ────────────────────────────────────
        self.lin_dict     = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims: dict) -> None:
        """Identical to production set_input_dims()."""
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type]     = nn.Linear(dim, self.hidden_channels)
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(
        self,
        x_dict_seq: dict,
        edge_index_dict: dict,
        hidden_state_dict: dict = None,
    ) -> tuple:
        """
        Forward pass — identical structure to gnn/src/model/stgnn.py forward().

        Args:
            x_dict_seq:       {node_type: Tensor[N, T, F]}  — time-series features
            edge_index_dict:  {(src, rel, dst): Tensor[2, E]} — static topology
            hidden_state_dict:{node_type: Tensor[1, N, H]}  — optional warm-start GRU state

        Returns:
            recon_dict:        {node_type: Tensor[N, F]}     — reconstructed final step
            out_embeddings:    {node_type: Tensor[N, H]}     — final temporal embedding
            new_hidden_states: {node_type: Tensor[1, N, H]}  — updated GRU states
        """
        # Collect spatial embeddings for each time step
        histories = {nt: [] for nt in x_dict_seq}
        T_local = next(iter(x_dict_seq.values())).size(1)

        # ── Step 1: Spatial message passing at each time step ─────────────────
        for t in range(T_local):
            # Extract snapshot at time t: {node_type: Tensor[N, F]}
            x_t = {nt: x[:, t, :] for nt, x in x_dict_seq.items() if x.dim() == 3}

            # Typed projection
            h_t = {}
            for nt, feat in x_t.items():
                if feat is not None and feat.size(0) > 0:
                    h_t[nt] = self.lin_dict[nt](feat).relu()

            # Filter valid edges
            filtered = {
                et: ei
                for et, ei in edge_index_dict.items()
                if et[0] in h_t and et[2] in h_t and ei.size(1) > 0
            }

            # Spatial convolutions
            for conv in self.convs:
                h_updated = conv(h_t, filtered)
                for nt in h_t:
                    if nt in h_updated:
                        h_t[nt] = h_updated[nt].relu()

            # Record spatial embedding at this time step
            for nt in x_t:
                if nt in h_t:
                    histories[nt].append(h_t[nt])

        # ── Step 2: Temporal processing (GRU) ────────────────────────────────
        recon_dict        = {}
        out_embeddings    = {}
        new_hidden_states = {}

        for nt, steps in histories.items():
            # Stack: [N, T, H]
            h_seq = torch.stack(steps, dim=1)

            # Warm-start from previous hidden state if supplied (streaming inference)
            h_state = hidden_state_dict[nt] if hidden_state_dict and nt in hidden_state_dict else None

            # rnn_out: [N, T, H],  h_n: [1, N, H]
            rnn_out, h_n = self.rnns[nt](h_seq, h_state)

            # Use final time step's output as the "trajectory summary" embedding
            final_emb = rnn_out[:, -1, :]   # [N, H]

            out_embeddings[nt]    = final_emb
            new_hidden_states[nt] = h_n
            recon_dict[nt]        = self.decoder_dict[nt](final_emb)

        return recon_dict, out_embeddings, new_hidden_states


# ──────────────────────────────────────────────────────────────────────────────
# 4. TRAINING
#    Loss = MSE(recon[nt], x_dict_seq[nt][:, -1, :]) per branch
#    Trains the model to predict the final snapshot from a healthy sequence.
# ──────────────────────────────────────────────────────────────────────────────

def train(
    model: STGNNAutoencoder,
    sequences: list,
    edge_index_dict: dict,
    epochs: int = 100,
    lr: float = 1e-3,
) -> None:
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn   = nn.MSELoss()

    print(f"\n{'='*62}")
    print(f"  Training STGNN Autoencoder — {epochs} epochs")
    print(f"  Branches: router | interface | bgp_session  (T={T} time steps)")
    print(f"  Sequences: {len(sequences)}   Hidden: {model.hidden_channels}   Layers: {model.num_layers}")
    print(f"{'='*62}")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0

        for x_seq in sequences:
            optimiser.zero_grad()
            recon_dict, _, _ = model(x_seq, edge_index_dict)

            # Compare reconstruction against the actual FINAL time step features.
            # This is the next-step prediction objective from the research doc:
            # "predict what the network looks like at the end of the window"
            loss_r = loss_fn(recon_dict['router'],      x_seq['router'][:,      -1, :])
            loss_i = loss_fn(recon_dict['interface'],   x_seq['interface'][:,   -1, :])
            loss_b = loss_fn(recon_dict['bgp_session'], x_seq['bgp_session'][:, -1, :])
            loss   = (loss_r + loss_i + loss_b) / 3.0

            loss.backward()
            optimiser.step()
            total_loss += loss.item()

        if epoch % 20 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {total_loss / len(sequences):.6f}")

    print(f"{'='*62}\n")


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANOMALY SCORING
# ──────────────────────────────────────────────────────────────────────────────

@torch.no_grad()
def compute_scores(
    model: STGNNAutoencoder,
    x_dict_seq: dict,
    edge_index_dict: dict,
) -> tuple:
    """
    Score a full T-step sequence.
    Compares reconstruction against the ACTUAL final-step features.
    A high score on bgp_session when t_final looks healthy means the GRU
    remembered a fault from an earlier step in the window.
    """
    model.eval()
    recon_dict, embeddings, _ = model(x_dict_seq, edge_index_dict)

    node_scores   = {}
    branch_scores = {}

    for nt in x_dict_seq:
        if nt in recon_dict:
            actual_final = x_dict_seq[nt][:, -1, :]  # [N, F] — final snapshot
            err = ((recon_dict[nt] - actual_final) ** 2).mean(dim=1)  # [N]
            node_scores[nt]   = err.numpy()
            branch_scores[nt] = float(err.max())

    return node_scores, branch_scores, {nt: recon_dict[nt].numpy() for nt in recon_dict}


def print_report(
    fault_name: str,
    node_scores: dict,
    branch_scores: dict,
) -> None:
    print(f"\n{'='*62}")
    print(f"  Fault: {fault_name}")
    print(f"{'='*62}")

    print(f"\n  Per-node scores:")
    for nt in NODE_TYPES:
        if nt in node_scores:
            for name, score in zip(NODE_NAMES[nt], node_scores[nt]):
                print(f"    {nt:<12} {name:<12}  {score:.5f}")

    max_branch = max(branch_scores, key=branch_scores.get)
    print(f"\n  Branch-level diagnosis:")
    for nt in NODE_TYPES:
        if nt in branch_scores:
            marker = "  ⚠️  FAULT LAYER" if nt == max_branch else ""
            print(f"    {nt:<14}  {branch_scores[nt]:.5f}{marker}")

    print(f"\n  → Root layer: {max_branch}")


# ──────────────────────────────────────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*62)
    print("  STGNN Failure Pinpointing — PyTorch / torch_geometric")
    print("  Mirrors: gnn/src/model/stgnn.py")
    print("  Catches transient faults invisible to static models")
    print("="*62)

    # Instantiate (production pattern: model first, then set_input_dims)
    model = STGNNAutoencoder(metadata=METADATA, hidden_channels=32, num_layers=2, rnn_type='gru')
    model.set_input_dims(INPUT_DIMS)

    # Train on healthy sequences
    seqs = generate_normal_sequences(n=300)
    train(model, seqs, EDGE_INDEX_DICT, epochs=100, lr=1e-3)

    # Sanity — healthy sequence should score near zero
    print("[Sanity] Healthy sequence (all branches should score low):")
    x_h = generate_normal_sequences(n=1, seed=99)[0]
    _, br_h, _ = compute_scores(model, x_h, EDGE_INDEX_DICT)
    for nt, s in br_h.items():
        print(f"  {nt:<14}  {s:.6f}")

    # ── Fault 1: BGP flap (t=1 DOWN, t=2 recovered) ───────────────────────
    print("\n[Fault 1] BGP flap — t=1 DOWN, t=2 recovered:")
    print("  Note: at t=2 the features look identical to healthy.")
    print("  A static HetGNN on t=2 alone would score ~0. STGNN catches the flap.")
    x_flap = make_bgp_flap_sequence()
    ns1, br1, _ = compute_scores(model, x_flap, EDGE_INDEX_DICT)
    print_report("BGP flap (Fault 1)", ns1, br1)

    # ── Fault 2: Persistent MTU mismatch ──────────────────────────────────
    print("\n[Fault 2] Persistent MTU mismatch on pe1_eth1 (all 3 time steps):")
    x_mtu = make_mtu_fault_sequence()
    ns2, br2, _ = compute_scores(model, x_mtu, EDGE_INDEX_DICT)
    print_report("Persistent MTU mismatch (Fault 2)", ns2, br2)

    # Summary
    print("="*62)
    print("  Summary — STGNN vs static model")
    print("="*62)
    print(f"  Fault 1 (BGP flap):")
    print(f"    bgp_session branch: {br1.get('bgp_session',0):.4f}  ← STGNN catches transient flap")
    print(f"    interface branch:   {br1.get('interface',0):.4f}  ← correctly low")
    print(f"  Fault 2 (MTU — persistent):")
    print(f"    interface branch:   {br2.get('interface',0):.4f}  ← high for sustained fault")
    print(f"    bgp_session branch: {br2.get('bgp_session',0):.4f}  ← correctly low")
    print()
    print("  Production wiring:")
    print("  • Replace sequences with T=60-step windows from NetworkMetrics (5-sec intervals)")
    print("  • Pass hidden_state_dict between inference calls for stateful streaming")
    print("  • Use 'fast' window (5 min) for microbursts, 'slow' (24h) for hardware degradation")
    print()


if __name__ == "__main__":
    torch.manual_seed(42)
    main()
