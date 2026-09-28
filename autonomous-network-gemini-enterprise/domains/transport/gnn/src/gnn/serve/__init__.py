"""gnn.serve — backend-agnostic scoring for the prediction endpoint."""

from gnn.serve.inference import InferenceResult, score_snapshot

__all__ = ["score_snapshot", "InferenceResult"]
