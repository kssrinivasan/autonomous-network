"""quality.py — drop snapshots that are data-collection artifacts.

The collector occasionally emits a snapshot where a whole node type's metrics
are missing or wildly off (agent restart, partial Spanner write, clock skew).
Those look like network-wide anomalies to an autoencoder and poison both the
scalers and the healthy-embedding cluster statistics.

The filter is deliberately crude: compute each snapshot's mean for a handful of
load-bearing fields, then drop any snapshot where one of those means sits more
than ``z_thresh`` population-σ from the dataset mean. It targets whole-snapshot
corruption, not individual node faults — a genuine single-router fault barely
moves a per-type mean.
"""

import logging
import statistics
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# Fields whose per-snapshot mean is a reliable corruption signal.
FILTER_FIELDS: Dict[str, List[str]] = {
    "router":    ["cpu", "mem", "ospf_num_routes", "pfx_count_norm"],
    "interface": ["rx_bytes_rate", "tx_bytes_rate", "tx_util", "rx_util", "mtu_norm"],
}

Z_THRESH = 4.0


def drop_outlier_snapshots(
    snapshots: Sequence[Dict],
    z_thresh: float = Z_THRESH,
    filter_fields: Dict[str, List[str]] = None,
) -> Tuple[List[Dict], int]:
    """Return ``(kept_snapshots, num_dropped)``, preserving input order."""
    filter_fields = filter_fields or FILTER_FIELDS

    # Pass 1 — per-snapshot mean for each (node type, field).
    snap_means: List[Dict[Tuple[str, str], float]] = []
    accum = defaultdict(list)
    for snap in snapshots:
        means: Dict[Tuple[str, str], float] = {}
        for ntype, fields in filter_fields.items():
            nodes = [n for n in snap.get("nodes", []) if n.get("type") == ntype]
            if not nodes:
                continue
            for field in fields:
                vals = [n.get(field, 0.0) for n in nodes if n.get(field) is not None]
                if vals:
                    m = sum(vals) / len(vals)
                    means[(ntype, field)] = m
                    accum[(ntype, field)].append(m)
        snap_means.append(means)

    # Pass 2 — dataset-level mean/σ, then flag outliers.
    global_stats = {}
    for key, means in accum.items():
        if len(means) > 1:
            global_stats[key] = (sum(means) / len(means), statistics.pstdev(means))

    kept: List[Dict] = []
    n_dropped = 0
    for snap, means in zip(snapshots, snap_means):
        offender = None
        for key, val in means.items():
            mu, sigma = global_stats.get(key, (val, 0.0))
            if sigma > 0 and abs(val - mu) / sigma > z_thresh:
                offender = (key, val, mu, sigma)
                break
        if offender is None:
            kept.append(snap)
            continue
        n_dropped += 1
        key, val, mu, sigma = offender
        logger.warning(
            "Dropped bad snapshot %s (feature '%s' mean=%.4f, z=%.1fσ)",
            snap.get("timestamp"), key, val, (val - mu) / sigma,
        )

    if n_dropped:
        logger.info("Filtered %d bad snapshot(s) — %d remaining", n_dropped, len(kept))
    return kept, n_dropped
