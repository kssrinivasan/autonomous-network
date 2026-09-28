"""graph.py — SnapshotArrays → DGF ``InMemoryGraph``, plus padding and masks.

Pure adapter, same role as ``gnn.backends.pyg.graph``. Two things make it
longer than its PyG counterpart, both consequences of JAX needing static
shapes:

**Padding is a property of the model, not of a snapshot.** ``build_graph``
therefore returns an *unpadded* graph and defers padding to train/inference
time, where the padding recorded in the bundle is applied. Padding at
``build_graph`` time would mean the first snapshot's size silently fixed the
shapes for every later one.

**Sentinel rows must never escape.** ``pad`` returns ``(jax_graph, masks)``
together so the caller physically cannot forget the mask, and the model strips
padded rows before returning numpy — see ``gnn.backends.base``. A leaked
sentinel row is invisible in training and only shows up as wrong anomaly
scores, because it contaminates every mean and covariance downstream.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np

from gnn.backends.dgf.schema import (EDGE_TYPE_TO_SET, FEATURE_KEY,
                                     FORWARD_EDGE_TYPES)
from gnn.core.schema import FEATURE_DIMS, NODE_TYPES
from gnn.core.snapshot import SnapshotArrays

logger = logging.getLogger(__name__)


@dataclass
class DGFGraph:
    """An unpadded snapshot plus the bookkeeping padding will need later.

    ``node_ids`` and ``num_nodes`` are carried alongside the DGF graph because
    once it is merged and padded, the mapping from row index back to a Spanner
    node id is no longer recoverable from the graph itself.
    """

    graph: Any                       # dgf.data.InMemoryGraph
    num_nodes: Dict[str, int]
    node_ids: Dict[str, List[str]]


def arrays_to_graph(arrays: SnapshotArrays) -> DGFGraph:
    """Wrap already-normalized ``SnapshotArrays`` in an ``InMemoryGraph``.

    Every declared node and edge set gets an entry, empty ones included, so the
    graph always matches the schema regardless of what the snapshot contained —
    a snapshot with no running flows must not produce a differently-shaped
    graph.
    """
    from dgf.data import InMemoryEdgeSet, InMemoryGraph, InMemoryNodeSet

    node_sets, num_nodes = {}, {}
    for node_type in NODE_TYPES:
        features = np.asarray(arrays.features[node_type], dtype=np.float32)
        if features.ndim != 2 or features.shape[1] != FEATURE_DIMS[node_type]:
            raise ValueError(
                f"'{node_type}' features have shape {features.shape}, expected "
                f"(N, {FEATURE_DIMS[node_type]}) — normalizer or schema mismatch"
            )
        num_nodes[node_type] = int(features.shape[0])
        node_sets[node_type] = InMemoryNodeSet(
            num_nodes=int(features.shape[0]),
            features={FEATURE_KEY: features},
        )

    # Only the forward direction of each relation contributes adjacency; the
    # PyG inverse types are re-expressed as reversed plan entries instead.
    edge_sets = {}
    for edge_type in FORWARD_EDGE_TYPES:
        name = EDGE_TYPE_TO_SET[edge_type][0]
        adjacency = np.asarray(arrays.edges[edge_type], dtype=np.int32)
        if adjacency.size == 0:
            adjacency = np.zeros((2, 0), dtype=np.int32)
        edge_sets[name] = InMemoryEdgeSet(adjacency=adjacency, features={})

    return DGFGraph(
        graph=InMemoryGraph(node_sets=node_sets, edge_sets=edge_sets),
        num_nodes=num_nodes,
        node_ids={k: list(v) for k, v in arrays.node_ids.items()},
    )


# ── padding ───────────────────────────────────────────────────────────────────

def derive_padding(graphs: Sequence[DGFGraph], schema,
                   relative_margin: float = 0.25, absolute_margin: int = 8):
    """Padding large enough for every graph in ``graphs``, with headroom.

    Margins are wider than DGF's defaults (0.1 / 1) on purpose. Network
    topologies grow: a model padded to exactly the training maximum starts
    raising ``InsufficientPaddingError`` at serving time the first day someone
    provisions another router. Headroom costs a little wasted compute per
    forward pass and buys a model that survives ordinary growth.
    """
    from dgf.analyse import padding_from_graph_generator

    padding = padding_from_graph_generator(
        schema, iter([g.graph for g in graphs]),
        relative_margin=relative_margin, absolute_margin=absolute_margin,
    )
    logger.info("Derived padding: nodes=%s edges=%s",
                {k: v.num_nodes for k, v in padding.node_sets.items()},
                {k: v.num_edges for k, v in padding.edge_sets.items()})
    return padding


def padding_to_dict(padding) -> Dict[str, Dict[str, int]]:
    """``Padding`` → plain JSON, for ``padding.json`` in the bundle."""
    return {
        "node_sets": {k: int(v.num_nodes) for k, v in padding.node_sets.items()},
        "edge_sets": {k: int(v.num_edges) for k, v in padding.edge_sets.items()},
    }


def padding_from_dict(payload: Dict[str, Dict[str, int]]):
    """Inverse of :func:`padding_to_dict`."""
    from dgf.data import EdgeSetPadding, NodeSetPadding, Padding

    return Padding(
        node_sets={k: NodeSetPadding(num_nodes=int(v))
                   for k, v in payload["node_sets"].items()},
        edge_sets={k: EdgeSetPadding(num_edges=int(v))
                   for k, v in payload["edge_sets"].items()},
    )


def grow_padding(padding, graph: DGFGraph, factor: float = 1.5):
    """A copy of ``padding`` enlarged to fit ``graph``.

    The escape hatch for ``InsufficientPaddingError``: better to recompile the
    jitted forward pass for a bigger shape than to fail a prediction request.
    """
    from dgf.data import EdgeSetPadding, NodeSetPadding, Padding

    node_sets = {}
    for name, current in padding.node_sets.items():
        needed = graph.num_nodes.get(name, 0) + 1
        node_sets[name] = NodeSetPadding(
            num_nodes=max(int(current.num_nodes), int(np.ceil(needed * factor)))
        )
    edge_sets = {}
    for name, current in padding.edge_sets.items():
        edge_set = graph.graph.edge_sets.get(name)
        needed = (edge_set.adjacency.shape[1] if edge_set is not None else 0) + 1
        edge_sets[name] = EdgeSetPadding(
            num_edges=max(int(current.num_edges), int(np.ceil(needed * factor)))
        )
    return Padding(node_sets=node_sets, edge_sets=edge_sets)


def node_masks(offsets: Dict[str, np.ndarray], padding) -> Dict[str, np.ndarray]:
    """Boolean mask per node set selecting the real (non-sentinel) rows.

    Real nodes occupy ``[offsets[ns][0], offsets[ns][-1])`` — with
    ``sentinel_offset=True`` the final offset is the start of the padding
    region. Comparing against ``offsets[ns][-1]`` rather than assuming the
    sentinel sits at ``P-1`` is correct under either reading of DGF's docstring;
    :func:`pad` asserts the resulting count anyway.
    """
    return {
        name: np.arange(padding.node_sets[name].num_nodes) < int(offset[-1])
        for name, offset in offsets.items()
    }


def pad(graph: DGFGraph, schema, padding) -> Tuple[Any, Dict[str, np.ndarray]]:
    """Merge one graph up to ``padding`` and return ``(jax_graph, masks)``.

    Returned together deliberately: every consumer of the padded graph needs the
    mask, and handing back the graph alone invites a silent contamination bug.
    """
    from dgf.convert import graph_to_jax_graph
    from dgf.transform import merge_graphs

    padded, offsets = merge_graphs([graph.graph], schema, padding,
                                   sentinel_offset=True)
    masks = node_masks(offsets, padding)

    # Cheap, and it pins down the one thing the DGF docs are ambiguous about.
    for name, mask in masks.items():
        expected = graph.num_nodes.get(name, 0)
        if int(mask.sum()) != expected:
            raise AssertionError(
                f"padding mask for '{name}' admits {int(mask.sum())} rows but the "
                f"graph has {expected} real nodes — sentinel layout is not what "
                "node_masks() assumes"
            )
    return graph_to_jax_graph(padded), masks


def pad_batch(graphs: Iterable[DGFGraph], schema, padding
              ) -> List[Tuple[Any, Dict[str, np.ndarray]]]:
    """:func:`pad` over a sequence — the training set, materialised once."""
    return [pad(g, schema, padding) for g in graphs]


__all__ = [
    "DGFGraph", "arrays_to_graph", "derive_padding", "grow_padding",
    "node_masks", "pad", "pad_batch", "padding_to_dict", "padding_from_dict",
]
