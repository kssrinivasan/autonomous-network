"""split.py — train/validation splitting.

The split is **chronological, not random**: snapshots arrive as a time series
and adjacent snapshots are near-duplicates, so a shuffled split leaks the
validation set into training and reports a val loss that means nothing. Train
on the earlier window, validate on the later one.

Split before fitting the normalizer, so validation statistics don't leak into
the scaling either.
"""

import logging
from typing import List, Sequence, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

VALIDATION_SPLIT = 0.2


def chronological_split(
    snapshots: Sequence[T], val_fraction: float = VALIDATION_SPLIT
) -> Tuple[List[T], List[T]]:
    """Split an ordered snapshot sequence into ``(train, val)``.

    ``snapshots`` must already be in ascending time order — which is how
    ``SpannerDataset._get_timestamps()`` returns them.
    """
    if not 0.0 <= val_fraction < 1.0:
        raise ValueError(f"val_fraction must be in [0, 1), got {val_fraction}")
    split_idx = int(len(snapshots) * (1 - val_fraction))
    train, val = list(snapshots[:split_idx]), list(snapshots[split_idx:])
    logger.info(
        "Dataset split: %d training, %d validation snapshots", len(train), len(val)
    )
    return train, val
