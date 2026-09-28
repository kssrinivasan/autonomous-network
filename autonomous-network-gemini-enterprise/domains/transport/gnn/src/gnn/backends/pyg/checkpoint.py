"""checkpoint.py — persistence for the PyG backend.

The on-disk format is the same ``model.pth`` dict the pipeline and serving path
have always read, with two additions: ``arch`` and ``arch_kwargs``, so a bundle
identifies which architecture produced it instead of everything being assumed
to be GraphSAGE. Checkpoints written before this refactor have neither key and
load as ``sage`` with no options — which is what they are.

``edge_types`` is also recorded now. Serving previously rebuilt the metadata by
fetching a live Spanner snapshot at load time, which made model loading depend
on the database being reachable and on the live topology still containing every
relation the model was trained on.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import torch

from gnn.backends.base import TrainConfig
from gnn.backends.pyg.model import HetGNN
from gnn.core.schema import EDGE_TYPES, NODE_TYPES

logger = logging.getLogger(__name__)

MODEL_FILENAME = "model.pth"


def save_model(model: HetGNN, bundle_dir: Path, config: TrainConfig,
               val_loss: float) -> Path:
    """Write ``bundle_dir/model.pth``."""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / MODEL_FILENAME

    # Every layer holds the same relation set, so layer 0 is representative.
    edge_types = _hetero_edge_types(model.convs[0]) if len(model.convs) else []

    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "hidden_channels":  model.hidden_channels,
            "out_channels":     model.out_channels,
            "num_layers":       model.num_layers,
            "dropout":          config.dropout,
            "val_loss":         val_loss,
            "arch":             model.arch,
            "arch_kwargs":      model.arch_kwargs,
            "node_types":       list(NODE_TYPES),
            "edge_types":       edge_types,
        },
        path,
    )
    logger.info("Saved model to %s (arch=%s, val_loss=%.4f)", path, model.arch, val_loss)
    return path


def _hetero_edge_types(hetero_conv):
    """Edge-type triples a ``HeteroConv`` holds, in registration order.

    Older PyG mangles the triple into a ``'src__rel__dst'`` ModuleDict key;
    2.8 keeps the tuple. Accept either so the checkpoint records the same
    thing regardless of which version wrote it.
    """
    return [
        tuple(key) if isinstance(key, tuple) else tuple(key.split("__"))
        for key in hetero_conv.convs.keys()
    ]


def load_model(
    bundle_dir: Path,
    metadata=None,
    input_dims: Optional[Dict[str, int]] = None,
) -> Tuple[HetGNN, TrainConfig]:
    """Load ``bundle_dir/model.pth``; return ``(model, config)``.

    Metadata resolution order: the ``metadata`` argument, then the edge types
    recorded in the checkpoint, then the full declared schema.
    """
    from gnn.core.schema import FEATURE_DIMS

    path = Path(bundle_dir) / MODEL_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{MODEL_FILENAME} not found at {path}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ckpt = torch.load(str(path), map_location=device, weights_only=False)

    if metadata is None:
        edge_types = [tuple(et) for et in ckpt.get("edge_types") or []]
        metadata = (
            list(ckpt.get("node_types") or NODE_TYPES),
            edge_types or list(EDGE_TYPES),
        )

    hidden = ckpt.get("hidden_channels", 64)
    config = TrainConfig(
        arch=ckpt.get("arch", "sage"),
        hidden_channels=hidden,
        num_layers=ckpt.get("num_layers", 2),
        # Dropout is irrelevant at inference (eval mode disables it) but is
        # recorded so a reloaded model can be fine-tuned identically.
        dropout=ckpt.get("dropout", 0.0),
        arch_kwargs=dict(ckpt.get("arch_kwargs") or {}),
    )

    model = HetGNN(
        metadata=metadata,
        hidden_channels=hidden,
        out_channels=ckpt.get("out_channels", hidden),
        num_layers=config.num_layers,
        dropout=config.dropout,
        arch=config.arch,
        arch_kwargs=config.arch_kwargs,
    )
    model.set_input_dims(input_dims or FEATURE_DIMS)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    model.to(device)
    logger.info("Loaded model from %s (arch=%s, device=%s)", path, config.arch, device)
    return model, config
