"""model.py — the heterogeneous autoencoder, shared across PyG architectures.

Structure (identical for every architecture; only the convolution differs):

    x_t ──Linear(F_t → H)──ReLU──Dropout──┐
                                          ├── L × HeteroConv ──ReLU──Dropout ──> z_t
                                          ┘
    z_t ──Linear(H → F_t)──> reconstruction of x_t

Type-specific decoders are the point: one ``Linear(H, F_t)`` per node type
means reconstruction error stays attributable to a layer of the network
(router / interface / vpn / flow) instead of being pooled into a single
number, which is what lets the training loop weight the four objectives
separately and lets an operator see *which* layer is misbehaving.
"""

from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
from torch_geometric.nn import HeteroConv

from gnn.backends.pyg.archs import get_spec

EdgeType = Tuple[str, str, str]


class HetGNN(nn.Module):
    """Heterogeneous graph autoencoder with per-node-type encoders and decoders.

    Args:
        metadata: ``(node_types, edge_types)`` — PyG's usual metadata tuple.
        hidden_channels: latent width H.
        out_channels: accepted for checkpoint compatibility; the autoencoder is
            symmetric so the latent width is always ``hidden_channels``.
        num_layers: number of message-passing layers.
        dropout: applied after the input projection and after each conv layer.
        arch: architecture name — see ``gnn.backends.pyg.archs.SPECS``.
        arch_kwargs: architecture-specific options (e.g. ``heads`` for GAT).
    """

    def __init__(
        self,
        metadata,
        hidden_channels: int = 64,
        out_channels: int = 64,
        num_layers: int = 2,
        dropout: float = 0.3,
        arch: str = "sage",
        arch_kwargs: Optional[Dict] = None,
    ):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.out_channels = out_channels
        self.num_layers = num_layers
        self.arch = arch
        self.dropout = nn.Dropout(p=dropout)

        spec = get_spec(arch)
        self.arch_kwargs = spec.kwargs(arch_kwargs)

        edge_types: List[EdgeType] = list(metadata[1])
        self.convs = nn.ModuleList()
        for _ in range(num_layers):
            conv_dict = {
                edge_type: spec.conv_factory(edge_type, hidden_channels, **self.arch_kwargs)
                for edge_type in edge_types
            }
            self.convs.append(HeteroConv(conv_dict, aggr=spec.aggr))

        # Populated by set_input_dims() once the per-type feature widths are known.
        self.lin_dict = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims: Dict[str, int]) -> None:
        """Create the input projections and decoders for the given feature widths."""
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type] = nn.Linear(dim, self.hidden_channels)
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(self, x_dict, edge_index_dict):
        """Run one snapshot through the autoencoder.

        Returns ``(reconstructions, embeddings)``, both keyed by node type, so
        the training loop can apply per-type loss weights and the serving path
        can score and explain in the same pass.
        """
        # Schema-specific projection into the shared latent space.
        h_dict = {}
        for ntype, x in x_dict.items():
            if x is not None and x.size(0) > 0:
                h_dict[ntype] = self.dropout(self.lin_dict[ntype](x).relu())

        # Drop edges whose endpoints have no features in this snapshot —
        # a node type can legitimately be empty (no flows running).
        filtered_edge_index_dict = {
            edge_type: edge_index
            for edge_type, edge_index in edge_index_dict.items()
            if edge_type[0] in h_dict and edge_type[2] in h_dict and edge_index.size(1) > 0
        }

        for conv in self.convs:
            h_dict_updated = conv(h_dict, filtered_edge_index_dict)
            for ntype in h_dict:
                if ntype in h_dict_updated:
                    h_dict[ntype] = self.dropout(h_dict_updated[ntype].relu())
                # Nodes with no incoming edges this layer keep their previous
                # representation rather than being zeroed — an isolated router
                # should still be reconstructable from its own features.

        recon_dict, embeddings = {}, {}
        for ntype in x_dict:
            if ntype in h_dict:
                embeddings[ntype] = h_dict[ntype]
                recon_dict[ntype] = self.decoder_dict[ntype](h_dict[ntype])
        return recon_dict, embeddings


def contrastive_diversity_loss(embeddings: Dict[str, torch.Tensor]) -> torch.Tensor:
    """Penalise within-type embedding similarity to prevent representation collapse.

    Without this an autoencoder with per-type decoders can map every node of a
    type to nearly the same point and still reconstruct acceptably — which
    destroys the Mahalanobis scoring downstream, because a collapsed cluster
    has near-zero covariance and every live node looks infinitely anomalous.

    Returns the mean off-diagonal cosine similarity, averaged over node types
    with more than one node. Higher = more collapsed = worse.
    """
    total = 0.0
    count = 0
    for emb in embeddings.values():
        n = emb.size(0)
        if n <= 1:
            continue
        normalized = torch.nn.functional.normalize(emb, p=2, dim=1)
        similarity = normalized @ normalized.T
        off_diagonal = (similarity * (1 - torch.eye(n, device=emb.device))).sum()
        total += off_diagonal / (n * (n - 1))
        count += 1
    return total / count if count > 0 else torch.tensor(0.0)
