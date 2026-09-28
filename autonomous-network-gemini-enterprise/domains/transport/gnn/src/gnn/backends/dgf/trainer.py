"""trainer.py — the DGF training loop.

Architecture-agnostic, exactly as the PyG trainer is: it only knows the model
returns ``(reconstructions, embeddings)``, so ``hetconv`` and ``gat`` train
through this same code and an A/B differs only in the convolution.

**Why not ``dgf.jax.train``?** It is the better loop in general — step-based,
preemption-tolerant, orbax checkpointing, distributed. But it is step-based
where ours is epoch-based, and its early stopping, LR schedule and metric
accounting are its own. Adopting it here would mean a ``dgf.*`` result differs
from a ``pyg.*`` result in the *training procedure* as well as the
architecture, which is exactly the confound the benchmark exists to avoid. This
loop reproduces ``gnn.backends.pyg.trainer`` step for step: same Adam, same
plateau schedule, same ``min_delta``/patience early stopping, same
best-weight restore, same ``history`` keys.

Switch to ``dgf.jax.train`` when training outgrows one machine — at that point
the distributed loop is worth the lost comparability, and the PyG arm will have
served its purpose.
"""

import logging
import time
from typing import Any, Dict, List, Sequence, Tuple

import jax
import jax.numpy as jnp
import optax

from gnn.backends.base import TrainConfig
from gnn.backends.dgf.model import (contrastive_diversity_loss, masked_mse)
from gnn.backends.dgf.schema import FEATURE_KEY

logger = logging.getLogger(__name__)

# Plateau schedule, identical to the PyG trainer's ReduceLROnPlateau.
LR_FACTOR = 0.5
LR_PATIENCE = 5
LR_MIN = 1e-6


def _as_jnp_masks(masks: Dict[str, Any]) -> Dict[str, jnp.ndarray]:
    return {k: jnp.asarray(v) for k, v in masks.items()}


def make_steps(model, optimizer, config: TrainConfig):
    """Build the jitted ``(loss_fn, train_step, eval_step)`` triple."""

    def loss_fn(params, batch, rng_key, training: bool):
        graph, masks = batch
        reconstructions, embeddings = model.apply(
            params, graph, training=training,
            rngs={"dropout": rng_key} if training else None,
        )

        per_type = {
            node_type: masked_mse(
                reconstruction,
                graph.node_sets[node_type].features[FEATURE_KEY],
                masks[node_type],
            )
            for node_type, reconstruction in reconstructions.items()
        }
        diversity = contrastive_diversity_loss(embeddings, masks)
        total = sum(
            config.loss_weights.get(node_type, 0.0) * loss
            for node_type, loss in per_type.items()
        ) + config.diversity_weight * diversity

        metrics = {"total": total, "diversity": diversity,
                   **{f"loss_{k}": v for k, v in per_type.items()}}
        return total, metrics

    @jax.jit
    def train_step(params, opt_state, batch, rng_key):
        (_, metrics), grads = jax.value_and_grad(loss_fn, has_aux=True)(
            params, batch, rng_key, True
        )
        updates, opt_state = optimizer.update(grads, opt_state, params)
        return optax.apply_updates(params, updates), opt_state, metrics

    @jax.jit
    def eval_step(params, batch):
        _, metrics = loss_fn(params, batch, None, False)
        return metrics

    return loss_fn, train_step, eval_step


def _evaluate(eval_step, params, batches: Sequence) -> Dict[str, Any]:
    """Mean metrics over ``batches`` with dropout disabled."""
    if not batches:
        return {"total": 0.0, "diversity": 0.0, "per_type": {}}

    accumulated: Dict[str, List[float]] = {}
    for batch in batches:
        for key, value in eval_step(params, batch).items():
            accumulated.setdefault(key, []).append(float(value))

    mean = {k: sum(v) / len(v) for k, v in accumulated.items()}
    return {
        "total": mean.get("total", 0.0),
        "diversity": mean.get("diversity", 0.0),
        "per_type": {k[len("loss_"):]: v for k, v in mean.items()
                     if k.startswith("loss_")},
    }


def train_model(model, train_batches: Sequence, val_batches: Sequence,
                config: TrainConfig, rng_key
                ) -> Tuple[Any, float, Dict[str, List[float]], int]:
    """Train ``model``; return ``(params, best_val_loss, history, epochs_run)``.

    Early stopping restores the best parameters before returning, so what comes
    back is the model that achieved ``best_val_loss`` — not whatever the final
    epoch happened to produce.
    """
    train_batches = [(g, _as_jnp_masks(m)) for g, m in train_batches]
    val_batches = [(g, _as_jnp_masks(m)) for g, m in val_batches]
    if not train_batches:
        raise ValueError("no training snapshots")
    if not val_batches:
        # Silently falling back to the training set would report a best
        # validation loss that means nothing, and early stopping would never
        # fire correctly.
        raise ValueError(
            "no validation snapshots — increase validation_split or supply "
            "more snapshots"
        )

    # inject_hyperparams exposes the learning rate as mutable state, which is
    # how the plateau schedule below adjusts it — optax schedules are pure
    # functions of the step count and cannot react to the validation loss.
    optimizer = optax.inject_hyperparams(optax.adam)(
        learning_rate=config.learning_rate
    )
    _, train_step, eval_step = make_steps(model, optimizer, config)

    init_key, rng_key = jax.random.split(rng_key)
    params = model.init(init_key, train_batches[0][0], training=True)
    opt_state = optimizer.init(params)

    logger.info(
        "Training up to %d epochs on %d snapshots (val %d), early stopping "
        "patience=%d, loss weights=%s, diversity=%s",
        config.epochs, len(train_batches), len(val_batches),
        config.early_stopping_patience, config.loss_weights,
        config.diversity_weight,
    )

    best_val_loss = float("inf")
    best_params = None
    epochs_without_improvement = 0
    plateau_epochs = 0
    history: Dict[str, List[float]] = {"train": [], "val": []}
    epoch = -1
    started = time.time()

    for epoch in range(config.epochs):
        for batch in train_batches:
            rng_key, step_key = jax.random.split(rng_key)
            params, opt_state, _ = train_step(params, opt_state, batch, step_key)

        # Re-measure the training loss with dropout off. Averaging the losses
        # seen during the epoch would mix dropout noise and mid-epoch weight
        # updates into a number compared against a dropout-free validation loss.
        train_metrics = _evaluate(eval_step, params, train_batches)
        val_metrics = _evaluate(eval_step, params, val_batches)
        train_loss, val_loss = train_metrics["total"], val_metrics["total"]

        if not (train_loss == train_loss and val_loss == val_loss):  # NaN check
            raise ValueError(
                f"loss became NaN at epoch {epoch + 1} "
                f"(train={train_loss}, val={val_loss}) — check the normalizer "
                "output for infinities, or lower the learning rate"
            )

        history["train"].append(train_loss)
        history["val"].append(val_loss)

        # ReduceLROnPlateau, reimplemented: halve the LR after LR_PATIENCE
        # epochs without improvement, down to LR_MIN.
        if val_loss < best_val_loss - config.min_delta:
            plateau_epochs = 0
        else:
            plateau_epochs += 1
            if plateau_epochs >= LR_PATIENCE:
                current = float(opt_state.hyperparams["learning_rate"])
                reduced = max(current * LR_FACTOR, LR_MIN)
                if reduced < current:
                    opt_state.hyperparams["learning_rate"] = jnp.asarray(reduced)
                    logger.info("  Reduced learning rate %.6f → %.6f", current, reduced)
                plateau_epochs = 0

        current_lr = float(opt_state.hyperparams["learning_rate"])
        if (epoch + 1) % 5 == 0:
            logger.info("Epoch %d/%d, Train Loss: %.4f, Val Loss: %.4f, LR: %.6f",
                        epoch + 1, config.epochs, train_loss, val_loss, current_lr)
            logger.info("  Train - %s, Diversity: %.4f",
                        _fmt_per_type(train_metrics["per_type"]),
                        train_metrics["diversity"])
            logger.info("  Val   - %s, Diversity: %.4f",
                        _fmt_per_type(val_metrics["per_type"]),
                        val_metrics["diversity"])
        else:
            logger.debug("Epoch %d/%d, Train Loss: %.4f, Val Loss: %.4f",
                         epoch + 1, config.epochs, train_loss, val_loss)

        if val_loss < best_val_loss - config.min_delta:
            best_val_loss = val_loss
            epochs_without_improvement = 0
            # jax arrays are immutable, so unlike torch's state_dict this needs
            # no defensive clone — the tree cannot be mutated underneath us.
            best_params = params
            logger.info("  ✓ New best validation loss: %.4f", best_val_loss)
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.early_stopping_patience:
                logger.info("Early stopping after %d epochs (no improvement for %d)",
                            epoch + 1, config.early_stopping_patience)
                break

    if best_params is not None:
        params = best_params
        logger.info("Restored best parameters (val_loss=%.4f)", best_val_loss)

    logger.info("Trained %d epochs in %.1fs", epoch + 1, time.time() - started)
    return params, best_val_loss, history, epoch + 1


def _fmt_per_type(per_type: Dict[str, float]) -> str:
    return ", ".join(f"{k.capitalize()}: {v:.4f}" for k, v in sorted(per_type.items()))
