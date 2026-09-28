"""graph.py — SnapshotArrays → PyTorch Geometric HeteroData.

Pure adapter. All schema decisions live in ``gnn.core``; this file only wraps
numpy in tensors and hands back numpy on the way out.
"""

from typing import Dict, Optional

import numpy as np
import torch
from torch_geometric.data import HeteroData

from gnn.core.normalize import Normalizer
from gnn.core.schema import EDGE_TYPES, NODE_TYPES
from gnn.core.snapshot import SnapshotArrays, build_arrays


def arrays_to_hetero(arrays: SnapshotArrays) -> HeteroData:
    """Wrap normalized ``SnapshotArrays`` in a ``HeteroData``.

    Every declared node and edge type gets a store, empty ones included, so
    ``metadata()`` is the same regardless of what a given snapshot contained.
    """
    data = HeteroData()
    for ntype in NODE_TYPES:
        data[ntype].x = torch.tensor(arrays.features[ntype], dtype=torch.float)
    for etype in EDGE_TYPES:
        data[etype].edge_index = torch.tensor(arrays.edges[etype], dtype=torch.long)
    data.node_id_map = dict(arrays.node_ids)
    return data


def hetero_to_numpy(tensors: Dict[str, torch.Tensor]) -> Dict[str, np.ndarray]:
    """Detach a ``{node_type: tensor}`` dict to numpy — the backend boundary."""
    return {k: v.detach().cpu().numpy() for k, v in tensors.items()}


class GraphBuilder:
    """Snapshot dict → ``HeteroData``, applying a fitted normalizer.

    Kept as a class because the training pipeline, serving path and several
    notebooks construct one and reuse it across snapshots. New code can call
    ``gnn.core.snapshot.build_arrays`` plus ``arrays_to_hetero`` directly.
    """

    def __init__(self, scalers: Optional[Dict] = None, normalizer: Optional[Normalizer] = None):
        if normalizer is not None and scalers is not None:
            raise TypeError("pass either `scalers` or `normalizer`, not both")
        self.normalizer = normalizer or Normalizer.load(scalers or {})

    @property
    def scalers(self) -> Dict:
        """The underlying ``{node_type: StandardScaler}`` dict."""
        return self.normalizer.scalers

    def process_snapshot(self, snapshot: Dict) -> HeteroData:
        """Convert a ``SpannerDataset.fetch_snapshot()`` dict to ``HeteroData``."""
        arrays = self.normalizer.transform(build_arrays(snapshot))
        return arrays_to_hetero(arrays)

    def process_arrays(self, snapshot: Dict) -> SnapshotArrays:
        """Normalized numpy arrays, for callers that don't want tensors."""
        return self.normalizer.transform(build_arrays(snapshot))
