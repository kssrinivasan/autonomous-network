"""gnn_utils.py — DEPRECATED compatibility shim.

This module was split during the multi-architecture refactor:

  schema constants, feature rows  →  ``gnn.core.schema``
  snapshot → numpy arrays         →  ``gnn.core.snapshot``
  scaler fitting / applying       →  ``gnn.core.normalize.Normalizer``
  snapshot → HeteroData           →  ``gnn.backends.pyg.graph.GraphBuilder``

It re-exports the same names so the compiled KFP components (which are baked
into ``gnn_pipeline.yaml`` and import ``utils.gnn_utils`` from ``/app``),
existing notebooks and the local monitor keep working unchanged. Import from
``gnn.core`` in new code — this shim goes away once the pipeline YAML is
recompiled against the new paths.

Importing this module pulls in torch and torch_geometric via ``GraphBuilder``.
``gnn.core`` does not.
"""

from typing import Dict, List

from sklearn.preprocessing import StandardScaler

from gnn.backends.pyg.graph import GraphBuilder  # noqa: F401  (re-export)
from gnn.core.normalize import Normalizer
from gnn.core.schema import (  # noqa: F401  (re-exports)
    ANOMALY_THRESHOLDS,
    CONTINUOUS_FEATURES,
    EDGE_TYPES,
    FEATURE_DIMS,
    FEATURE_NAMES,
    FLOW_FEATURES,
    INTERFACE_FEATURES,
    NODE_TYPES,
    NUM_CONTINUOUS,
    ROLE_ORDER,
    ROUTER_FEATURES,
    VPN_FEATURES,
)
from gnn.core.schema import role_onehot as _role_onehot  # noqa: F401


def fit_scalers(snapshots: List[Dict]) -> Dict[str, StandardScaler]:
    """Deprecated alias for ``gnn.core.normalize.Normalizer.fit(...).scalers``."""
    return Normalizer.fit(snapshots).scalers


__all__ = [
    "GraphBuilder", "fit_scalers",
    "NODE_TYPES", "EDGE_TYPES", "FEATURE_DIMS", "FEATURE_NAMES",
    "ROUTER_FEATURES", "INTERFACE_FEATURES", "VPN_FEATURES", "FLOW_FEATURES",
    "CONTINUOUS_FEATURES", "NUM_CONTINUOUS",
    "ANOMALY_THRESHOLDS", "ROLE_ORDER",
]
