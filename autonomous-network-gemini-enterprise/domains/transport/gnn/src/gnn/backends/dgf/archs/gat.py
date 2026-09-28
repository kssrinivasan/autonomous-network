"""gat — DGF's ``HeterogeneousGraphAttentionNetwork``.

Same config surface as ``hetconv`` plus ``num_heads``, which is what makes the
hetconv-vs-gat A/B clean: one field changes, everything else — dims, dropout,
plan, the training loop, the normalizer — is held fixed.

``message_pooling`` is accepted by the config but unused by this layer
(aggregation is by attention weight), so it is not exposed here; passing it
would suggest a knob that does nothing.

Attention is the plausible win for this problem. Anomaly signal in a transport
network is usually carried by one or two neighbours — the interface that
started dropping packets, the peer that withdrew its routes — and mean pooling
dilutes that by degree. Whether it actually pays for its cost is what the
benchmark is for; on the synthetic fixture ``pyg.gat`` was ~2× slower than
``pyg.sage`` for no recall gain, so treat this as a hypothesis, not a default.
"""

from gnn.backends.dgf.archs.base import ArchSpec


def _make_layer(dims: int, dropout_rate: float, plan, embedded_schema,
                num_heads: int = 4):
    from dgf.jax.layers import HeterogeneousGraphAttentionNetworkConfig

    if dims % num_heads != 0:
        raise ValueError(
            f"dims={dims} is not divisible by num_heads={num_heads}; "
            "pick a hidden width that splits evenly across heads"
        )
    return HeterogeneousGraphAttentionNetworkConfig(
        plan=plan,
        dims=dims,
        dropout_rate=dropout_rate,
        num_heads=num_heads,
    ).make(embedded_schema)


SPEC = ArchSpec(
    name="gat",
    factory=_make_layer,
    defaults={"num_heads": 4},
)
