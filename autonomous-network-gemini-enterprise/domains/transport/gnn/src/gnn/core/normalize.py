"""normalize.py — per-node-type feature scaling.

Wraps the ``{node_type: sklearn.StandardScaler}`` dict that has always been
persisted as ``scalers.pkl``. The on-disk format is unchanged: ``save`` writes
the bare dict and ``load`` accepts a bare dict, so existing artefacts and any
consumer that joblib-loads ``scalers.pkl`` directly keep working.

Only the leading continuous block of each row is scaled — see
``core.schema.scaled_slice``. Router role one-hots pass through untouched.

Holding the normalizer **fixed** across model variants is what makes an A/B
comparison honest, which is why fitting is a separate step from graph building
rather than something a backend does internally.
"""

import logging
from typing import Dict, Iterable, List, Optional

import numpy as np
from sklearn.preprocessing import StandardScaler

from gnn.core import schema
from gnn.core.schema import FLOW_TRAFFIC_COLS, NODE_TYPES
from gnn.core.snapshot import SnapshotArrays

logger = logging.getLogger(__name__)


class Normalizer:
    """Applies one fitted ``StandardScaler`` per node type."""

    def __init__(self, scalers: Optional[Dict[str, StandardScaler]] = None):
        self.scalers: Dict[str, StandardScaler] = dict(scalers or {})

    # ── fitting ──────────────────────────────────────────────────────────────

    @classmethod
    def fit(cls, snapshots: Iterable[Dict]) -> "Normalizer":
        """Fit one scaler per node type over raw snapshot dicts.

        Fit on **training snapshots only** — the caller splits first, otherwise
        validation statistics leak into the scaling.

        Flows whose traffic metrics are all zero are excluded: they are
        inactive or unmeasured, and including them drags the mean toward zero
        and inflates the variance for every real flow.
        """
        accumulators: Dict[str, List[List[float]]] = {t: [] for t in NODE_TYPES}
        zero_flow_count = 0

        for snap in snapshots:
            for node in snap.get("nodes", []):
                ntype = node.get("type")
                if ntype not in accumulators:
                    continue
                row = schema.continuous_row(node, ntype)
                if ntype == "flow" and sum(row[:FLOW_TRAFFIC_COLS]) < 1e-6:
                    zero_flow_count += 1
                    logger.debug(
                        "Skipping zero-metric flow %s during scaler fit", node.get("id")
                    )
                    continue
                accumulators[ntype].append(row)

        if zero_flow_count:
            logger.info("Filtered %d zero-metric flows from scaler training", zero_flow_count)

        scalers: Dict[str, StandardScaler] = {}
        for ntype, rows in accumulators.items():
            if not rows:
                logger.warning("No data to fit scaler for node type '%s'", ntype)
                scalers[ntype] = StandardScaler()
                continue
            scaler = StandardScaler()
            scaler.fit(np.array(rows, dtype=np.float32))
            scalers[ntype] = scaler
            logger.info("Fitted scaler for '%s' on %d node observations", ntype, len(rows))

        return cls(scalers)

    # ── applying ─────────────────────────────────────────────────────────────

    def transform_matrix(self, node_type: str, features: np.ndarray) -> np.ndarray:
        """Scale the continuous block of a ``[N, F]`` feature matrix.

        Returns a new float32 array; ``features`` is left unmodified. A node
        type with no fitted scaler passes through unchanged.

        ``fit`` stores a bare, *unfitted* ``StandardScaler`` for any node type
        the training set never contained — most plausibly ``flow``, whose rows
        are dropped when all traffic metrics are zero. Calling ``.transform``
        on one raises ``NotFittedError``, so treat it the same as an absent
        scaler: pass the features through. Unscaled features are worse than
        scaled ones, but they are a great deal better than a crashed run.
        """
        scaler = self.scalers.get(node_type)
        if scaler is None or features.shape[0] == 0:
            return features
        if not hasattr(scaler, "mean_"):
            logger.warning(
                "Scaler for '%s' was never fitted — passing features through unscaled",
                node_type,
            )
            return features
        out = features.copy()
        cols = schema.scaled_slice(node_type)
        out[:, cols] = scaler.transform(out[:, cols]).astype(np.float32)
        return out

    def transform(self, arrays: SnapshotArrays) -> SnapshotArrays:
        """Return a copy of ``arrays`` with every feature matrix scaled."""
        return SnapshotArrays(
            features={t: self.transform_matrix(t, x) for t, x in arrays.features.items()},
            node_ids=arrays.node_ids,
            edges=arrays.edges,
            timestamp=arrays.timestamp,
            raw_nodes=arrays.raw_nodes,
        )

    # ── persistence ──────────────────────────────────────────────────────────

    def save(self, path) -> None:
        """Write the bare ``{node_type: StandardScaler}`` dict to ``path``."""
        import joblib

        joblib.dump(self.scalers, str(path))

    @classmethod
    def load(cls, path_or_scalers) -> "Normalizer":
        """Load from a ``scalers.pkl`` path, a bare dict, or another Normalizer."""
        if isinstance(path_or_scalers, Normalizer):
            return path_or_scalers
        if isinstance(path_or_scalers, dict):
            return cls(path_or_scalers)
        import joblib

        return cls(joblib.load(str(path_or_scalers)))

    def __repr__(self) -> str:
        return f"Normalizer(node_types={sorted(self.scalers)})"
