"""model.py — the heterogeneous autoencoder in Flax.

Deliberately the same shape as ``gnn.backends.pyg.model.HetGNN``:

    x_t ──Dense(F_t → H)──ReLU──Dropout──┐
                                         ├── L × DGF message passing ──> z_t
                                         ┘
    z_t ──Dense(H → F_t)──> reconstruction of x_t

Per-node-type encoders and decoders are the point, exactly as in PyG:
reconstruction error stays attributable to router / interface / vpn / flow, so
the four objectives can be weighted separately and an operator can see *which*
layer of the network is misbehaving.

Two deviations from DGF's tutorial pattern, both intentional:

**No ``EmbedGraph``.** The tutorial's embedder reads named per-column features
and is coupled to DGF-native normalization. We hold the sklearn
``StandardScaler`` fixed across backends so an A/B is attributable (see
``gnn.backends.dgf.schema``), which leaves a single packed feature matrix and
nothing for ``EmbedGraph`` to do that a ``Dense`` does not. The hand-rolled
encoder also mirrors PyG's ``lin_dict`` exactly, so both backends project from
the same starting point. The matching post-embedding schema comes from
``schema.build_embedded_schema`` instead of ``EmbedGraphConfig.output_schema``.

**No ReLU/dropout between conv layers.** PyG applies both after each
``HeteroConv`` because ``SAGEConv`` is a bare linear aggregation. A DGF conv is
already a residual block with its own activation, norm and dropout; stacking
another activation on top would squash the residual path it depends on.
"""

import dataclasses
from typing import Any, Dict, Tuple

import jax.numpy as jnp
from flax import linen as nn

from gnn.backends.dgf.archs import get_spec
from gnn.backends.dgf.schema import EMBEDDING_KEY, FEATURE_KEY
from gnn.core.schema import FEATURE_DIMS


class HetAutoencoder(nn.Module):
    """Heterogeneous graph autoencoder over a padded DGF graph.

    Attributes:
        embedded_schema: post-encoder schema the conv layers are built against.
        plan: message-passing plan, ``[(edge_set_name, is_reversed), …]``.
        dims: latent width H.
        num_layers: number of message-passing layers.
        dropout: rate for the encoder and for each conv layer's internal dropout.
        arch: architecture name — see ``gnn.backends.dgf.archs.SPECS``.
        arch_kwargs: architecture-specific options (e.g. ``num_heads``).
    """

    embedded_schema: Any
    plan: Any
    dims: int = 64
    num_layers: int = 2
    dropout: float = 0.3
    arch: str = "hetconv"
    arch_kwargs: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @nn.compact
    def __call__(self, graph, training: bool
                 ) -> Tuple[Dict[str, jnp.ndarray], Dict[str, jnp.ndarray]]:
        """Run one padded snapshot through the autoencoder.

        Returns ``(reconstructions, embeddings)`` keyed by node type, **still
        padded and still jax arrays** — masking and the conversion to numpy
        happen at the backend boundary, not here, because the training loss
        needs the padded shapes to stay static.
        """
        spec = get_spec(self.arch)
        kwargs = spec.kwargs(self.arch_kwargs)
        drop = nn.Dropout(rate=self.dropout, deterministic=not training)

        # ── encode: schema-specific projection into the shared latent space ──
        node_sets = {}
        for node_type, node_set in graph.node_sets.items():
            h = nn.Dense(self.dims, name=f"encode_{node_type}")(
                node_set.features[FEATURE_KEY]
            )
            h = drop(nn.relu(h))
            # Rebuild rather than mutate `node_set.features` in place: the
            # tutorial mutates, but that leaves a stale "embedding" key on the
            # caller's graph object, which then differs between the first call
            # and every later one.
            node_sets[node_type] = dataclasses.replace(
                node_set, features={**node_set.features, EMBEDDING_KEY: h}
            )
        graph = dataclasses.replace(graph, node_sets=node_sets)

        # ── message passing ──────────────────────────────────────────────────
        for _ in range(self.num_layers):
            # Built inside the loop on purpose: hoisting a single `.make()`
            # result out would share one set of weights across every layer.
            conv = spec.factory(
                dims=self.dims,
                dropout_rate=self.dropout,
                plan=self.plan,
                embedded_schema=self.embedded_schema,
                **kwargs,
            )
            graph = conv(graph, training=training)

        # ── decode ───────────────────────────────────────────────────────────
        embeddings, reconstructions = {}, {}
        for node_type, node_set in graph.node_sets.items():
            z = node_set.features[EMBEDDING_KEY]
            embeddings[node_type] = z
            reconstructions[node_type] = nn.Dense(
                FEATURE_DIMS[node_type], name=f"decode_{node_type}"
            )(z)
        return reconstructions, embeddings


def masked_mse(pred: jnp.ndarray, target: jnp.ndarray, mask: jnp.ndarray
               ) -> jnp.ndarray:
    """Mean squared error over real rows only.

    ``mask`` is ``[N]`` boolean; padded rows contribute neither to the numerator
    nor the denominator. Without this the loss is diluted by however many
    sentinel rows the padding happens to contain, which varies with topology
    size — so the same model would report a different loss on a smaller network.
    """
    m = mask[:, None].astype(pred.dtype)
    squared_error = ((pred - target) ** 2) * m
    denominator = jnp.maximum(m.sum() * pred.shape[-1], 1.0)
    return squared_error.sum() / denominator


def contrastive_diversity_loss(embeddings: Dict[str, jnp.ndarray],
                               masks: Dict[str, jnp.ndarray]) -> jnp.ndarray:
    """Penalise within-type embedding similarity to prevent representation collapse.

    The JAX counterpart of ``gnn.backends.pyg.model.contrastive_diversity_loss``,
    and it must stay numerically equivalent or the two backends are optimising
    different objectives. Without it an autoencoder with per-type decoders can
    map every node of a type to nearly the same point and still reconstruct
    acceptably — which destroys the Mahalanobis scoring downstream, because a
    collapsed cluster has near-zero covariance and every live node then looks
    infinitely anomalous.

    Returns the mean off-diagonal cosine similarity over real nodes, averaged
    across node types with more than one node. Higher = more collapsed = worse.

    Shapes stay static under ``jit``: padded rows are zeroed out of the
    similarity matrix rather than sliced away, and node types with fewer than
    two real nodes contribute zero to both the sum and the count.
    """
    total = jnp.asarray(0.0)
    count = jnp.asarray(0.0)

    for node_type, embedding in embeddings.items():
        mask = masks[node_type].astype(embedding.dtype)
        n = mask.sum()

        normalized = embedding / jnp.maximum(
            jnp.linalg.norm(embedding, axis=1, keepdims=True), 1e-12
        )
        normalized = normalized * mask[:, None]

        similarity = normalized @ normalized.T
        # Zero the diagonal; padded rows are already zero vectors, so their
        # rows and columns contribute nothing.
        similarity = similarity * (1.0 - jnp.eye(similarity.shape[0],
                                                 dtype=similarity.dtype))
        pairs = n * (n - 1)
        contribution = jnp.where(pairs > 0, similarity.sum() / jnp.maximum(pairs, 1.0), 0.0)

        total = total + contribution
        count = count + jnp.where(n > 1, 1.0, 0.0)

    return jnp.where(count > 0, total / jnp.maximum(count, 1.0), 0.0)


def strip_padding(arrays: Dict[str, jnp.ndarray], masks: Dict[str, jnp.ndarray]
                  ) -> Dict[str, Any]:
    """jax arrays → numpy with padded rows removed — the backend boundary.

    Every path out of this backend goes through here. Doing the masking once, in
    one place, is the highest-value invariant in the multi-backend design:
    padding bugs are silent, since the model trains fine and only the scores are
    wrong.
    """
    import numpy as np

    return {
        node_type: np.asarray(value)[masks[node_type]]
        for node_type, value in arrays.items()
    }


__all__ = [
    "HetAutoencoder", "masked_mse", "contrastive_diversity_loss", "strip_padding",
]
