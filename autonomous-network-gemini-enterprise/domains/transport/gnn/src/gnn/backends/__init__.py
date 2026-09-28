"""gnn.backends — lazily-resolved model backends.

Importing this package must **not** import torch or jax. The two backends have
incompatible dependency trees (and DGF ships manylinux x86-64 wheels only), so
a container built for one must not fail at import because the other's framework
is absent. Resolution happens in ``load_backend``, at which point exactly one
framework is pulled in.
"""

import importlib
from typing import Dict, Tuple

from gnn.backends.base import Backend, TrainConfig, TrainedModel, TrainResult

# backend name → "module:attribute"
_BACKENDS: Dict[str, str] = {
    "pyg": "gnn.backends.pyg:PyGBackend",
    "dgf": "gnn.backends.dgf:DGFBackend",
}

# Extra to install if the backend's framework is missing.
_EXTRAS: Dict[str, str] = {"pyg": "gnn-core[pyg]", "dgf": "gnn-core[dgf]"}


def available_backends() -> Tuple[str, ...]:
    """Names that ``load_backend`` will accept — not whether they're installed."""
    return tuple(_BACKENDS)


def load_backend(name: str, **options) -> Backend:
    """Instantiate a backend by name (``"pyg"`` or ``"dgf"``).

    ``options`` are framework-level settings from the variant config's
    backend block — DGF's padding margins, for instance. An unrecognised option
    raises rather than being ignored, so a stale or misspelled key in a config
    file fails at startup instead of silently having no effect.
    """
    try:
        target = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"unknown backend {name!r}; expected one of {sorted(_BACKENDS)}"
        ) from None
    module_name, attr = target.split(":")
    try:
        # getattr is inside the guard too: a backend package may import its
        # framework lazily on attribute access (dgf does), so the missing
        # dependency surfaces here rather than at import_module.
        backend = getattr(importlib.import_module(module_name), attr)
    except ImportError as exc:
        raise ImportError(
            f"backend {name!r} is not installed: {exc}. "
            f"Install it with `pip install {_EXTRAS.get(name, name)}`."
        ) from exc
    try:
        return backend(**options)
    except TypeError as exc:
        raise TypeError(
            f"backend {name!r} rejected options {sorted(options)}: {exc}"
        ) from exc


def parse_variant(variant: str) -> Tuple[str, str]:
    """Split a ``"<backend>.<arch>"`` string, e.g. ``"pyg.gat"`` → ``("pyg", "gat")``."""
    backend, sep, arch = variant.partition(".")
    if not sep or not arch:
        raise ValueError(
            f"variant must be '<backend>.<arch>' (e.g. 'pyg.sage'), got {variant!r}"
        )
    return backend, arch


__all__ = [
    "Backend", "TrainedModel", "TrainConfig", "TrainResult",
    "load_backend", "available_backends", "parse_variant",
]
