"""base.py — description of one PyG message-passing architecture."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Tuple

EdgeType = Tuple[str, str, str]

#: (edge_type, hidden_channels, **arch_kwargs) -> a PyG MessagePassing module
ConvFactory = Callable[..., Any]


@dataclass(frozen=True)
class ArchSpec:
    """How to build the per-relation convolutions for one architecture.

    Attributes:
        name:         identifier used in configs and variant strings ("sage").
        conv_factory: builds one convolution for one edge type.
        aggr:         how ``HeteroConv`` combines messages arriving at a node
                      from different relations.
        defaults:     architecture-specific kwargs, overridable per run via
                      ``TrainConfig.arch_kwargs``.
    """

    name: str
    conv_factory: ConvFactory
    aggr: str = "mean"
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
