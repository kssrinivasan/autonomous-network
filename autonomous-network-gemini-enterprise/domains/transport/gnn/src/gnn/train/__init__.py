"""gnn.train — backend-agnostic training orchestration and artefact bundles."""

from gnn.train.run import collect_embeddings, train_from_snapshots

__all__ = ["train_from_snapshots", "collect_embeddings"]
