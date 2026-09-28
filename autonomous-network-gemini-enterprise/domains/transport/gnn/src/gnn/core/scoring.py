"""scoring.py — anomaly scoring in embedding space.

Framework-free (numpy only). This is the far side of the "numpy sandwich":
every backend hands over ``dict[node_type, np.ndarray]`` embeddings and
reconstructions with padding already stripped, and everything from here on —
distances, calibration, thresholds, explanations — is shared.

Scoring
───────
Primary path is **Mahalanobis distance in embedding space**: how far a node's
embedding sits from the centroid of the healthy training cluster for its type,
measured in units of the healthy cluster's own covariance.

    D_M(z) = sqrt((z - μ)ᵀ Σ⁻¹ (z - μ))

This beats per-feature reconstruction MSE because reconstruction error is
dominated by whichever features happen to be hardest to reconstruct, whereas
D_M asks the question we actually care about: does this node look like the
healthy population?

Fallback path (model artefacts predating cluster stats) is per-feature
reconstruction MSE against the hardcoded ``schema.ANOMALY_THRESHOLDS``.

Per-feature MSE is computed on **both** paths regardless — it is what drives
the "top features driving this anomaly" explanation written to Spanner.

Precision
─────────
Statistics are accumulated in ``float32`` by default, matching the PyTorch
implementation this was lifted from, so the refactor is behaviour-preserving.
``float64`` is the numerically sounder choice for inverting a near-singular
64×64 covariance and is available via the ``dtype`` argument — but flipping it
shifts calibrated thresholds slightly, so treat it as a deliberate model change
with its own before/after evidence, not a free win.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Ridge added to the covariance diagonal before inversion. Rare-class types
# (vpn has N=3 nodes per snapshot) otherwise produce an effectively
# rank-deficient covariance that inverts to garbage.
MAHA_RIDGE = 1e-5

# Threshold = mean + MAHA_K·σ of the healthy D_M distribution. K=2.0 is roughly
# the 95th Gaussian percentile — sensitive enough to catch single-feature faults
# like vrf-rt-misconfiguration, where the affected PE's embedding moves only
# modestly relative to the healthy spread.
MAHA_K = 2.0

# Per-type floor: max_healthy_D_M × multiplier. Each type needs a floor because
# mean+Kσ over a mixed-role training population is too tight at serving time:
#   router    — PE2 (hub) and CE/RR routers sit at the edge of the population
#               centroid for structural reasons, not faults. Live healthy D_M
#               for PE2 routinely hits 30 while population mean+2σ is ~13.
#   interface — route-reflector loopbacks and hub-side links diverge from the
#               bulk distribution the same way.
#   vpn       — N=3 unique nodes, so the empirical σ measures within-node
#               temporal fluctuation rather than across-population spread.
#   flow      — per-flow KPIs (latency, jitter, loss) drift with traffic
#               conditions; mean+2σ over a 1.5h training window underestimates.
# 1.2× gives ~20% headroom above the worst healthy training point; vpn gets
# 1.5× to reflect the much larger observed serving variance of its tiny
# population.
FLOOR_MULT_PER_TYPE: Dict[str, float] = {
    "router":    1.2,
    "interface": 1.2,
    "vpn":       1.5,
    "flow":      1.2,
}

# Number of features named in an anomaly explanation.
TOP_FEATURES = 3

# Minimum observations before a per-node (as opposed to per-type) statistic is
# considered meaningful.
MIN_NODE_OBSERVATIONS = 5


@dataclass
class ClusterStats:
    """Healthy-embedding statistics for one node type."""

    mean: np.ndarray            # [D] centroid of healthy embeddings
    std: np.ndarray             # [D] per-dimension spread
    cov: np.ndarray             # [D, D]
    cov_inv: np.ndarray         # [D, D] ridge-regularised inverse
    sample_count: int
    calibrated_threshold: float
    healthy_d_mean: float
    healthy_d_std: float
    healthy_d_max: float

    def distance(self, embeddings: np.ndarray) -> np.ndarray:
        """Mahalanobis distance of each row of ``[N, D]`` from the centroid."""
        return mahalanobis(embeddings, self.mean, self.cov_inv)

    def to_artifact(self) -> Dict:
        return {
            "mean":                 self.mean,
            "std":                  self.std,
            "cov":                  self.cov,
            "cov_inv":              self.cov_inv,
            "sample_count":         self.sample_count,
            "calibrated_threshold": self.calibrated_threshold,
            "healthy_d_mean":       self.healthy_d_mean,
            "healthy_d_std":        self.healthy_d_std,
            "healthy_d_max":        self.healthy_d_max,
        }

    @classmethod
    def from_artifact(cls, blob: Mapping) -> "ClusterStats":
        """Rehydrate from a persisted dict.

        Tolerates torch tensors so ``model_stats.pth`` files written before the
        numpy refactor still load.
        """
        required = ("mean", "cov_inv", "calibrated_threshold")
        missing = [k for k in required if k not in blob]
        if missing:
            raise KeyError(f"cluster stats missing {missing}")
        mean = _as_numpy(blob["mean"])
        return cls(
            mean=mean,
            std=_as_numpy(blob.get("std", np.zeros_like(mean))),
            cov=_as_numpy(blob.get("cov", np.eye(mean.shape[0], dtype=mean.dtype))),
            cov_inv=_as_numpy(blob["cov_inv"]),
            sample_count=int(blob.get("sample_count", 0)),
            calibrated_threshold=float(blob["calibrated_threshold"]),
            healthy_d_mean=float(blob.get("healthy_d_mean", 0.0)),
            healthy_d_std=float(blob.get("healthy_d_std", 0.0)),
            healthy_d_max=float(blob.get("healthy_d_max", 0.0)),
        )


def _as_numpy(value) -> np.ndarray:
    """Coerce a torch tensor / list / array to a numpy array without copying if possible."""
    detach = getattr(value, "detach", None)
    if detach is not None:                      # torch.Tensor
        value = detach().cpu()
    return np.asarray(value)


# ── Distance ─────────────────────────────────────────────────────────────────

def mahalanobis(embeddings: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray) -> np.ndarray:
    """Mahalanobis distance of each row of ``[N, D]`` from ``mean``.

    The squared distance is clipped at zero before the square root: a
    regularised inverse of a near-singular covariance can produce a tiny
    negative quadratic form from rounding alone.
    """
    if embeddings.shape[0] == 0:
        return np.zeros((0,), dtype=embeddings.dtype)
    diffs = embeddings - mean
    d_sq = np.einsum("nd,de,ne->n", diffs, cov_inv, diffs)
    return np.sqrt(np.clip(d_sq, 0.0, None))


# ── Fitting ──────────────────────────────────────────────────────────────────

def group_by_node_index(
    per_snapshot: Sequence[np.ndarray],
) -> Dict[int, List[np.ndarray]]:
    """Regroup a list of ``[N, D]`` per-snapshot matrices by node index.

    Node index is position within the node type, which is stable across
    snapshots as long as the topology doesn't change. Grouping this way is what
    makes the per-node statistics in ``node_stats`` meaningful.
    """
    grouped: Dict[int, List[np.ndarray]] = {}
    for matrix in per_snapshot:
        for idx in range(matrix.shape[0]):
            grouped.setdefault(idx, []).append(matrix[idx])
    return grouped


def fit_cluster_stats(
    embeddings_per_snapshot: Mapping[str, Sequence[np.ndarray]],
    *,
    ridge: float = MAHA_RIDGE,
    k: float = MAHA_K,
    floor_mult_per_type: Mapping[str, float] = None,
    dtype=np.float32,
) -> Dict[str, ClusterStats]:
    """Fit healthy-cluster statistics per node type.

    Args:
        embeddings_per_snapshot: node type → list of ``[N, D]`` embedding
            matrices, one per (healthy) training snapshot.
        ridge: added to the covariance diagonal before inversion.
        k: σ multiplier for the mean+Kσ threshold candidate.
        floor_mult_per_type: per-type multiplier on ``max_healthy`` for the
            threshold floor. Types absent from the mapping get no floor.
        dtype: accumulation dtype — see the module docstring on precision.
    """
    floor_mult_per_type = (
        FLOOR_MULT_PER_TYPE if floor_mult_per_type is None else floor_mult_per_type
    )
    stats: Dict[str, ClusterStats] = {}

    for node_type, matrices in embeddings_per_snapshot.items():
        # Flatten in node-index order, matching the pre-refactor grouping.
        grouped = group_by_node_index(matrices)
        rows = [row for embs in grouped.values() for row in embs]
        if not rows:
            continue

        stacked = np.stack(rows).astype(dtype, copy=False)     # [N, D]
        n, d = stacked.shape
        mean = stacked.mean(axis=0)

        if n > 1:
            cov = np.cov(stacked.astype(np.float64), rowvar=False, ddof=1)
            cov = np.atleast_2d(cov).astype(dtype)
        else:
            cov = np.eye(d, dtype=dtype)

        cov_reg = cov + ridge * np.eye(d, dtype=dtype)
        try:
            cov_inv = np.linalg.inv(cov_reg)
        except np.linalg.LinAlgError as exc:
            logger.warning(
                "  %s: covariance inversion failed (%s); falling back to identity "
                "(D_M degrades to Euclidean).", node_type, exc,
            )
            cov_inv = np.eye(d, dtype=dtype)

        healthy_d = mahalanobis(stacked, mean, cov_inv)
        d_mean = float(healthy_d.mean())
        d_std = float(healthy_d.std(ddof=1)) if n > 1 else 0.0
        max_healthy = float(healthy_d.max())

        floor_mult = float(floor_mult_per_type.get(node_type, 0.0))
        mean_plus_k = d_mean + k * d_std
        floor_val = max_healthy * floor_mult
        calibrated = max(mean_plus_k, floor_val)

        stats[node_type] = ClusterStats(
            mean=mean,
            std=stacked.std(axis=0, ddof=1) if n > 1 else np.zeros(d, dtype=dtype),
            cov=cov,
            cov_inv=cov_inv,
            sample_count=n,
            calibrated_threshold=calibrated,
            healthy_d_mean=d_mean,
            healthy_d_std=d_std,
            healthy_d_max=max_healthy,
        )

        chosen = "floor" if floor_val > mean_plus_k else f"mean+{k}σ"
        logger.info(
            "  %s: %d healthy samples, embedding_dim=%d, healthy_D_M mean=%.3f "
            "std=%.3f max=%.3f, mean+%sσ=%.3f, floor=%.3f (mult=%s), "
            "calibrated_threshold=%.3f [%s]",
            node_type, n, d, d_mean, d_std, max_healthy, k, mean_plus_k,
            floor_val, floor_mult, calibrated, chosen,
        )

    return stats


def fit_node_stats(
    embeddings_per_snapshot: Mapping[str, Sequence[np.ndarray]],
    *,
    min_observations: int = MIN_NODE_OBSERVATIONS,
    dtype=np.float32,
) -> Dict[str, Dict[int, Dict[str, np.ndarray]]]:
    """Per-node (rather than per-type) embedding mean/std.

    Nodes observed fewer than ``min_observations`` times are skipped — their
    statistics would be noise. Not currently used for scoring; retained because
    it is the natural basis for per-node drift detection.
    """
    out: Dict[str, Dict[int, Dict[str, np.ndarray]]] = {}
    for node_type, matrices in embeddings_per_snapshot.items():
        per_node = {}
        for idx, embs in group_by_node_index(matrices).items():
            if len(embs) < min_observations:
                continue
            stacked = np.stack(embs).astype(dtype, copy=False)
            per_node[idx] = {
                "mean": stacked.mean(axis=0),
                "std":  stacked.std(axis=0, ddof=1),
            }
        out[node_type] = per_node
    return out


# ── Serving-time scoring ─────────────────────────────────────────────────────

@dataclass
class TypeScores:
    """Scoring outcome for all nodes of one type in one snapshot."""

    node_type: str
    scores: np.ndarray          # [N]
    is_anomaly: np.ndarray      # [N] bool
    threshold: float
    kind: str                   # "D_M" or "MSE"
    feature_mse: np.ndarray     # [N, F] per-feature reconstruction error

    @property
    def anomaly_count(self) -> int:
        return int(self.is_anomaly.sum())


def score_type(
    node_type: str,
    embeddings: np.ndarray,
    features: np.ndarray,
    reconstruction: np.ndarray,
    cluster_stats: Optional[ClusterStats] = None,
    mse_threshold: float = 0.20,
) -> TypeScores:
    """Score every node of one type.

    Uses Mahalanobis distance when ``cluster_stats`` is available, otherwise
    falls back to mean per-feature reconstruction MSE. Per-feature MSE is
    returned either way for explanations.
    """
    feature_mse = (reconstruction - features) ** 2               # [N, F]

    if cluster_stats is not None:
        scores = cluster_stats.distance(embeddings)
        threshold = cluster_stats.calibrated_threshold
        kind = "D_M"
    else:
        scores = feature_mse.mean(axis=1) if feature_mse.size else np.zeros(0)
        threshold = mse_threshold
        kind = "MSE"

    return TypeScores(
        node_type=node_type,
        scores=scores,
        is_anomaly=scores > threshold,
        threshold=float(threshold),
        kind=kind,
        feature_mse=feature_mse,
    )


def explain(
    result: TypeScores,
    feature_names: Sequence[str],
    top_k: int = TOP_FEATURES,
) -> List[Dict]:
    """One explanation dict per node, aligned with ``result.scores``.

    Anomalous nodes get the ``top_k`` features with the highest reconstruction
    MSE. Explanations always come from per-feature MSE, even when the flag was
    raised by D_M — a distance in a 64-dimensional latent space is not
    something an operator can act on, but "rx_errs_rate is unreconstructable"
    is.
    """
    explanations: List[Dict] = []
    for idx in range(result.scores.shape[0]):
        if not result.is_anomaly[idx]:
            explanations.append({"anomalous": False})
            continue
        per_feat = result.feature_mse[idx]
        # Stable descending sort — ties resolve to the lower column index, so
        # the named features don't shuffle between otherwise identical runs.
        order = np.argsort(-per_feat, kind="stable")[:top_k]
        explanations.append({
            "anomalous": True,
            "top_features": [
                {
                    "feature": feature_names[fi] if fi < len(feature_names) else str(fi),
                    "mse": round(float(per_feat[fi]), 5),
                }
                for fi in order
            ],
        })
    return explanations
