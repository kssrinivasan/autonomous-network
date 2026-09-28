"""gnn.backends.dgf — Google Distributed Graph Flow (JAX/Flax) backend.

Supports the ``hetconv`` and ``gat`` architectures.

Importing this package is **cheap and framework-free**; jax, flax and dgf are
pulled in only when :class:`DGFBackend` or :class:`DGFTrainedModel` is actually
touched, via the module ``__getattr__`` below. That is not just tidiness: it
means ``gnn.backends.dgf.schema`` — the PyG-to-DGF edge-set mapping, which is
pure table lookup and the easiest thing here to get wrong — can be imported and
tested on a machine where DGF cannot be installed at all, which includes every
Apple silicon Mac. Making the mapping untestable off-Linux would have been a
poor trade for one less file.

``load_backend("dgf")`` resolves ``DGFBackend`` through this same attribute
hook, so nothing downstream needs to know about the split.

**Platform note.** DGF publishes manylinux x86-64 wheels only — the package is
19 compiled x86-64 ELF shared libraries alongside its Python, with no sdist to
build from, so it cannot be installed on an Apple silicon Mac at all. Run it on
a Linux x86-64 host or via ``gnn/docker/Dockerfile.dgf``.
"""

from typing import Any

#: Attributes served lazily from ``gnn.backends.dgf.backend``.
_LAZY = {"DGFBackend", "DGFTrainedModel", "HetAutoencoder",
         "REQUIRED_DGF_MODULES"}

__all__ = sorted(_LAZY) + ["schema"]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from gnn.backends.dgf import backend

        return getattr(backend, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LAZY)
