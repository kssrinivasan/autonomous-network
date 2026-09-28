# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Extract validation loss from the trained model checkpoint."""

from kfp.dsl import component, Input, Output, Artifact, Metrics


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-storage==2.16.0",
        "torch==2.2.2",
    ],
)
def evaluate_model(
    project: str,
    region: str,
    model_name: str,
    model_gcs_path: Input[Artifact],
    snapshots_gcs_path: Input[Artifact],
    scalers_gcs_path: Input[Artifact],
    train_image_uri: str,
    gcs_bucket: str,
    service_account: str,
    metrics: Output[Metrics],
) -> float:
    """Extracts the validation loss recorded during training from the model checkpoint.

    The training step (train_hetgnn_on_snapshots) runs a full train/val split
    and stores ``best_val_loss`` inside ``model.pth`` alongside the model weights.
    This component downloads that checkpoint and reads the pre-computed metric —
    no second Custom Job or extra VM is required.

    Args:
        model_name: One of 'dgat', 'hetgnn', or 'stgnn'.

    Returns:
        val_loss (float) — primary metric used by the quality-gate condition.
    """
    import io
    import logging
    import tempfile

    import torch
    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(f"evaluate_{model_name}")

    # ── Locate model.pth inside model_gcs_path ────────────────────────────────
    # model_gcs_path.uri is set to AIP_MODEL_DIR by the train component, e.g.
    #   gs://<bucket>/pipeline-runs/<run_id>/train/model/
    model_uri = model_gcs_path.uri.rstrip("/") + "/model.pth"

    bucket_name = model_uri.removeprefix("gs://").split("/")[0]
    blob_path   = "/".join(model_uri.removeprefix("gs://").split("/")[1:])

    logger.info(f"Downloading checkpoint from gs://{bucket_name}/{blob_path}")

    storage_client = storage.Client(project=project)
    bucket = storage_client.bucket(bucket_name)
    blob   = bucket.blob(blob_path)

    val_loss = 9999.0
    try:
        checkpoint_bytes = blob.download_as_bytes()
        checkpoint = torch.load(io.BytesIO(checkpoint_bytes), map_location="cpu")
        val_loss = float(checkpoint.get("val_loss", 9999.0))
        logger.info(f"Checkpoint loaded — val_loss={val_loss:.4f}")
    except Exception as exc:
        logger.warning(
            f"Could not read val_loss from checkpoint: {exc}. "
            "Using fallback val_loss=9999."
        )

    metrics.log_metric(f"{model_name}/val_loss", val_loss)
    logger.info(f"Evaluation complete for {model_name}: val_loss={val_loss}")

    return val_loss
