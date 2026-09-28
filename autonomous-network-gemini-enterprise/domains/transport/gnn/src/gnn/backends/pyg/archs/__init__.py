"""Message-passing architectures for the PyG backend.

Each module exposes a ``SPEC`` describing how to build one convolution for one
edge type. Everything else about the model — input projections, per-type
decoders, dropout placement, the residual "keep previous state for nodes with
no incoming edges" rule — is shared in ``gnn.backends.pyg.model``, so adding an
architecture is a factory function, not a second model.
"""

from typing import Dict

from gnn.backends.pyg.archs.base import ArchSpec
from gnn.backends.pyg.archs import gat, sage

SPECS: Dict[str, ArchSpec] = {
    sage.SPEC.name: sage.SPEC,
    gat.SPEC.name: gat.SPEC,
}


def get_spec(name: str) -> ArchSpec:
    try:
        return SPECS[name]
    except KeyError:
        raise ValueError(
            f"unknown PyG architecture {name!r}; expected one of {sorted(SPECS)}"
        ) from None


__all__ = ["ArchSpec", "SPECS", "get_spec"]
