# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Fit sklearn scalers and build global_id_map from GCS snapshots.

Runs inside the traingnn-vertex Docker image so GraphBuilder (from
utils/gnn_utils.py) can be imported directly — the same class used by the
training scripts and serve.py.

The output scalers.pkl produced here is consumed by all three training
components (DGAT, HetGNN, STGNN) and by the inference / serving code.
"""

import os

from kfp.dsl import component, Input, Output, Artifact

_BASE_IMAGE = os.environ.get("GNN_TRAIN_IMAGE_URI", "python:3.12-slim")


@component(base_image=_BASE_IMAGE)
def fit_scalers(
    snapshots_gcs_path: Input[Artifact],
    gcs_bucket: str,
    run_id: str,
    scalers_gcs_path: Output[Artifact],
) -> str:
    """Fits sklearn StandardScalers and builds the global_id_map from snapshot data.

    Reads all snapshot pickle files from GCS, passes them to
    GraphBuilder.fit_scalers() (the authoritative scaler logic in gnn_utils.py),
    then uploads the resulting scalers.pkl to GCS via joblib.

    The output file has the structure:
        {"scalers": {node_type: {metric: StandardScaler}},
         "id_map":  {node_type: {node_id: int_index}}}

    This matches exactly what GraphBuilder.load_scalers() and
    GraphBuilder.process_snapshot() expect downstream.

    Returns:
        GCS path (gs://…) to the scalers directory.
    """
    import io
    import logging
    import pickle
    import sys

    import joblib
    from google.cloud import storage

    # /app is the WORKDIR for all GNN Docker images (ADD gnn/src/ /app)
    sys.path.insert(0, "/app")
    from utils.gnn_utils import GraphBuilder  # noqa: E402  (runtime import)

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("fit_scalers")

    storage_client = storage.Client()
    bucket = storage_client.bucket(gcs_bucket)

    # snapshots_gcs_path.uri is "gs://<bucket>/<prefix>"
    snap_prefix = snapshots_gcs_path.uri.replace(f"gs://{gcs_bucket}/", "")
    blobs = list(bucket.list_blobs(prefix=snap_prefix))
    pkl_blobs = sorted(
        [b for b in blobs if b.name.endswith(".pkl")],
        key=lambda b: b.name,
    )

    logger.info(
        f"Loading {len(pkl_blobs)} snapshot pickle files from "
        f"{snapshots_gcs_path.uri}"
    )

    if not pkl_blobs:
        raise RuntimeError(
            f"No .pkl snapshot files found under {snapshots_gcs_path.uri}. "
            "Check that the ingest component completed successfully."
        )

    snapshots = []
    for blob in pkl_blobs:
        snapshot = pickle.loads(blob.download_as_bytes())
        if snapshot.get("nodes"):
            snapshots.append(snapshot)
        else:
            logger.warning(f"Skipping empty snapshot from {blob.name}")

    logger.info(f"Loaded {len(snapshots)} non-empty snapshots")

    # Use the module-level fit_scalers() which is the canonical scaler logic
    # shared by train_hetgnn.py and serve.py.  GraphBuilder does not have
    # instance methods for fitting — scalers are passed in at construction time.
    sys.path.insert(0, "/app")
    from utils.gnn_utils import fit_scalers as _fit_scalers, NODE_TYPES  # noqa: E402

    scalers = _fit_scalers(snapshots)

    logger.info(
        f"Scalers fitted for node types: "
        f"{[nt for nt in NODE_TYPES if nt in scalers]}"
    )

    # Serialize scalers with joblib (the same format train_hetgnn.py and
    # serve.py expect: a plain dict of {node_type: StandardScaler}).
    output_gcs_prefix = f"scalers/{run_id}"
    scaler_blob_name = f"{output_gcs_prefix}/scalers.pkl"

    buf = io.BytesIO()
    joblib.dump(scalers, buf)
    buf.seek(0)
    bucket.blob(scaler_blob_name).upload_from_file(buf, content_type="application/octet-stream")

    output_path = f"gs://{gcs_bucket}/{output_gcs_prefix}"
    logger.info(f"Uploaded scalers to {output_path}/scalers.pkl")

    scalers_gcs_path.uri = output_path
    return output_path
