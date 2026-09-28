"""gnn — multi-architecture heterogeneous GNN for network anomaly detection.

Layout
──────
  gnn.core      framework-free: schema, snapshot→array conversion, normalization,
                quality filtering, splitting, Mahalanobis scoring, explanations.
                Imports **neither torch nor jax** — this is enforced by a test.
  gnn.data      Spanner reader (SCD Type 2 point-in-time snapshots).
  gnn.backends  thin adapters: `pyg` (PyTorch Geometric), `dgf` (JAX/Flax).
                Loaded lazily via `gnn.backends.load_backend(name)` so importing
                `gnn` never pulls in a deep-learning framework.

The "numpy sandwich": every backend converts to/from ``dict[node_type,
np.ndarray]`` at its edges, so everything before message passing (features,
scaling) and everything after it (Mahalanobis, thresholds, explanations,
Spanner writes) is shared, framework-free code.
"""

__all__ = ["core", "data", "backends"]
