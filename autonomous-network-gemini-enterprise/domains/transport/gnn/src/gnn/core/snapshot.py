"""snapshot.py — snapshot dict → numpy arrays.

The framework-free half of what used to be ``GraphBuilder.process_snapshot``.
Produces plain numpy feature matrices, adjacency arrays and node-id lists;
each backend wraps those in its own graph container (``HeteroData`` for PyG,
``InMemoryGraph`` for DGF) without re-deriving any of the schema logic.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from gnn.core import schema
from gnn.core.schema import EDGE_TYPES, FEATURE_DIMS, NODE_TYPES

EdgeType = Tuple[str, str, str]


@dataclass
class SnapshotArrays:
    """One network snapshot as numpy arrays.

    Attributes:
        features:  node type → ``[num_nodes, num_features]`` float32 matrix.
                   Every declared node type is present; absent types get a
                   ``[0, F]`` matrix so downstream shapes stay predictable.
        node_ids:  node type → list of Spanner node ids, positionally aligned
                   with the rows of ``features``.
        edges:     edge type triple → ``[2, num_edges]`` int64 adjacency, with
                   local (per-node-type) indices. Every declared edge type is
                   present; absent ones get a ``[2, 0]`` array.
        timestamp: the snapshot timestamp, passed through unchanged.
    """

    features: Dict[str, np.ndarray]
    node_ids: Dict[str, List[str]]
    edges: Dict[EdgeType, np.ndarray]
    timestamp: object = None
    raw_nodes: Dict[str, List[Dict]] = field(default_factory=dict, repr=False)

    def num_nodes(self, node_type: str) -> int:
        return int(self.features[node_type].shape[0])


def build_arrays(snapshot: Dict) -> SnapshotArrays:
    """Convert a ``SpannerDataset.fetch_snapshot()`` dict into numpy arrays.

    Features are **unscaled**. Apply ``core.normalize.Normalizer.transform``
    afterwards — keeping the two steps separate is what lets an A/B comparison
    hold the normalizer fixed while varying the model.
    """
    nodes_by_type: Dict[str, List[Dict]] = {t: [] for t in NODE_TYPES}
    # node id → (node type, index within that type)
    local_index: Dict[str, Tuple[str, int]] = {}

    for node in snapshot.get("nodes", []):
        ntype = node.get("type")
        if ntype not in nodes_by_type:
            continue
        local_index[node["id"]] = (ntype, len(nodes_by_type[ntype]))
        nodes_by_type[ntype].append(node)

    features: Dict[str, np.ndarray] = {}
    for ntype in NODE_TYPES:
        node_list = nodes_by_type[ntype]
        if not node_list:
            features[ntype] = np.zeros((0, FEATURE_DIMS[ntype]), dtype=np.float32)
            continue
        rows = [schema.feature_row(n, ntype) for n in node_list]
        features[ntype] = np.array(rows, dtype=np.float32)

    edge_buckets: Dict[EdgeType, Tuple[List[int], List[int]]] = {
        et: ([], []) for et in EDGE_TYPES
    }
    for edge in snapshot.get("edges", []):
        src_id, dst_id = edge.get("source"), edge.get("target")
        if src_id not in local_index or dst_id not in local_index:
            continue
        src_type, src_idx = local_index[src_id]
        dst_type, dst_idx = local_index[dst_id]
        key = (src_type, edge.get("relation"), dst_type)
        bucket = edge_buckets.get(key)
        if bucket is not None:
            bucket[0].append(src_idx)
            bucket[1].append(dst_idx)

    edges: Dict[EdgeType, np.ndarray] = {
        et: (np.array([srcs, dsts], dtype=np.int64) if srcs
             else np.zeros((2, 0), dtype=np.int64))
        for et, (srcs, dsts) in edge_buckets.items()
    }

    return SnapshotArrays(
        features=features,
        node_ids={t: [n["id"] for n in nodes_by_type[t]] for t in NODE_TYPES},
        edges=edges,
        timestamp=snapshot.get("timestamp"),
        raw_nodes=nodes_by_type,
    )


def node_display_names(snapshot: Dict) -> Dict[str, str]:
    """node id → human-readable name, for anomaly explanations and dashboards."""
    return {
        node["id"]: node.get("hostname", node.get("name", node["id"]))
        for node in snapshot.get("nodes", [])
    }


def observed_edge_types(snapshots: List[SnapshotArrays]) -> List[EdgeType]:
    """Edge types present across ``snapshots``, in declared (``EDGE_TYPES``) order.

    ``build_arrays`` emits every declared edge type — empty ones as ``[2, 0]``
    — so in practice this returns all 11. Backends instantiate one set of
    per-relation weights per entry, matching the pre-refactor behaviour where
    metadata was derived from ``HeteroData.edge_index_dict.keys()``.

    The one deliberate change is **ordering**: this used to come out of a
    ``set``, so module-registration order varied between runs. Fixing it to
    the declared order makes seeded runs reproducible and does not affect
    checkpoint compatibility (per-relation weights are keyed by name).
    """
    seen = {et for s in snapshots for et in s.edges}
    return [et for et in EDGE_TYPES if et in seen]
