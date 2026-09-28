"""gnn.backends.pyg — PyTorch Geometric backend.

Supports the ``sage`` (incumbent) and ``gat`` architectures. Importing this
module imports torch and torch_geometric; go through
``gnn.backends.load_backend("pyg")`` so that never happens by accident.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

from gnn.backends.base import TrainConfig, TrainResult
from gnn.backends.pyg import archs, checkpoint
from gnn.backends.pyg.graph import GraphBuilder, arrays_to_hetero, hetero_to_numpy
from gnn.backends.pyg.model import HetGNN, contrastive_diversity_loss
from gnn.backends.pyg.trainer import train_model
from gnn.core.schema import EDGE_TYPES, FEATURE_DIMS, NODE_TYPES
from gnn.core.snapshot import SnapshotArrays

logger = logging.getLogger(__name__)

EdgeType = Tuple[str, str, str]


def graph_metadata(graphs: Sequence) -> Tuple[List[str], List[EdgeType]]:
    """PyG metadata tuple covering every edge type seen in ``graphs``.

    Ordered by the schema's ``EDGE_TYPES`` rather than by set iteration, so
    module registration order — and therefore a seeded run's weight init — is
    reproducible.
    """
    seen = {et for g in graphs for et in g.edge_index_dict}
    return list(NODE_TYPES), [et for et in EDGE_TYPES if et in seen]


class PyGTrainedModel:
    """Adapter putting a trained ``HetGNN`` behind the ``TrainedModel`` protocol.

    Everything crossing this boundary is numpy — see ``gnn.backends.base``.
    """

    def __init__(self, model: HetGNN, config: TrainConfig, device: torch.device = None):
        self.model = model
        self.config = config
        self.device = device or torch.device("cpu")
        self.model.to(self.device)
        self.model.eval()

    @property
    def variant(self) -> str:
        return f"pyg.{self.model.arch}"

    def forward(self, graph) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        graph = graph.to(self.device)
        with torch.no_grad():
            recon, embeddings = self.model(graph.x_dict, graph.edge_index_dict)
        return hetero_to_numpy(recon), hetero_to_numpy(embeddings)

    def embed(self, graph) -> Dict[str, np.ndarray]:
        return self.forward(graph)[1]

    def reconstruct(self, graph) -> Dict[str, np.ndarray]:
        return self.forward(graph)[0]

    def save(self, bundle_dir: Path, val_loss: float = float("nan")) -> None:
        checkpoint.save_model(self.model, Path(bundle_dir), self.config, val_loss)


class PyGBackend:
    """PyTorch Geometric backend."""

    name = "pyg"

    @property
    def architectures(self) -> Tuple[str, ...]:
        return tuple(sorted(archs.SPECS))

    # ── graph construction ───────────────────────────────────────────────────

    def build_graph(self, arrays: SnapshotArrays):
        """Wrap already-normalized arrays in a ``HeteroData``."""
        return arrays_to_hetero(arrays)

    # ── training ─────────────────────────────────────────────────────────────

    def train(self, train_graphs: Sequence, val_graphs: Sequence,
              config: TrainConfig) -> TrainResult:
        if config.seed is not None:
            torch.manual_seed(config.seed)
            torch.cuda.manual_seed_all(config.seed)   # no-op on CPU
            logger.info("PyTorch RNG seeded with seed=%d", config.seed)
        else:
            # Stochastic by default. Pinned seeds proved brittle for this
            # autoencoder — seed=42 produced a VPN average score of 43,000 at
            # serve time, seed=1337 a val_loss of 2.42 that trips the pipeline
            # quality gate. Stochastic init plus the quality gate is more
            # robust than picking a seed and hoping.
            logger.info("seed not set — training is stochastic (different init each run)")

        node_types, edge_types = graph_metadata(list(train_graphs) + list(val_graphs))
        logger.info("Node types (%d): %s", len(node_types), node_types)
        logger.info("Edge types (%d): %s", len(edge_types), edge_types)

        model = HetGNN(
            metadata=(node_types, edge_types),
            hidden_channels=config.hidden_channels,
            out_channels=config.hidden_channels,   # symmetric autoencoder
            num_layers=config.num_layers,
            dropout=config.dropout,
            arch=config.arch,
            arch_kwargs=config.arch_kwargs,
        )
        model.set_input_dims(FEATURE_DIMS)
        logger.info(
            "Created HetGNN arch=%s hidden_channels=%d num_layers=%d dropout=%s %s",
            config.arch, config.hidden_channels, config.num_layers, config.dropout,
            model.arch_kwargs or "",
        )

        model, best_val_loss, history, epochs_run = train_model(
            model, train_graphs, val_graphs, config
        )
        return TrainResult(
            model=PyGTrainedModel(model, config),
            best_val_loss=best_val_loss,
            history=history,
            epochs_run=epochs_run,
        )

    # ── loading ──────────────────────────────────────────────────────────────

    def load(self, bundle_dir: Path, config: Optional[TrainConfig] = None,
             metadata=None) -> PyGTrainedModel:
        """Reload a trained model from ``bundle_dir/model.pth``.

        ``metadata`` defaults to the full declared schema. Passing the metadata
        derived from a live snapshot (as the serving path used to) is still
        supported, but is no longer necessary: the checkpoint records the edge
        types it was trained with.
        """
        model, loaded_config = checkpoint.load_model(
            Path(bundle_dir), metadata=metadata, input_dims=FEATURE_DIMS
        )
        return PyGTrainedModel(model, config or loaded_config)


__all__ = [
    "PyGBackend", "PyGTrainedModel", "GraphBuilder", "HetGNN",
    "contrastive_diversity_loss", "arrays_to_hetero", "hetero_to_numpy",
    "graph_metadata",
]
