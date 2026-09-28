"""config.py — turn ``configs/*.yaml`` into a ``TrainConfig``.

A variant is data, not code. ``configs/base.yaml`` holds everything that must
stay fixed across variants (split, normalizer, loss weights, scoring, benchmark
protocol); ``configs/variants/<name>.yaml`` holds only what the variant changes.
The merge is one level deep per top-level section, so a variant that sets
``train.epochs`` inherits the rest of ``train`` rather than replacing it.

Loading is intentionally framework-free — resolving a config must not import
torch or jax, so the benchmark runner can enumerate and validate every variant
before deciding which backends it actually needs.
"""

import copy
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from gnn.backends.base import TrainConfig

logger = logging.getLogger(__name__)

# In a source checkout ``configs/`` sits beside ``src/``:
#   gnn/src/gnn/config.py → gnn/configs
# The container images copy the tree differently (``ADD gnn/src/ /app``), so
# GNN_CONFIG_ROOT lets the Dockerfile point at wherever it put them without
# this module having to guess the layout.
CONFIG_ROOT = Path(
    os.getenv("GNN_CONFIG_ROOT") or Path(__file__).resolve().parents[2] / "configs"
)
BASE_FILENAME = "base.yaml"
VARIANTS_DIRNAME = "variants"


@dataclass
class VariantConfig:
    """One fully resolved variant: base.yaml merged with a variant file."""

    variant: str
    backend: str
    arch: str
    train: TrainConfig
    data: Dict[str, Any] = field(default_factory=dict)
    loss: Dict[str, Any] = field(default_factory=dict)
    scoring: Dict[str, Any] = field(default_factory=dict)
    benchmark: Dict[str, Any] = field(default_factory=dict)
    backend_options: Dict[str, Any] = field(default_factory=dict)
    source: Optional[Path] = None

    @property
    def validation_split(self) -> float:
        return float(self.data.get("validation_split", 0.2))

    @property
    def repeats(self) -> int:
        return int(self.benchmark.get("repeats", 1))

    @property
    def mse_thresholds(self) -> Dict[str, float]:
        return dict(self.scoring.get("mse_thresholds", {}))

    def __repr__(self) -> str:
        return (f"VariantConfig({self.variant}, hidden={self.train.hidden_channels}, "
                f"layers={self.train.num_layers}, epochs={self.train.epochs})")


def _read_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``override`` onto ``base``, recursing into nested dicts.

    Scalars and lists replace wholesale; dicts merge. That means a variant can
    change ``loss.weights.router`` without restating the other three weights,
    but changing ``benchmark.report`` replaces the whole list — which is the
    intuitive reading in both cases.
    """
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def variant_path(name: str, config_root: Path = CONFIG_ROOT) -> Path:
    """Resolve a variant name to its YAML file.

    Accepts the dotted runtime form (``pyg.gat``), the filename stem
    (``pyg-gat``), or an explicit path.
    """
    candidate = Path(name)
    if candidate.suffix in (".yaml", ".yml"):
        return candidate
    stem = name.replace(".", "-")
    return Path(config_root) / VARIANTS_DIRNAME / f"{stem}.yaml"


def available_variants(config_root: Path = CONFIG_ROOT) -> List[str]:
    """Variant names with a config file, whether or not their backend installs."""
    directory = Path(config_root) / VARIANTS_DIRNAME
    if not directory.is_dir():
        return []
    return sorted(p.stem.replace("-", ".") for p in directory.glob("*.yaml"))


def load_variant(name: str, config_root: Path = CONFIG_ROOT,
                 overrides: Optional[Dict[str, Any]] = None) -> VariantConfig:
    """Load and validate one variant.

    Args:
        name: ``"pyg.gat"``, ``"pyg-gat"``, or a path to a variant YAML.
        config_root: directory holding ``base.yaml`` and ``variants/``.
        overrides: merged last, above the variant file. For CLI flags and
            benchmark sweeps — e.g. ``{"train": {"epochs": 3}}`` for a smoke run.
    """
    config_root = Path(config_root)
    path = variant_path(name, config_root)
    merged = _merge(_read_yaml(config_root / BASE_FILENAME), _read_yaml(path))
    if overrides:
        merged = _merge(merged, overrides)

    variant = merged.get("variant")
    backend = merged.get("backend")
    arch_block = merged.get("arch") or {}
    arch = arch_block.get("name")

    missing = [k for k, v in (("variant", variant), ("backend", backend), ("arch", arch))
               if not v]
    if missing:
        raise ValueError(f"{path}: missing required key(s): {', '.join(missing)}")
    if variant != f"{backend}.{arch}":
        raise ValueError(
            f"{path}: variant '{variant}' does not match backend.arch "
            f"'{backend}.{arch}' — the two must agree or the bundle will be "
            "reloaded by the wrong backend at serve time."
        )

    train_block = merged.get("train") or {}
    loss_block = merged.get("loss") or {}

    train = TrainConfig(
        arch=arch,
        hidden_channels=int(arch_block.get("hidden_channels", 64)),
        num_layers=int(arch_block.get("num_layers", 2)),
        dropout=float(arch_block.get("dropout", 0.3)),
        epochs=int(train_block.get("epochs", 50)),
        learning_rate=float(train_block.get("learning_rate", 1e-3)),
        early_stopping_patience=int(train_block.get("early_stopping_patience", 10)),
        min_delta=float(train_block.get("min_delta", 1e-3)),
        diversity_weight=float(loss_block.get("diversity_weight", 0.1)),
        seed=train_block.get("seed"),
        arch_kwargs=dict(arch_block.get("kwargs") or {}),
        # The block named after the backend, e.g. `dgf:` — framework-level
        # options, passed to load_backend().
        backend_options=dict(merged.get(backend) or {}),
    )
    # Assign after construction rather than passing an empty dict, which would
    # shadow TrainConfig's default weights with nothing.
    weights = loss_block.get("weights") or {}
    if weights:
        train.loss_weights = {k: float(v) for k, v in weights.items()}
    if train.seed is not None:
        train.seed = int(train.seed)

    return VariantConfig(
        variant=variant,
        backend=backend,
        arch=arch,
        train=train,
        data=merged.get("data") or {},
        loss=loss_block,
        scoring=merged.get("scoring") or {},
        benchmark=merged.get("benchmark") or {},
        backend_options=merged.get(backend) or {},
        source=path,
    )


__all__ = [
    "VariantConfig", "load_variant", "available_variants", "variant_path",
    "CONFIG_ROOT",
]
