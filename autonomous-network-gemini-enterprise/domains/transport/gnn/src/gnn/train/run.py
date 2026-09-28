"""run.py — backend-agnostic training orchestration.

The sequence is fixed and shared by every variant; only ``load_backend`` and
the ``TrainConfig`` differ:

    snapshots
      → quality filter          (core.quality)
      → chronological split     (core.split)
      → fit normalizer on train (core.normalize)
      → numpy arrays            (core.snapshot)
      → framework graphs        (backend.build_graph)
      → train                   (backend.train)
      → healthy cluster stats   (core.scoring)
      → bundle                  (train.artifacts)

Keeping the order fixed is what makes an A/B comparison a comparison. In
particular the normalizer is fitted **before** the backend is involved and is
handed to every variant unchanged — if each variant refitted its own scalers
the two runs would differ by more than their convolution.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from gnn.backends import load_backend, parse_variant
from gnn.backends.base import TrainConfig, TrainResult
from gnn.core import quality, scoring, split
from gnn.core.normalize import Normalizer
from gnn.core.snapshot import build_arrays
from gnn.train import artifacts

logger = logging.getLogger(__name__)


def train_from_snapshots(
    snapshots: Sequence[Dict],
    output_dir: Path = Path("."),
    variant: str = "pyg.sage",
    config: Optional[TrainConfig] = None,
    val_fraction: float = split.VALIDATION_SPLIT,
    normalizer: Optional[Normalizer] = None,
) -> Tuple[TrainResult, Normalizer, Dict[str, scoring.ClusterStats]]:
    """Train one variant end to end and write its artefact bundle.

    Args:
        snapshots: raw snapshot dicts in ascending time order.
        output_dir: bundle directory; created if absent.
        variant: ``"<backend>.<arch>"``, e.g. ``"pyg.sage"`` or ``"pyg.gat"``.
        config: hyperparameters. ``config.arch`` is overridden by ``variant``.
        val_fraction: chronological holdout fraction.
        normalizer: reuse an already-fitted normalizer instead of fitting one.
            Pass the same object across variants when A/B testing.

    Returns:
        ``(train_result, normalizer, cluster_stats)``.
    """
    backend_name, arch = parse_variant(variant)
    config = config or TrainConfig()
    config.arch = arch

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    backend = load_backend(backend_name, **config.backend_options)
    if arch not in backend.architectures:
        raise ValueError(
            f"backend '{backend_name}' has no architecture '{arch}'; "
            f"available: {list(backend.architectures)}"
        )

    # ── Data preparation (identical for every variant) ───────────────────────
    snapshots, _ = quality.drop_outlier_snapshots(snapshots)
    if not snapshots:
        raise ValueError("no snapshots left after quality filtering")

    train_snaps, val_snaps = split.chronological_split(snapshots, val_fraction)

    if normalizer is None:
        logger.info("Fitting scalers on training snapshots only...")
        normalizer = Normalizer.fit(train_snaps)
    else:
        logger.info("Reusing supplied normalizer (%s)", normalizer)
    artifacts.save_normalizer(output_dir, normalizer)

    logger.info("Building %s graphs...", backend_name)
    train_graphs = [backend.build_graph(normalizer.transform(build_arrays(s)))
                    for s in train_snaps]
    val_graphs = [backend.build_graph(normalizer.transform(build_arrays(s)))
                  for s in val_snaps]

    # ── Train ────────────────────────────────────────────────────────────────
    result = backend.train(train_graphs, val_graphs, config)
    result.model.save(output_dir, val_loss=result.best_val_loss)

    # ── Calibrate anomaly thresholds on the healthy training set ─────────────
    logger.info("Computing healthy embedding cluster statistics...")
    embeddings = collect_embeddings(result.model, train_graphs)
    cluster_stats = scoring.fit_cluster_stats(embeddings)
    node_stats = scoring.fit_node_stats(embeddings)
    artifacts.save_stats(output_dir, cluster_stats, node_stats, variant=variant)

    return result, normalizer, cluster_stats


def collect_embeddings(model, graphs: Sequence) -> Dict[str, List[np.ndarray]]:
    """Embeddings for every graph, grouped by node type.

    Only the training graphs should go in here: the cluster statistics define
    what "healthy" looks like, so anything anomalous in the input widens the
    covariance and desensitises the detector against exactly the faults it is
    meant to catch.
    """
    out: Dict[str, List[np.ndarray]] = {}
    for graph in graphs:
        for node_type, matrix in model.embed(graph).items():
            out.setdefault(node_type, []).append(matrix)
    return out
