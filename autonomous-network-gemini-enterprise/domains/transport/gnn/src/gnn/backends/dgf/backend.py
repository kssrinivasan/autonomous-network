"""gnn.backends.dgf — Google Distributed Graph Flow (JAX/Flax) backend.

Supports the ``hetconv`` and ``gat`` architectures. Importing this module
imports jax, flax and dgf; go through ``gnn.backends.load_backend("dgf")`` so
that never happens by accident.

**Platform note.** DGF publishes manylinux x86-64 wheels only. On Apple silicon
``pip install dgf`` resolves to an unrelated placeholder package at version
0.0.0 rather than failing, so :func:`_require_dgf` checks the version rather
than merely that the import succeeded. Run this backend on a Linux x86-64 host
or via ``gnn/docker/Dockerfile.dgf``.

The division of labour is the same as for PyG: this backend owns everything
between ``SnapshotArrays`` going in and numpy embeddings coming out, and nothing
on either side. The one structural difference JAX forces is padding, and it is
confined to two places — :meth:`DGFBackend.train` derives it, and
:class:`DGFTrainedModel` applies and strips it. No padded row ever crosses this
boundary.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from gnn.backends.base import TrainConfig, TrainResult
from gnn.backends.dgf import archs, checkpoint
from gnn.backends.dgf.graph import (DGFGraph, arrays_to_graph, derive_padding,
                                    grow_padding, pad, pad_batch)
from gnn.backends.dgf.model import HetAutoencoder, strip_padding
from gnn.backends.dgf.schema import (MESSAGE_PLAN, build_embedded_schema,
                                     build_graph_schema)
from gnn.backends.dgf.trainer import train_model
from gnn.core.snapshot import SnapshotArrays

logger = logging.getLogger(__name__)

# The submodules this backend actually calls into. Presence of all of them is
# the install check — see _require_dgf.
REQUIRED_DGF_MODULES = ("data", "transform", "analyse", "convert", "exception")


def _require_dgf() -> None:
    """Fail with something actionable if ``dgf`` is missing or not the real one.

    Deliberately *not* a version check. DGF 0.0.5 ships ``__version__ =
    "0.0.4"`` in ``dgf/__init__.py`` — the in-package string was never bumped
    and disagrees with the wheel metadata — so a ``>=`` comparison against the
    released version rejects a correct install. String comparison would be
    wrong anyway once a 0.0.10 exists.

    What matters is whether the API is there, so that is what is tested.
    """
    try:
        import dgf
    except ImportError as exc:
        raise ImportError(
            f"dgf is not importable: {exc}. DGF ships manylinux x86-64 wheels "
            "only — there is no macOS or aarch64 build and no sdist to build "
            "from. Use gnn/docker/Dockerfile.dgf or a Linux x86-64 host."
        ) from exc

    missing = [m for m in REQUIRED_DGF_MODULES if not hasattr(dgf, m)]
    if missing:
        raise ImportError(
            f"dgf is installed but is missing {', '.join(missing)} — this looks "
            "like the unrelated placeholder distribution rather than Google's "
            "DGF. Install the manylinux x86-64 wheel on a Linux x86-64 host."
        )


def _insufficient_padding_error() -> type:
    """``InsufficientPaddingError``, wherever this DGF version exports it.

    Canonically ``dgf.exception``; it is *not* re-exported from
    ``dgf.transform`` alongside ``merge_graphs``, which is the natural place to
    look and the reason this helper exists. ``dgf.src.transform.merge`` is the
    defining module and the last-resort fallback.

    Only the class's identity matters here. Catching ``Exception`` instead
    would swallow genuine bugs in :func:`pad` — including the sentinel-layout
    assertion, whose whole purpose is to fail loudly.
    """
    import importlib

    import dgf

    for module in (getattr(dgf, "exception", None),
                   getattr(dgf, "transform", None)):
        error = getattr(module, "InsufficientPaddingError", None)
        if isinstance(error, type):
            return error
    try:
        merge = importlib.import_module("dgf.src.transform.merge")
        error = getattr(merge, "InsufficientPaddingError", None)
        if isinstance(error, type):
            return error
    except ImportError:
        pass
    # Not fatal, but it silently disables padding growth, so say so loudly:
    # the failure mode is a serving request dying on a graph one router too big.
    logger.warning("InsufficientPaddingError not found in this dgf build; "
                   "automatic padding growth at inference time is disabled")
    return _NeverRaised


class _NeverRaised(Exception):
    """Placeholder so ``except`` still type-checks when DGF moves the error."""


def _prng_key(seed: Optional[int]):
    import jax

    if seed is None:
        # Stochastic by default, matching PyG. Pinned seeds proved brittle for
        # this autoencoder; stochastic init plus the pipeline quality gate is
        # more robust than picking a seed and hoping.
        seed = int(np.random.SeedSequence().generate_state(1)[0])
        logger.info("seed not set — training is stochastic (drew seed=%d)", seed)
    else:
        logger.info("JAX PRNG seeded with seed=%d", seed)
    return jax.random.PRNGKey(seed)


class DGFTrainedModel:
    """Adapter putting trained DGF params behind the ``TrainedModel`` protocol.

    Holds the padding the model was trained with. A snapshot that outgrows it
    would otherwise raise ``InsufficientPaddingError`` mid-request; instead the
    padding is grown and the forward pass recompiled, which costs one slow
    prediction rather than a failed one.
    """

    def __init__(self, model: HetAutoencoder, params, config: TrainConfig,
                 schema, padding, plan):
        self.model = model
        self.params = params
        self.config = config
        self.schema = schema
        self.padding = padding
        self.plan = plan
        self._apply = None

    @property
    def variant(self) -> str:
        # From the module, not from ``config``: ``config`` can be overridden by
        # the caller at load time, the module is what actually holds the weights.
        return f"dgf.{self.model.arch}"

    # ── inference ────────────────────────────────────────────────────────────

    def _jitted_apply(self):
        import jax

        if self._apply is None:
            self._apply = jax.jit(self.model.apply, static_argnames="training")
        return self._apply

    def _pad(self, graph: DGFGraph):
        try:
            return pad(graph, self.schema, self.padding)
        except _insufficient_padding_error():
            logger.warning(
                "snapshot exceeds the padding this model was trained with "
                "(nodes=%s); growing padding and recompiling. Retrain to make "
                "this permanent.", graph.num_nodes,
            )
            self.padding = grow_padding(self.padding, graph)
            return pad(graph, self.schema, self.padding)

    def forward(self, graph: DGFGraph
                ) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        jax_graph, masks = self._pad(graph)
        reconstructions, embeddings = self._jitted_apply()(
            self.params, jax_graph, training=False
        )
        return strip_padding(reconstructions, masks), strip_padding(embeddings, masks)

    def embed(self, graph: DGFGraph) -> Dict[str, np.ndarray]:
        return self.forward(graph)[1]

    def reconstruct(self, graph: DGFGraph) -> Dict[str, np.ndarray]:
        return self.forward(graph)[0]

    # ── persistence ──────────────────────────────────────────────────────────

    def save(self, bundle_dir: Path, val_loss: float = float("nan")) -> None:
        checkpoint.save_model(self.params, Path(bundle_dir), self.config,
                              self.schema, self.padding, self.plan, val_loss)


class DGFBackend:
    """Distributed Graph Flow backend.

    Args:
        padding_relative_margin: fraction of headroom above the largest
            training graph.
        padding_absolute_margin: minimum extra slots on top of that. DGF
            requires at least 1, because padding edges need a node to point at;
            the default here is deliberately larger so that a model survives a
            few new devices being provisioned without retraining.

    Both come from the ``dgf:`` block of the variant config.
    """

    name = "dgf"

    def __init__(self, padding_relative_margin: float = 0.25,
                 padding_absolute_margin: int = 8):
        _require_dgf()
        if padding_absolute_margin < 1:
            raise ValueError(
                "padding_absolute_margin must be >= 1: padding edges point at a "
                "sentinel node, so the padded node count has to exceed the real "
                "one by at least one"
            )
        self.padding_relative_margin = float(padding_relative_margin)
        self.padding_absolute_margin = int(padding_absolute_margin)
        self.schema = build_graph_schema()

    @property
    def architectures(self) -> Tuple[str, ...]:
        return tuple(sorted(archs.SPECS))

    # ── graph construction ───────────────────────────────────────────────────

    def build_graph(self, arrays: SnapshotArrays) -> DGFGraph:
        """Wrap already-normalized arrays in an **unpadded** ``InMemoryGraph``.

        Padding is deliberately not applied here. It is a property of the model
        — recorded in the bundle, shared across every snapshot — whereas this
        method sees one snapshot at a time. Padding here would let whichever
        snapshot happened to be converted first fix the shapes for all the rest.
        """
        return arrays_to_graph(arrays)

    # ── training ─────────────────────────────────────────────────────────────

    def train(self, train_graphs: Sequence[DGFGraph], val_graphs: Sequence[DGFGraph],
              config: TrainConfig) -> TrainResult:
        all_graphs = list(train_graphs) + list(val_graphs)
        if not all_graphs:
            raise ValueError("no graphs to train on")

        padding = derive_padding(
            all_graphs, self.schema,
            relative_margin=self.padding_relative_margin,
            absolute_margin=self.padding_absolute_margin,
        )
        embedded_schema = build_embedded_schema(config.hidden_channels)

        model = HetAutoencoder(
            embedded_schema=embedded_schema,
            plan=MESSAGE_PLAN,
            dims=config.hidden_channels,
            num_layers=config.num_layers,
            dropout=config.dropout,
            arch=config.arch,
            arch_kwargs=dict(config.arch_kwargs),
        )
        logger.info(
            "Created HetAutoencoder arch=%s dims=%d num_layers=%d dropout=%s %s",
            config.arch, config.hidden_channels, config.num_layers,
            config.dropout, config.arch_kwargs or "",
        )
        logger.info("Message plan (%d directions over %d edge sets)",
                    len(MESSAGE_PLAN), len({name for name, _ in MESSAGE_PLAN}))

        train_batches = pad_batch(train_graphs, self.schema, padding)
        val_batches = pad_batch(val_graphs, self.schema, padding)

        params, best_val_loss, history, epochs_run = train_model(
            model, train_batches, val_batches, config, _prng_key(config.seed)
        )
        return TrainResult(
            model=DGFTrainedModel(model, params, config, self.schema,
                                  padding, MESSAGE_PLAN),
            best_val_loss=best_val_loss,
            history=history,
            epochs_run=epochs_run,
        )

    # ── loading ──────────────────────────────────────────────────────────────

    def load(self, bundle_dir: Path,
             config: Optional[TrainConfig] = None) -> DGFTrainedModel:
        """Reload a trained model from ``bundle_dir/model.pth``.

        Schema, padding and plan all come from the bundle, so loading needs
        neither the data source nor the current topology.
        """
        params, loaded_config, schema, padding, plan = checkpoint.load_model(
            Path(bundle_dir)
        )
        config = config or loaded_config
        model = HetAutoencoder(
            embedded_schema=build_embedded_schema(loaded_config.hidden_channels),
            plan=plan,
            dims=loaded_config.hidden_channels,
            num_layers=loaded_config.num_layers,
            dropout=loaded_config.dropout,
            arch=loaded_config.arch,
            arch_kwargs=dict(loaded_config.arch_kwargs),
        )
        return DGFTrainedModel(model, params, config, schema, padding, plan)


__all__ = [
    "DGFBackend", "DGFTrainedModel", "HetAutoencoder",
    "arrays_to_graph", "derive_padding", "pad", "strip_padding",
]
