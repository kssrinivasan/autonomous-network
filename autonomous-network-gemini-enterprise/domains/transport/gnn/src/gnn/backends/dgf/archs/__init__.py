"""archs — the message-passing blocks the DGF backend can build.

Mirrors ``gnn.backends.pyg.archs``: one ``ArchSpec`` per architecture, holding a
factory and its overridable defaults. ``TrainConfig.arch_kwargs`` is validated
against ``defaults``, so a typo in a config file fails loudly at construction
instead of being silently ignored.

DGF's two heterogeneous layers share a config surface, so ``hetconv`` and
``gat`` differ only in the config class and ``num_heads`` — which is precisely
what makes them a fair A/B pair.
"""

from typing import Dict

from gnn.backends.dgf.archs.base import ArchSpec
from gnn.backends.dgf.archs.gat import SPEC as _GAT
from gnn.backends.dgf.archs.hetconv import SPEC as _HETCONV

SPECS: Dict[str, ArchSpec] = {
    _HETCONV.name: _HETCONV,
    _GAT.name: _GAT,
}


def get_spec(name: str) -> ArchSpec:
    try:
        return SPECS[name]
    except KeyError:
        raise ValueError(
            f"unknown dgf architecture '{name}'; available: {sorted(SPECS)}"
        ) from None


__all__ = ["ArchSpec", "SPECS", "get_spec"]
