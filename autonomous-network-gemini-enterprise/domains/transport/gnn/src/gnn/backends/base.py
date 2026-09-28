"""base.py — the backend contract.

Two axes vary independently:

  * **backend**      — which framework runs the maths (``pyg`` = PyTorch
                       Geometric, ``dgf`` = JAX/Flax).
  * **architecture** — which message-passing block it uses (``sage``, ``gat``,
                       ``hetconv``).

A backend owns everything between ``SnapshotArrays`` going in and numpy
embeddings/reconstructions coming out. Everything on either side of that —
schema, scaling, Mahalanobis, thresholds, explanations, Spanner writes — is
shared ``gnn.core`` code.

These are ``Protocol``s, not ABCs, deliberately: the DGF backend's natural
implementation is a Flax module plus a closure, and forcing it to inherit from
a torch-shaped base class would be the tail wagging the dog. Duck typing plus
``@runtime_checkable`` gives the same guardrail without the coupling.

The critical clause is ``embed``/``reconstruct`` returning
``dict[node_type, np.ndarray]`` **with padding already removed**. JAX needs
static shapes and therefore sentinel-padded graphs; if a single padded row
escapes into the scoring code it silently contaminates every mean, covariance
and threshold. Making unpadding part of the contract confines that failure mode
to one place per backend.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np

from gnn.core.snapshot import SnapshotArrays


@dataclass
class TrainConfig:
    """Backend-agnostic training hyperparameters.

    ``arch`` selects the message-passing block within the backend;
    ``arch_kwargs`` carries anything specific to it (e.g. ``heads`` for GAT,
    ``num_heads`` for DGF attention) so this dataclass doesn't accumulate a
    field per architecture.

    ``backend_options`` is the same idea one level up — options belonging to the
    *framework* rather than to the architecture, such as DGF's padding margins.
    Kept here rather than passed separately so that a variant string plus a
    ``TrainConfig`` still fully determines a run, which is what makes a
    benchmark result reproducible from what gets recorded.
    """

    arch: str = "sage"
    hidden_channels: int = 64
    num_layers: int = 2
    dropout: float = 0.3
    epochs: int = 50
    learning_rate: float = 1e-3
    early_stopping_patience: int = 10
    min_delta: float = 1e-3
    # Multi-task reconstruction weights per node type, plus the contrastive
    # diversity penalty that stops the latent space collapsing.
    loss_weights: Dict[str, float] = field(default_factory=lambda: {
        "router": 0.25, "interface": 0.25, "vpn": 0.25, "flow": 0.25,
    })
    diversity_weight: float = 0.1
    seed: Optional[int] = None
    arch_kwargs: Dict[str, Any] = field(default_factory=dict)
    backend_options: Dict[str, Any] = field(default_factory=dict)

    @property
    def variant(self) -> str:
        """Stable identifier for this configuration, e.g. ``pyg.gat``."""
        return self.arch


@dataclass
class TrainResult:
    """What a training run produces, beyond the model itself."""

    model: "TrainedModel"
    best_val_loss: float
    history: Dict[str, List[float]]
    epochs_run: int


@runtime_checkable
class TrainedModel(Protocol):
    """A model that can embed and reconstruct, in numpy."""

    #: e.g. "pyg.sage" — written into artefacts so a bundle is self-describing.
    variant: str

    def embed(self, graph: Any) -> Dict[str, np.ndarray]:
        """Latent embeddings per node type, ``[N_t, D]``, padding removed."""
        ...

    def reconstruct(self, graph: Any) -> Dict[str, np.ndarray]:
        """Reconstructed input features per node type, ``[N_t, F_t]``, padding removed."""
        ...

    def forward(self, graph: Any) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        """``(reconstructions, embeddings)`` in one pass — avoids running the
        model twice at serving time, where both are always needed."""
        ...

    def save(self, bundle_dir: Path) -> None:
        """Write weights and whatever else is needed to reload into ``bundle_dir``."""
        ...


@runtime_checkable
class Backend(Protocol):
    """Builds framework graphs, trains, and reloads trained models."""

    #: "pyg" | "dgf"
    name: str

    #: architectures this backend can build, e.g. ("sage", "gat")
    architectures: Tuple[str, ...]

    def build_graph(self, arrays: SnapshotArrays) -> Any:
        """Wrap normalized ``SnapshotArrays`` in the framework's graph container.

        The arrays are expected to be **already normalized** — the backend does
        not own scaling, so an A/B comparison can hold the normalizer fixed.
        """
        ...

    def train(
        self,
        train_graphs: Sequence[Any],
        val_graphs: Sequence[Any],
        config: TrainConfig,
    ) -> TrainResult:
        ...

    def load(self, bundle_dir: Path, config: Optional[TrainConfig] = None) -> TrainedModel:
        ...
