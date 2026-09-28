"""trainer.py — the PyG training loop.

Architecture-agnostic: it only knows that the model returns
``(reconstructions, embeddings)``, so ``sage`` and ``gat`` train through
exactly this code. That is deliberate — an A/B result is only meaningful if the
only thing that differs between the two runs is the convolution.
"""

import logging
from typing import Dict, List, Sequence, Tuple

import torch
import torch.nn as nn

from gnn.backends.base import TrainConfig
from gnn.backends.pyg.model import HetGNN, contrastive_diversity_loss

logger = logging.getLogger(__name__)

# ReduceLROnPlateau settings — halve the LR after 5 epochs without validation
# improvement, down to a floor of 1e-6.
LR_FACTOR = 0.5
LR_PATIENCE = 5
LR_MIN = 1e-6


def _snapshot_losses(
    model: HetGNN,
    graph,
    criterion,
    config: TrainConfig,
) -> Tuple[torch.Tensor, Dict[str, torch.Tensor], torch.Tensor]:
    """Forward one snapshot; return ``(total_loss, per_type_losses, diversity)``."""
    recon_dict, embeddings = model(graph.x_dict, graph.edge_index_dict)

    per_type: Dict[str, torch.Tensor] = {}
    for node_type, recon in recon_dict.items():
        if node_type in graph.x_dict:
            per_type[node_type] = criterion(recon, graph.x_dict[node_type])

    diversity = contrastive_diversity_loss(embeddings)
    total = sum(
        config.loss_weights.get(node_type, 0.0) * loss
        for node_type, loss in per_type.items()
    ) + config.diversity_weight * diversity
    return total, per_type, diversity


def _evaluate(model: HetGNN, graphs: Sequence, criterion, config: TrainConfig) -> Dict:
    """Mean losses over ``graphs`` with the model in eval mode (no dropout)."""
    totals: List[torch.Tensor] = []
    per_type: Dict[str, List[torch.Tensor]] = {}
    diversities: List[torch.Tensor] = []

    model.eval()
    with torch.no_grad():
        for graph in graphs:
            total, losses, diversity = _snapshot_losses(model, graph, criterion, config)
            totals.append(total.detach())
            diversities.append(diversity.detach())
            for node_type, loss in losses.items():
                per_type.setdefault(node_type, []).append(loss.detach())

    return {
        "total":     torch.stack(totals).mean().item() if totals else 0.0,
        "diversity": torch.stack(diversities).mean().item() if diversities else 0.0,
        "per_type":  {k: torch.stack(v).mean().item() for k, v in per_type.items()},
    }


def train_model(
    model: HetGNN,
    train_graphs: Sequence,
    val_graphs: Sequence,
    config: TrainConfig,
) -> Tuple[HetGNN, float, Dict[str, List[float]], int]:
    """Train in place; return ``(model, best_val_loss, history, epochs_run)``.

    Early stopping restores the best weights before returning, so the model
    handed back is the one that scored ``best_val_loss`` — not whatever the
    final epoch happened to produce.
    """
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=LR_FACTOR, patience=LR_PATIENCE, min_lr=LR_MIN
    )
    criterion = nn.MSELoss(reduction="mean")

    logger.info(
        "Training up to %d epochs, early stopping patience=%d, "
        "loss weights=%s, diversity=%s",
        config.epochs, config.early_stopping_patience,
        config.loss_weights, config.diversity_weight,
    )

    best_val_loss = float("inf")
    best_state = None
    epochs_without_improvement = 0
    history: Dict[str, List[float]] = {"train": [], "val": []}
    epoch = -1

    for epoch in range(config.epochs):
        model.train()
        for graph in train_graphs:
            optimizer.zero_grad()
            total, _, _ = _snapshot_losses(model, graph, criterion, config)
            total.backward()
            optimizer.step()

        # Re-measure the training loss in eval mode. Reporting the loss
        # accumulated during the epoch would mix dropout noise and mid-epoch
        # weight updates into a number we compare against a dropout-free
        # validation loss.
        train_metrics = _evaluate(model, train_graphs, criterion, config)
        val_metrics = _evaluate(model, val_graphs, criterion, config)
        train_loss, val_loss = train_metrics["total"], val_metrics["total"]

        history["train"].append(train_loss)
        history["val"].append(val_loss)

        scheduler.step(val_loss)
        current_lr = optimizer.param_groups[0]["lr"]

        if (epoch + 1) % 5 == 0:
            logger.info(
                "Epoch %d/%d, Train Loss: %.4f, Val Loss: %.4f, LR: %.6f",
                epoch + 1, config.epochs, train_loss, val_loss, current_lr,
            )
            logger.info("  Train - %s, Diversity: %.4f",
                        _fmt_per_type(train_metrics["per_type"]), train_metrics["diversity"])
            logger.info("  Val   - %s, Diversity: %.4f",
                        _fmt_per_type(val_metrics["per_type"]), val_metrics["diversity"])
        else:
            logger.debug("Epoch %d/%d, Train Loss: %.4f, Val Loss: %.4f",
                         epoch + 1, config.epochs, train_loss, val_loss)

        if val_loss < best_val_loss - config.min_delta:
            best_val_loss = val_loss
            epochs_without_improvement = 0
            # Clone, don't just copy the dict. `state_dict()` hands back live
            # references to the parameter tensors, so the previous
            # `state_dict().copy()` snapshotted nothing — the "best" weights
            # kept mutating with every optimizer step and the restore below was
            # a no-op. Detaching and cloning makes the restore real.
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            logger.info("  ✓ New best validation loss: %.4f", best_val_loss)
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.early_stopping_patience:
                logger.info(
                    "Early stopping after %d epochs (no improvement for %d)",
                    epoch + 1, config.early_stopping_patience,
                )
                break

    # Restore on every exit path, not just early stopping: the checkpoint
    # records `best_val_loss`, so the weights it ships must be the ones that
    # achieved it.
    if best_state is not None:
        model.load_state_dict(best_state)
        logger.info("Restored best model weights (val_loss=%.4f)", best_val_loss)

    return model, best_val_loss, history, epoch + 1


def _fmt_per_type(per_type: Dict[str, float]) -> str:
    return ", ".join(f"{k.capitalize()}: {v:.4f}" for k, v in sorted(per_type.items()))
