"""inference.py — score one snapshot, backend-agnostic.

Everything here operates on numpy, so it is identical whether the embeddings
came out of PyTorch or JAX. That is the payoff of the backend contract: swapping
the model does not touch scoring, explanation, or the Spanner row layout.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from gnn.core import scoring
from gnn.core.schema import ANOMALY_THRESHOLDS, FEATURE_NAMES
from gnn.core.scoring import ClusterStats, TypeScores
from gnn.core.snapshot import SnapshotArrays

logger = logging.getLogger(__name__)


@dataclass
class InferenceResult:
    """Per-node embeddings, scores and explanations for one snapshot."""

    embeddings: Dict[str, List[List[float]]]
    anomaly_scores: Dict[str, List[float]]
    anomaly_explanations: Dict[str, List[Dict]]
    anomaly_count: int
    node_id_map: Dict[str, List[str]]
    per_type: Dict[str, TypeScores]


def score_snapshot(
    model,
    graph,
    arrays: SnapshotArrays,
    cluster_stats: Optional[Dict[str, ClusterStats]] = None,
    mse_thresholds: Optional[Dict[str, float]] = None,
) -> InferenceResult:
    """Run the model over one snapshot and score every node.

    Args:
        model: anything satisfying the ``TrainedModel`` protocol.
        graph: that model's backend-native graph for this snapshot.
        arrays: the same snapshot as normalized numpy — the reconstruction
            target, and the source of node ids.
        cluster_stats: per-type healthy statistics. Types present here are
            scored by Mahalanobis distance; types absent fall back to
            reconstruction MSE.
        mse_thresholds: thresholds for the fallback path.
    """
    cluster_stats = cluster_stats or {}
    mse_thresholds = mse_thresholds or dict(ANOMALY_THRESHOLDS)

    reconstructions, embeddings = model.forward(graph)

    per_type: Dict[str, TypeScores] = {}
    anomaly_scores: Dict[str, List[float]] = {}
    anomaly_explanations: Dict[str, List[Dict]] = {}
    total_anomalies = 0

    for node_type, reconstruction in reconstructions.items():
        features = arrays.features.get(node_type)
        if features is None:
            continue
        result = scoring.score_type(
            node_type=node_type,
            embeddings=embeddings.get(node_type, np.zeros((features.shape[0], 0))),
            features=features,
            reconstruction=reconstruction,
            cluster_stats=cluster_stats.get(node_type),
            mse_threshold=mse_thresholds.get(node_type, 0.20),
        )
        per_type[node_type] = result
        anomaly_scores[node_type] = result.scores.tolist()
        anomaly_explanations[node_type] = scoring.explain(
            result, FEATURE_NAMES.get(node_type, [])
        )
        total_anomalies += result.anomaly_count
        logger.info(
            "  %s: %d nodes, %d anomalies (%s > %.4f)",
            node_type, result.scores.shape[0], result.anomaly_count,
            result.kind, result.threshold,
        )

    return InferenceResult(
        embeddings={t: e.tolist() for t, e in embeddings.items()},
        anomaly_scores=anomaly_scores,
        anomaly_explanations=anomaly_explanations,
        anomaly_count=total_anomalies,
        node_id_map=dict(arrays.node_ids),
        per_type=per_type,
    )
