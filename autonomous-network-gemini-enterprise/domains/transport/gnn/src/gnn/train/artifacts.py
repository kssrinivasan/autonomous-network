"""artifacts.py — the trained-model bundle.

A bundle is a directory containing:

  ``model.pth``        backend-owned weights (see ``backends/*/checkpoint.py``)
  ``scalers.pkl``      the fitted ``Normalizer``
  ``model_stats.pth``  healthy-embedding cluster statistics, per-node stats,
                       fallback MSE thresholds, feature dims, and the variant
                       that produced them

The three filenames are load-bearing: Vertex AI uploads whatever is in
``AIP_MODEL_DIR``, ``serve.py`` downloads ``*.pth`` / ``*.pkl`` by suffix, and
the local monitor pulls ``model_stats.pth`` from the deployed model's artefact
directory. Renaming any of them is a coordinated change, not a cleanup.

``model_stats.pth`` is still written with ``torch.save`` even though its
contents are now plain numpy. That is purely so the local monitor
(``tests/local_monitor/data.py``) keeps loading it; ``load_stats`` already
accepts both formats, so the writer can switch to pickle as soon as that reader
goes through this module.
"""

import logging
import pickle
from pathlib import Path
from typing import Dict, Optional

from gnn.core.normalize import Normalizer
from gnn.core.schema import ANOMALY_THRESHOLDS, FEATURE_DIMS
from gnn.core.scoring import ClusterStats

logger = logging.getLogger(__name__)

MODEL_FILENAME = "model.pth"
SCALERS_FILENAME = "scalers.pkl"
STATS_FILENAME = "model_stats.pth"

BUNDLE_FILES = (MODEL_FILENAME, SCALERS_FILENAME, STATS_FILENAME)


def save_stats(
    bundle_dir: Path,
    cluster_stats: Dict[str, ClusterStats],
    node_stats: Optional[Dict] = None,
    variant: str = "pyg.sage",
) -> Path:
    """Write ``model_stats.pth``."""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / STATS_FILENAME

    payload = {
        "cluster_stats":      {k: v.to_artifact() for k, v in cluster_stats.items()},
        "node_stats":         node_stats or {},
        "anomaly_thresholds": dict(ANOMALY_THRESHOLDS),
        "feature_dims":       dict(FEATURE_DIMS),
        "variant":            variant,
    }

    try:
        import torch

        torch.save(payload, path)
    except ImportError:
        with path.open("wb") as fh:
            pickle.dump(payload, fh, protocol=4)

    logger.info("Saved cluster statistics to %s", path)
    return path


def load_stats(path: Path) -> Dict:
    """Read ``model_stats.pth``, whether it was written by torch or by pickle."""
    path = Path(path)
    try:
        import torch

        return torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception:
        with path.open("rb") as fh:
            return pickle.load(fh)


def load_cluster_stats(payload: Dict) -> Dict[str, ClusterStats]:
    """Extract usable ``ClusterStats`` from a loaded stats payload.

    Node types whose entry is malformed are skipped with a warning rather than
    failing the load — scoring falls back to reconstruction MSE for those, which
    is degraded but still serves.
    """
    out: Dict[str, ClusterStats] = {}
    for node_type, blob in (payload.get("cluster_stats") or {}).items():
        if not isinstance(blob, dict):
            continue
        try:
            out[node_type] = ClusterStats.from_artifact(blob)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "cluster_stats[%s] unusable (%s); falling back to MSE scoring "
                "for this node type.", node_type, exc,
            )
    return out


def save_normalizer(bundle_dir: Path, normalizer: Normalizer) -> Path:
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / SCALERS_FILENAME
    normalizer.save(path)
    logger.info("Scalers saved to %s", path)
    return path


def load_normalizer(bundle_dir: Path) -> Normalizer:
    path = Path(bundle_dir) / SCALERS_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{SCALERS_FILENAME} not found at {path}")
    return Normalizer.load(path)
