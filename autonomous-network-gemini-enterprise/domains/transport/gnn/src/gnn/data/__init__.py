"""gnn.data — snapshot sources.

``SpannerDataset`` reconstructs the network graph as of an arbitrary timestamp
from the SCD Type 2 tables (``valid_start_ts`` / ``valid_end_ts``). DGF's
``read_spanner_graph`` cannot do this — it reads whole base tables with no
timestamp predicate — so this reader stays regardless of which backend trains
the model.
"""

from gnn.data.spanner import SpannerDataset

__all__ = ["SpannerDataset"]
