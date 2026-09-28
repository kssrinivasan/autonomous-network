"""checkpoint.py — persistence for the DGF backend.

The skill's reference recommends a four-file artefact — orbax ``params/`` plus
``schema.json``, ``normalizer.json`` and ``padding.json``. We keep all four
*pieces of information* but pack the three model-side ones into the existing
``model.pth``, and the normalizer stays in ``scalers.pkl`` where the PyG backend
already puts it. The result is a bundle with exactly the same three files
whatever the backend:

    model.pth        weights + schema + padding + architecture
    scalers.pkl      the fitted Normalizer (shared, backend-agnostic)
    model_stats.pth  cluster statistics + variant

That matters because the bundle contract is load-bearing outside this package:
Vertex AI uploads whatever is in ``AIP_MODEL_DIR``, ``serve.py`` downloads
``*.pth``/``*.pkl`` **by suffix**, and the local monitor reads
``model_stats.pth`` directly. A DGF bundle that added ``.json`` sidecars would
train and upload fine and then silently lose them on the way to the serving
container — the model would fail to load with a shape mismatch, at deploy time,
with nothing in the training logs pointing at the cause.

Weights are Flax's msgpack rather than orbax for the same reason: orbax writes a
*directory*, which the suffix-based download drops. ``msgpack_restore`` also
needs no target pytree, so loading doesn't require initialising a throwaway
model against a dummy graph first. Move to orbax when training runs long enough
that mid-run preemption recovery matters; that is a change to the upload path
too, not just to this file.
"""

import logging
import pickle
from pathlib import Path
from typing import Any, Tuple

import numpy as np

from gnn.backends.base import TrainConfig
from gnn.backends.dgf.graph import padding_from_dict, padding_to_dict
from gnn.core.schema import NODE_TYPES

logger = logging.getLogger(__name__)

MODEL_FILENAME = "model.pth"

#: Bumped when the payload layout changes incompatibly.
FORMAT_VERSION = 1


def _params_to_bytes(params) -> bytes:
    """Flax params → msgpack, with every leaf materialised as numpy.

    Leaves must be concrete before serialisation: a leaf still on an accelerator
    serialises to something the loading process may not be able to place.
    """
    import jax
    from flax import serialization

    return serialization.msgpack_serialize(
        jax.tree_util.tree_map(lambda leaf: np.asarray(leaf), params)
    )


def _params_from_bytes(payload: bytes):
    from flax import serialization

    return serialization.msgpack_restore(payload)


def save_model(params, bundle_dir: Path, config: TrainConfig, schema, padding,
               plan, val_loss: float) -> Path:
    """Write ``bundle_dir/model.pth``."""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / MODEL_FILENAME

    payload = {
        "format_version":  FORMAT_VERSION,
        "framework":       "dgf",
        "params_msgpack":  _params_to_bytes(params),
        "schema_json":     schema.to_json(),
        "padding":         padding_to_dict(padding),
        # Plan tuples become lists through JSON/pickle round-trips in some
        # readers; normalise to lists here and rebuild tuples on load.
        "plan":            [[name, bool(reversed_)] for name, reversed_ in plan],
        "hidden_channels": config.hidden_channels,
        "num_layers":      config.num_layers,
        "dropout":         config.dropout,
        "arch":            config.arch,
        "arch_kwargs":     dict(config.arch_kwargs),
        "node_types":      list(NODE_TYPES),
        "val_loss":        float(val_loss),
    }

    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=4)

    logger.info("Saved model to %s (arch=%s, val_loss=%.4f)",
                path, config.arch, val_loss)
    return path


def load_model(bundle_dir: Path) -> Tuple[Any, TrainConfig, Any, Any, list]:
    """Load ``bundle_dir/model.pth``.

    Returns ``(params, config, schema, padding, plan)`` — everything needed to
    rebuild the model without touching the data source. Serving that re-derives
    schema or padding from a live snapshot drifts the moment the topology
    changes, and cannot load at all when Spanner is unreachable.
    """
    from dgf.data import GraphSchema

    path = Path(bundle_dir) / MODEL_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{MODEL_FILENAME} not found at {path}")

    try:
        with path.open("rb") as fh:
            payload = pickle.load(fh)
    except pickle.UnpicklingError as exc:
        # torch.save writes a zip archive, so a PyG bundle fails here rather
        # than at the framework check below. Say what actually happened.
        raise ValueError(
            f"{path} is not a DGF bundle — it looks like a torch checkpoint "
            "from the pyg backend. Check the `variant` recorded in "
            "model_stats.pth."
        ) from exc

    framework = payload.get("framework")
    if framework != "dgf":
        raise ValueError(
            f"{path} was written by the '{framework or 'pyg'}' backend, not dgf "
            "— check the `variant` recorded in model_stats.pth"
        )
    version = payload.get("format_version", 0)
    if version > FORMAT_VERSION:
        raise ValueError(
            f"{path} uses bundle format v{version}; this code understands "
            f"v{FORMAT_VERSION}. Upgrade the serving image."
        )

    config = TrainConfig(
        arch=payload.get("arch", "hetconv"),
        hidden_channels=payload.get("hidden_channels", 64),
        num_layers=payload.get("num_layers", 2),
        # Irrelevant at inference (dropout is disabled) but recorded so a
        # reloaded model can be fine-tuned identically.
        dropout=payload.get("dropout", 0.0),
        arch_kwargs=dict(payload.get("arch_kwargs") or {}),
    )

    params = _params_from_bytes(payload["params_msgpack"])
    schema = GraphSchema.from_json(payload["schema_json"])
    padding = padding_from_dict(payload["padding"])
    plan = [(name, bool(reversed_)) for name, reversed_ in payload["plan"]]

    logger.info("Loaded model from %s (arch=%s)", path, config.arch)
    return params, config, schema, padding, plan


__all__ = ["MODEL_FILENAME", "FORMAT_VERSION", "save_model", "load_model"]
