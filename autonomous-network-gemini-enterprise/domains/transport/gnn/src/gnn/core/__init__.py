"""gnn.core — framework-free graph, normalization and scoring logic.

Nothing in this package may import torch, jax, flax or torch_geometric. A test
asserts that; the point is that swapping the model backend cannot silently
change how features are built, how they are scaled, or how anomalies are
scored and explained.
"""

from gnn.core import normalize, quality, schema, scoring, snapshot, split
from gnn.core.normalize import Normalizer
from gnn.core.schema import (
    ANOMALY_THRESHOLDS,
    EDGE_TYPES,
    FEATURE_DIMS,
    FEATURE_NAMES,
    FLOW_FEATURES,
    INTERFACE_FEATURES,
    NODE_TYPES,
    ROUTER_FEATURES,
    VPN_FEATURES,
)
from gnn.core.scoring import ClusterStats, TypeScores
from gnn.core.snapshot import SnapshotArrays, build_arrays

__all__ = [
    "normalize", "quality", "schema", "scoring", "snapshot", "split",
    "Normalizer", "ClusterStats", "TypeScores", "SnapshotArrays", "build_arrays",
    "NODE_TYPES", "EDGE_TYPES", "FEATURE_DIMS", "FEATURE_NAMES",
    "ROUTER_FEATURES", "INTERFACE_FEATURES", "VPN_FEATURES", "FLOW_FEATURES",
    "ANOMALY_THRESHOLDS",
]
