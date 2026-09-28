"""base.py — description of one DGF message-passing architecture."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict

#: (dims, dropout_rate, plan, **arch_kwargs) -> a built Flax message-passing module.
#: Called once per layer inside ``@nn.compact``; returning a shared instance
#: would tie every layer to the same weights.
LayerFactory = Callable[..., Any]


@dataclass(frozen=True)
class ArchSpec:
    """How to build one message-passing layer for one architecture.

    Attributes:
        name:    identifier used in configs and variant strings ("hetconv").
        factory: builds one layer, given the shared dims/dropout/plan and the
                 architecture's own kwargs.
        defaults: architecture-specific kwargs, overridable per run via
                 ``TrainConfig.arch_kwargs``.
    """

    name: str
    factory: LayerFactory
    defaults: Dict[str, Any] = field(default_factory=dict)

    def kwargs(self, overrides: Dict[str, Any] = None) -> Dict[str, Any]:
        merged = dict(self.defaults)
        merged.update(overrides or {})
        unknown = set(merged) - set(self.defaults)
        if unknown:
            raise ValueError(
                f"arch '{self.name}' got unknown arch_kwargs {sorted(unknown)}; "
                f"supported: {sorted(self.defaults) or 'none'}"
            )
        return merged
