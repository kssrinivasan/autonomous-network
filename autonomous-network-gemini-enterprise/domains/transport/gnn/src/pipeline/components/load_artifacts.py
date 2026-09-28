# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Load existing model artifacts from GCS (skip training)."""

from kfp.dsl import component, Output, Artifact


@component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-storage==2.16.0"],
)
def load_existing_artifacts(
    gcs_bucket: str,
    run_id: str,
    snapshots_gcs_path: Output[Artifact],
    scalers_gcs_path: Output[Artifact],
    model_gcs_path: Output[Artifact],
    val_loss: Output[float],
):
    """Load paths to existing artifacts from latest_run.json.

    This component is used when skip_training=True. It reads the manifest
    written by a previous pipeline run and outputs the artifact paths so
    the register/deploy steps can proceed without re-training.
    """
    import json
    import logging
    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("load_existing_artifacts")

    storage_client = storage.Client()
    bucket = storage_client.bucket(gcs_bucket)
    manifest_blob = bucket.blob("models/latest/latest_run.json")

    if not manifest_blob.exists():
        raise FileNotFoundError(
            f"No artifacts found at gs://{gcs_bucket}/models/latest/latest_run.json. "
            "Cannot skip training without existing artifacts."
        )

    manifest = json.loads(manifest_blob.download_as_text())
    hetgnn_info = manifest["models"]["hetgnn"]

    logger.info(f"Loading existing artifacts from run_id={manifest['run_id']}")

    # Construct paths based on the run_id from manifest
    # These match what the training pipeline would have created
    model_gcs_path.uri = hetgnn_info["gcs_path"]

    # Output[float] requires writing to .path as a file
    with open(val_loss.path, 'w') as f:
        f.write(str(hetgnn_info["val_loss"]))

    # Snapshots and scalers paths - reconstruct from run_id
    # The training pipeline stores these at: gs://bucket/{snapshots|scalers}/{run_id}/
    snapshots_gcs_path.uri = f"gs://{gcs_bucket}/snapshots/{run_id}"
    scalers_gcs_path.uri = f"gs://{gcs_bucket}/scalers/{run_id}"

    logger.info(f"Loaded artifacts:")
    logger.info(f"  Model: {model_gcs_path.uri}")
    logger.info(f"  Validation loss: {hetgnn_info['val_loss']}")
    logger.info(f"  Snapshots: {snapshots_gcs_path.uri}")
    logger.info(f"  Scalers: {scalers_gcs_path.uri}")
