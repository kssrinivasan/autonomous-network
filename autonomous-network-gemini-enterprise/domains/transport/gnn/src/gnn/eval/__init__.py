"""gnn.eval — the benchmark, the metrics, and the A/B runner.

Importing ``gnn.eval.benchmark`` or ``gnn.eval.metrics`` pulls in no deep
learning framework, so a benchmark can be built, validated and inspected
without torch or jax installed. ``gnn.eval.compare`` does load a backend,
because it trains — import it only when running an A/B.
"""

from gnn.eval.benchmark import Benchmark, Window, load_benchmark, synthetic_benchmark
from gnn.eval.metrics import ConfusionCounts, Distribution, RunMetrics, aggregate

__all__ = [
    "Benchmark", "Window", "load_benchmark", "synthetic_benchmark",
    "RunMetrics", "ConfusionCounts", "Distribution", "aggregate",
]
