"""metrics.py — how a variant is judged.

Reconstruction loss is deliberately *not* the headline. Two architectures with
different loss surfaces produce numerically incomparable losses, so ranking on
val_loss ranks the smoothness of the objective rather than the usefulness of the
detector. What matters operationally is:

  * **recall** on fault windows — does it catch the incident at all;
  * **false-positive rate** on healthy windows — how much noise it costs to run;
  * **time to detect** — how many snapshots after onset the flag appears;
  * **effective rank** of the embedding space — a collapse canary. Mahalanobis
    scoring needs a non-degenerate covariance, so a model with a rank-2 latent
    space can look fine on loss and be useless at detection.

Everything here takes numpy and plain dicts, so it is identical for every
backend.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

import numpy as np


@dataclass
class ConfusionCounts:
    """Node-level confusion counts accumulated over snapshots."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def __iadd__(self, other: "ConfusionCounts") -> "ConfusionCounts":
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.tn += other.tn
        return self

    @property
    def recall(self) -> float:
        """Of the nodes that were genuinely faulty, what fraction was flagged."""
        denom = self.tp + self.fn
        return self.tp / denom if denom else float("nan")

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else float("nan")

    @property
    def false_positive_rate(self) -> float:
        """Of the healthy nodes, what fraction was flagged.

        This is the number that decides whether anyone keeps the detector
        switched on: at ~1000 interfaces per snapshot and one snapshot every 30
        seconds, a 1% FPR is ten false alarms every half minute.
        """
        denom = self.fp + self.tn
        return self.fp / denom if denom else float("nan")

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if not (math.isfinite(p) and math.isfinite(r)) or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)

    def as_dict(self) -> Dict[str, float]:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "recall": self.recall, "precision": self.precision,
            "false_positive_rate": self.false_positive_rate, "f1": self.f1,
        }


def confusion_for_snapshot(
    flagged: Iterable[str],
    scored: Iterable[str],
    faulty: Set[str],
) -> ConfusionCounts:
    """Confusion counts for one snapshot.

    Args:
        flagged: node ids this model marked anomalous.
        scored: every node id the model scored — the denominator. Passing only
            the flagged ids would make the false-positive rate meaningless.
        faulty: ground-truth faulty node ids for this snapshot.
    """
    flagged = set(flagged)
    scored = set(scored)
    # Only nodes actually present can be scored; a fault label naming a node
    # that dropped out of the snapshot is not a miss, it is absent data.
    truth = faulty & scored
    return ConfusionCounts(
        tp=len(flagged & truth),
        fp=len(flagged - truth),
        fn=len(truth - flagged),
        tn=len(scored - flagged - truth),
    )


def time_to_detect(
    flags_per_snapshot: Sequence[Set[str]],
    faulty: Set[str],
    onset_index: int,
) -> Optional[int]:
    """Snapshots between fault onset and the first flag on any faulty node.

    ``0`` means detected in the very snapshot the fault appeared. ``None``
    means never detected in the window — which is a materially different
    outcome from "detected late" and so is kept distinct rather than being
    encoded as a large number.
    """
    for offset, flagged in enumerate(flags_per_snapshot[onset_index:]):
        if flagged & faulty:
            return offset
    return None


def effective_rank(embeddings: np.ndarray, eps: float = 1e-12) -> float:
    """Shannon effective rank of an ``[N, D]`` embedding matrix.

    ``exp(H)`` of the entropy of the normalised singular-value spectrum: D if
    variance is spread evenly across all dimensions, 1 if the embeddings lie on
    a single line. A value near 1 means the latent space has collapsed and the
    covariance the Mahalanobis threshold is built on is degenerate, no matter
    how good the reconstruction loss looks.
    """
    if embeddings.ndim != 2 or embeddings.shape[0] < 2 or embeddings.shape[1] == 0:
        return float("nan")
    centred = embeddings - embeddings.mean(axis=0, keepdims=True)
    sv = np.linalg.svd(centred, compute_uv=False)
    total = sv.sum()
    if total < eps:
        return 1.0
    p = sv / total
    p = p[p > eps]
    return float(np.exp(-(p * np.log(p)).sum()))


def separation(healthy_scores: np.ndarray, faulty_scores: np.ndarray) -> float:
    """Standardised mean difference between faulty and healthy score
    distributions (Cohen's d).

    Threshold-free, so it says whether the *scores* carry signal independently
    of whether the calibrated threshold happens to sit in a good place. A
    variant with strong separation and poor recall has a calibration problem,
    not an architecture problem — a distinction recall alone cannot make.
    """
    if healthy_scores.size < 2 or faulty_scores.size < 2:
        return float("nan")
    pooled = math.sqrt((healthy_scores.var(ddof=1) + faulty_scores.var(ddof=1)) / 2)
    if pooled < 1e-12:
        return float("nan")
    return float((faulty_scores.mean() - healthy_scores.mean()) / pooled)


@dataclass
class RunMetrics:
    """Everything one training run of one variant scored on the benchmark."""

    variant: str
    seed: Optional[int] = None
    val_loss: float = float("nan")
    epochs_run: int = 0
    train_seconds: float = float("nan")
    #: node_type → confusion over all fault windows
    fault_confusion: Dict[str, ConfusionCounts] = field(default_factory=dict)
    #: node_type → confusion over all healthy windows (tp/fn are always 0)
    healthy_confusion: Dict[str, ConfusionCounts] = field(default_factory=dict)
    #: window name → snapshots-after-onset, or None if never detected
    detection_delay: Dict[str, Optional[int]] = field(default_factory=dict)
    #: node_type → effective rank of the healthy embedding cloud
    effective_rank: Dict[str, float] = field(default_factory=dict)
    #: node_type → Cohen's d between faulty and healthy scores
    separation: Dict[str, float] = field(default_factory=dict)

    # ── headline scalars, aggregated over node types ─────────────────────────

    @property
    def fault_recall(self) -> float:
        total = ConfusionCounts()
        for c in self.fault_confusion.values():
            total += c
        return total.recall

    @property
    def healthy_false_positive_rate(self) -> float:
        total = ConfusionCounts()
        for c in self.healthy_confusion.values():
            total += c
        return total.false_positive_rate

    @property
    def detection_rate(self) -> float:
        """Fraction of fault windows detected at all."""
        if not self.detection_delay:
            return float("nan")
        hit = sum(1 for d in self.detection_delay.values() if d is not None)
        return hit / len(self.detection_delay)

    @property
    def median_time_to_detect(self) -> float:
        """Median delay over *detected* windows only.

        Undetected windows are excluded rather than imputed — they are counted
        by ``detection_rate``, and folding them in here would let a variant that
        detects one window instantly beat one that detects all five in two
        snapshots.
        """
        hits = [d for d in self.detection_delay.values() if d is not None]
        return float(np.median(hits)) if hits else float("nan")

    @property
    def min_effective_rank(self) -> float:
        vals = [v for v in self.effective_rank.values() if math.isfinite(v)]
        return float(min(vals)) if vals else float("nan")

    def headline(self) -> Dict[str, float]:
        return {
            "fault_recall":                self.fault_recall,
            "healthy_false_positive_rate": self.healthy_false_positive_rate,
            "detection_rate":              self.detection_rate,
            "median_time_to_detect":       self.median_time_to_detect,
            "min_effective_rank":          self.min_effective_rank,
            "val_loss":                    self.val_loss,
            "train_seconds":               self.train_seconds,
        }

    def as_dict(self) -> Dict:
        return {
            "variant": self.variant,
            "seed": self.seed,
            "epochs_run": self.epochs_run,
            "headline": self.headline(),
            "fault_confusion": {k: v.as_dict() for k, v in self.fault_confusion.items()},
            "healthy_confusion": {k: v.as_dict() for k, v in self.healthy_confusion.items()},
            "detection_delay": dict(self.detection_delay),
            "effective_rank": dict(self.effective_rank),
            "separation": dict(self.separation),
        }


@dataclass
class Distribution:
    """A metric's spread across repeated runs of the same variant."""

    n: int
    median: float
    mean: float
    p25: float
    p75: float
    minimum: float
    maximum: float

    @classmethod
    def of(cls, values: Sequence[float]) -> "Distribution":
        finite = np.array([v for v in values if v is not None and math.isfinite(v)],
                          dtype=float)
        if finite.size == 0:
            nan = float("nan")
            return cls(0, nan, nan, nan, nan, nan, nan)
        return cls(
            n=int(finite.size),
            median=float(np.median(finite)),
            mean=float(finite.mean()),
            p25=float(np.percentile(finite, 25)),
            p75=float(np.percentile(finite, 75)),
            minimum=float(finite.min()),
            maximum=float(finite.max()),
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "n": self.n, "median": self.median, "mean": self.mean,
            "p25": self.p25, "p75": self.p75,
            "min": self.minimum, "max": self.maximum,
        }

    def __str__(self) -> str:
        if self.n == 0:
            return "n/a"
        return f"{self.median:.3f} [{self.p25:.3f}–{self.p75:.3f}]"


def aggregate(runs: Sequence[RunMetrics]) -> Dict[str, Distribution]:
    """Median and IQR per headline metric across repeated runs.

    Reported as a distribution rather than a point estimate on purpose: with
    ``RANDOM_SEED`` unset, run-to-run variance is large enough to reverse an
    architecture ranking, so a single run is not evidence of anything.
    """
    if not runs:
        return {}
    keys = runs[0].headline().keys()
    return {k: Distribution.of([r.headline()[k] for r in runs]) for k in keys}
