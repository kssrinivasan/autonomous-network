"""utils/data.py — DEPRECATED compatibility shim.

``SpannerDataset`` moved to ``gnn.data.spanner`` during the multi-architecture
refactor. Re-exported here because the compiled KFP components in
``gnn_pipeline.yaml`` import ``utils.data`` from ``/app``, as do several
notebooks and test scripts. Import from ``gnn.data`` in new code.
"""

from gnn.data.spanner import (  # noqa: F401  (re-exports)
    SpannerDataset,
    _parse_speed_bps,
    _render_spanner_query,
)

__all__ = ["SpannerDataset"]
