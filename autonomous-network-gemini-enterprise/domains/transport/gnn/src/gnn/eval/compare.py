"""compare.py — run variants against the benchmark and rank them.

    python -m gnn.eval.compare --variants pyg.sage pyg.gat --repeats 5

What it holds fixed, and why it matters more than what it varies:

  * **one normalizer**, fitted once on the benchmark's training snapshots and
    handed to every variant. If each variant fitted its own, a difference in
    detection rate could come from the scaling rather than the architecture.
  * **one benchmark**, one split, one scoring path.
  * **n independent runs per variant**, compared as distributions. ``RANDOM_SEED``
    is unset by default because pinned seeds proved brittle, so a single run
    tells you about that run's initialisation, not about the architecture.

The output is a JSON leaderboard plus a Markdown table. The ranking key is
recall on fault windows, tie-broken by false-positive rate; validation loss is
reported but never ranked on, because it is not comparable across loss surfaces.
"""

import argparse
import json
import logging
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import numpy as np

from gnn.backends import load_backend
from gnn.config import CONFIG_ROOT, VariantConfig, load_variant
from gnn.core import scoring
from gnn.core.normalize import Normalizer
from gnn.core.snapshot import build_arrays
from gnn.eval import metrics
from gnn.eval.benchmark import Benchmark, Window, load_benchmark, synthetic_benchmark
from gnn.eval.metrics import ConfusionCounts, RunMetrics
from gnn.serve import score_snapshot
from gnn.train import train_from_snapshots

logger = logging.getLogger(__name__)


@dataclass
class VariantReport:
    """All repeats of one variant, plus their aggregate."""

    variant: str
    runs: List[RunMetrics]

    @property
    def aggregate(self) -> Dict[str, metrics.Distribution]:
        return metrics.aggregate(self.runs)

    def as_dict(self) -> Dict:
        return {
            "variant": self.variant,
            "repeats": len(self.runs),
            "aggregate": {k: v.as_dict() for k, v in self.aggregate.items()},
            "runs": [r.as_dict() for r in self.runs],
        }


# ── Scoring one window ────────────────────────────────────────────────────────

def _flags_for_window(
    model, backend, normalizer: Normalizer, window: Window,
    cluster_stats, mse_thresholds: Dict[str, float],
):
    """Score every snapshot in a window.

    Returns ``(flags, scores)`` where ``flags[i][node_type]`` is the set of node
    ids flagged in snapshot ``i``, and ``scores[node_type]`` accumulates
    ``(node_id, score)`` pairs across the window.
    """
    flags: List[Dict[str, Set[str]]] = []
    scores: Dict[str, List] = {}

    for snapshot in window.snapshots:
        arrays = normalizer.transform(build_arrays(snapshot))
        graph = backend.build_graph(arrays)
        result = score_snapshot(model, graph, arrays,
                                cluster_stats=cluster_stats,
                                mse_thresholds=mse_thresholds)

        per_snapshot: Dict[str, Set[str]] = {}
        for node_type, typed in result.per_type.items():
            ids = result.node_id_map.get(node_type, [])
            per_snapshot[node_type] = {
                ids[i] for i, flag in enumerate(typed.is_anomaly)
                if flag and i < len(ids)
            }
            scores.setdefault(node_type, []).extend(
                zip(ids, typed.scores.tolist())
            )
        flags.append(per_snapshot)

    return flags, scores


def _evaluate(
    model, backend, normalizer: Normalizer, benchmark: Benchmark,
    cluster_stats, mse_thresholds: Dict[str, float],
    run: RunMetrics,
) -> RunMetrics:
    """Fill ``run``'s detection metrics from the benchmark windows."""
    healthy_scores: Dict[str, List[float]] = {}
    faulty_scores: Dict[str, List[float]] = {}

    # ── healthy windows → false-positive rate ────────────────────────────────
    for window in benchmark.healthy:
        flags, scores = _flags_for_window(
            model, backend, normalizer, window, cluster_stats, mse_thresholds)
        for i, snapshot in enumerate(window.snapshots):
            for node_type, flagged in flags[i].items():
                scored = _ids_in(snapshot, node_type)
                run.healthy_confusion.setdefault(node_type, ConfusionCounts())
                run.healthy_confusion[node_type] += metrics.confusion_for_snapshot(
                    flagged, scored, set())
        for node_type, pairs in scores.items():
            healthy_scores.setdefault(node_type, []).extend(s for _, s in pairs)

    # ── fault windows → recall and time-to-detect ────────────────────────────
    for window in benchmark.faults:
        flags, scores = _flags_for_window(
            model, backend, normalizer, window, cluster_stats, mse_thresholds)

        for i, snapshot in enumerate(window.snapshots):
            # Before onset the window is healthy, so a flag there is a false
            # positive, not a lucky early detection.
            truth_at_i = window.faulty_nodes if i >= window.onset_index else {}
            for node_type, flagged in flags[i].items():
                scored = _ids_in(snapshot, node_type)
                faulty = set(truth_at_i.get(node_type, ()))
                run.fault_confusion.setdefault(node_type, ConfusionCounts())
                run.fault_confusion[node_type] += metrics.confusion_for_snapshot(
                    flagged, scored, faulty)

        merged = [set().union(*f.values()) if f else set() for f in flags]
        run.detection_delay[window.name] = metrics.time_to_detect(
            merged, window.all_faulty_ids, window.onset_index)

        for node_type, pairs in scores.items():
            faulty_ids = window.faulty_ids(node_type)
            for node_id, value in pairs:
                bucket = faulty_scores if node_id in faulty_ids else healthy_scores
                bucket.setdefault(node_type, []).append(value)

    for node_type in set(healthy_scores) | set(faulty_scores):
        run.separation[node_type] = metrics.separation(
            np.array(healthy_scores.get(node_type, []), dtype=float),
            np.array(faulty_scores.get(node_type, []), dtype=float),
        )

    # ── embedding health, pooled over every healthy snapshot ─────────────────
    #
    # Pooled, not per-snapshot: one snapshot holds only a handful of routers, so
    # its embedding matrix is rank-limited by node count and would read as
    # "collapsed" for any model. What matters is whether the latent space stays
    # spread out across the whole healthy population — that is the covariance
    # the Mahalanobis threshold is actually built on.
    pooled: Dict[str, List[np.ndarray]] = {}
    for window in benchmark.healthy:
        for snapshot in window.snapshots:
            arrays = normalizer.transform(build_arrays(snapshot))
            for node_type, emb in model.embed(backend.build_graph(arrays)).items():
                if emb.size:
                    pooled.setdefault(node_type, []).append(emb)
    for node_type, chunks in pooled.items():
        run.effective_rank[node_type] = metrics.effective_rank(np.vstack(chunks))

    return run


def _ids_in(snapshot: Dict, node_type: str) -> Set[str]:
    return {n.get("id") for n in snapshot.get("nodes", []) if n.get("type") == node_type}


# ── Running a variant ─────────────────────────────────────────────────────────

def run_variant(
    config: VariantConfig,
    benchmark: Benchmark,
    normalizer: Normalizer,
    repeats: Optional[int] = None,
    workdir: Optional[Path] = None,
) -> VariantReport:
    """Train ``config`` ``repeats`` times and score each run on the benchmark."""
    backend = load_backend(config.backend, **config.train.backend_options)
    repeats = repeats if repeats is not None else config.repeats
    runs: List[RunMetrics] = []

    for i in range(repeats):
        logger.info("── %s: run %d/%d ──", config.variant, i + 1, repeats)
        started = time.perf_counter()

        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            bundle = Path(tmp)
            result, _, cluster_stats = train_from_snapshots(
                benchmark.train_snapshots,
                output_dir=bundle,
                variant=config.variant,
                config=config.train,
                val_fraction=config.validation_split,
                normalizer=normalizer,     # fixed across variants — the whole point
            )
            elapsed = time.perf_counter() - started

            run = RunMetrics(
                variant=config.variant,
                seed=config.train.seed,
                val_loss=float(result.best_val_loss),
                epochs_run=result.epochs_run,
                train_seconds=elapsed,
            )
            _evaluate(result.model, backend, normalizer, benchmark,
                      cluster_stats, config.mse_thresholds, run)

        head = run.headline()
        logger.info(
            "   recall=%.3f fpr=%.4f detected=%.2f ttd=%s rank=%.1f val_loss=%.4f (%.1fs)",
            head["fault_recall"], head["healthy_false_positive_rate"],
            head["detection_rate"], head["median_time_to_detect"],
            head["min_effective_rank"], head["val_loss"], elapsed,
        )
        runs.append(run)

    return VariantReport(variant=config.variant, runs=runs)


def compare(
    variants: Sequence[str],
    benchmark: Optional[Benchmark] = None,
    repeats: Optional[int] = None,
    config_root: Path = CONFIG_ROOT,
    overrides: Optional[Dict] = None,
    workdir: Optional[Path] = None,
) -> List[VariantReport]:
    """Run every variant against one benchmark with one shared normalizer."""
    benchmark = benchmark or synthetic_benchmark()
    logger.info("Benchmark: %s", benchmark.summary())

    configs = [load_variant(v, config_root, overrides) for v in variants]

    # Fitted once, before any backend loads, and reused verbatim by every
    # variant. Refitting per variant would add a second uncontrolled axis.
    logger.info("Fitting the shared normalizer on the benchmark training set...")
    normalizer = Normalizer.fit(benchmark.train_snapshots)

    return [run_variant(c, benchmark, normalizer, repeats, workdir) for c in configs]


# ── Reporting ─────────────────────────────────────────────────────────────────

_RANK_KEY = "fault_recall"
_TIE_KEY = "healthy_false_positive_rate"


def leaderboard(reports: Sequence[VariantReport]) -> List[VariantReport]:
    """Best first: highest median recall, then lowest median false-positive rate."""
    def key(report: VariantReport):
        agg = report.aggregate
        recall = agg[_RANK_KEY].median
        fpr = agg[_TIE_KEY].median
        return (
            -(recall if np.isfinite(recall) else -1.0),
            fpr if np.isfinite(fpr) else float("inf"),
        )
    return sorted(reports, key=key)


def to_markdown(reports: Sequence[VariantReport]) -> str:
    """A leaderboard table showing median [IQR] per metric."""
    columns = [
        ("fault_recall", "recall ↑"),
        ("healthy_false_positive_rate", "FPR ↓"),
        ("detection_rate", "detected ↑"),
        ("median_time_to_detect", "TTD ↓"),
        ("min_effective_rank", "eff. rank ↑"),
        ("val_loss", "val_loss*"),
        ("train_seconds", "sec/run"),
    ]
    lines = [
        "| variant | n | " + " | ".join(label for _, label in columns) + " |",
        "|---|---|" + "---|" * len(columns),
    ]
    for report in leaderboard(reports):
        agg = report.aggregate
        cells = [str(agg[key]) for key, _ in columns]
        lines.append(f"| `{report.variant}` | {len(report.runs)} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Cells are median [p25–p75] over independent runs.")
    lines.append(
        "\\* val_loss is reported for information only. Different architectures "
        "optimise different loss surfaces, so it is not comparable across rows "
        "and is never the ranking key."
    )
    return "\n".join(lines)


def write_report(reports: Sequence[VariantReport], output_dir: Path) -> Dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "leaderboard.json"
    md_path = output_dir / "leaderboard.md"

    json_path.write_text(json.dumps(
        {"ranking": [r.variant for r in leaderboard(reports)],
         "ranked_on": _RANK_KEY,
         "tie_break": _TIE_KEY,
         "reports": [r.as_dict() for r in reports]},
        indent=2,
    ))
    md_path.write_text(to_markdown(reports) + "\n")
    logger.info("Wrote %s and %s", json_path, md_path)
    return {"json": json_path, "markdown": md_path}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--variants", nargs="+", default=["pyg.sage", "pyg.gat"],
                        help="variant names, e.g. pyg.sage pyg.gat")
    parser.add_argument("--benchmark", type=Path, default=None,
                        help="benchmark directory; omit to use the synthetic stand-in")
    parser.add_argument("--repeats", type=int, default=None,
                        help="runs per variant (default: benchmark.repeats from base.yaml)")
    parser.add_argument("--epochs", type=int, default=None,
                        help="override training epochs, for quick smoke runs")
    parser.add_argument("--output", type=Path, default=Path("eval_output"))
    parser.add_argument("--config-root", type=Path, default=CONFIG_ROOT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("gnn.core.normalize").setLevel(logging.WARNING)
    logging.getLogger("gnn.serve.inference").setLevel(logging.WARNING)

    if args.benchmark:
        bench = load_benchmark(args.benchmark)
    else:
        logger.warning(
            "No --benchmark given: using the synthetic stand-in. Its faults are "
            "crude by construction, so treat the result as a harness smoke test "
            "and not as evidence about architectures."
        )
        bench = synthetic_benchmark()

    overrides = {"train": {"epochs": args.epochs}} if args.epochs else None
    reports = compare(args.variants, bench, args.repeats, args.config_root, overrides)

    print("\n" + to_markdown(reports))
    write_report(reports, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
