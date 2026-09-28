"""sage.py — GraphSAGE convolutions (the incumbent architecture).

``SAGEConv`` computes ``W₁·x_dst + W₂·mean(x_src)``: cheap, degree-normalised,
and no attention parameters. This is what the deployed HetGNN has always used;
it is the baseline every other variant is measured against.
"""

from torch_geometric.nn import SAGEConv

from gnn.backends.pyg.archs.base import ArchSpec


def make_conv(edge_type, hidden_channels: int, **kwargs):
    return SAGEConv(hidden_channels, hidden_channels)


SPEC = ArchSpec(
    name="sage",
    conv_factory=make_conv,
    # 'mean' across relations, so a node type reachable by many relations
    # (interface: has_interface, connected_to, source_of, destination_of) isn't
    # scaled up relative to one reachable by few.
    aggr="mean",
    defaults={},
)
