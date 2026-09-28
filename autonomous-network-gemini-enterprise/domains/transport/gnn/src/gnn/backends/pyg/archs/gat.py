"""gat.py — Graph Attention convolutions.

``GATConv`` learns per-edge attention weights instead of averaging neighbours
uniformly. The bet for this network is that not all neighbours are equally
informative — a PE router's state depends far more on its CE-facing interface
than on one of a dozen core links — and attention can express that where
GraphSAGE's mean cannot.

Two settings are forced rather than exposed:

* ``add_self_loops=False`` — mandatory. Most edge types here are bipartite
  (router↔interface, flow↔vpn); adding a self-loop from a router index to an
  interface index is meaningless and PyG raises on it.
* ``concat=False`` — heads are averaged, not concatenated, so the output stays
  ``hidden_channels`` wide. The model is a symmetric autoencoder and the
  decoders are ``Linear(hidden, input_dim)``; concatenation would multiply the
  latent width by ``heads`` and make the SAGE/GAT comparison a comparison of
  two different capacities as much as two different mechanisms.
"""

from torch_geometric.nn import GATConv

from gnn.backends.pyg.archs.base import ArchSpec


def make_conv(edge_type, hidden_channels: int, heads: int = 4,
              attention_dropout: float = 0.0, **kwargs):
    return GATConv(
        (hidden_channels, hidden_channels),   # bipartite-safe input spec
        hidden_channels,
        heads=heads,
        concat=False,
        dropout=attention_dropout,
        add_self_loops=False,
    )


SPEC = ArchSpec(
    name="gat",
    conv_factory=make_conv,
    aggr="mean",
    defaults={"heads": 4, "attention_dropout": 0.0},
)
