"""hetconv — DGF's ``HeterogeneousGraphConvolution``.

The nearest DGF equivalent of the incumbent PyG GraphSAGE stack, but not a
drop-in one: a DGF conv layer is a transformer-style residual block
(``concat[src, dst]`` → message MLP → segment aggregation → ``concat[dst, agg]``
→ update MLP → residual → post MLP), whereas ``SAGEConv`` is
``W₁·x_dst + W₂·mean(x_src)``. It has several times the parameters per layer.
So ``dgf.hetconv`` vs ``pyg.sage`` is a comparison of *stacks*, not of one
isolated design choice — the honest same-framework comparison is
``dgf.hetconv`` vs ``dgf.gat``.

``message_pooling="mean"`` overrides DGF's ``"sum"`` default to match PyG's
``aggr="mean"``. With sum pooling a node's representation scales with its
degree, and in this topology degree varies by an order of magnitude between a
core P router and a CE — which the autoencoder would then dutifully learn to
reconstruct, reporting every well-connected router as anomalous.
"""

from gnn.backends.dgf.archs.base import ArchSpec


def _make_layer(dims: int, dropout_rate: float, plan, embedded_schema,
                message_pooling: str = "mean"):
    from dgf.jax.layers import HeterogeneousGraphConvolutionConfig

    return HeterogeneousGraphConvolutionConfig(
        plan=plan,
        dims=dims,
        dropout_rate=dropout_rate,
        message_pooling=message_pooling,
    ).make(embedded_schema)


SPEC = ArchSpec(
    name="hetconv",
    factory=_make_layer,
    defaults={"message_pooling": "mean"},
)
