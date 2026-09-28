# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Fetch network snapshots from Spanner and serialise to GCS.

Runs inside the traingnn-vertex Docker image so utils.data.SpannerDataset
(and its SCD Type 2 Spanner query logic) can be imported directly from
/app/utils/data.py — the same code path used by serve.py and infer.py.

The base image URI is injected at pipeline compile time via the
GNN_TRAIN_IMAGE_URI environment variable (set by submit_pipeline.py before
importing the pipeline module).
"""

import os

from kfp.dsl import component, Output, Artifact

# Read the image URI at module-load time so the @component decorator picks it
# up correctly.  submit_pipeline.py sets this env var before importing pipeline.
_BASE_IMAGE = os.environ.get("GNN_TRAIN_IMAGE_URI", "python:3.12-slim")


@component(base_image=_BASE_IMAGE)
def ingest_snapshots(
    spanner_instance: str,
    spanner_database: str,
    project: str,
    num_snapshots: int,
    interval_minutes: float,
    gcs_bucket: str,
    run_id: str,
    snapshots_gcs_path: Output[Artifact],
    service_database: str = "servicetopology-db",
    infra_database: str = "infratopology-db",
) -> str:
    """Fetches snapshots from Spanner via SpannerDataset and writes them to GCS.

    The function body runs inside the traingnn-vertex container on Vertex AI,
    where /app contains the full gnn/src/ tree (utils/data.py, model/, etc.).
    sys.path.insert(0, '/app') makes SpannerDataset importable without any
    extra packages_to_install.

    SpannerDataset uses ADC (Application Default Credentials) automatically
    on Vertex AI — no key file is needed.

    Returns:
        GCS path (gs://…) to the directory of snapshot .pkl files.
    """
    import json
    import logging
    import os
    import pickle
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime

    from google.cloud import storage

    # /app is the WORKDIR for all GNN Docker images (ADD gnn/src/ /app)
    sys.path.insert(0, "/app")
    from utils.data import SpannerDataset  # noqa: E402  (runtime import)

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("ingest_snapshots")

    gcs_prefix = f"snapshots/{run_id}"
    output_gcs_path = f"gs://{gcs_bucket}/{gcs_prefix}"

    storage_client = storage.Client()
    bucket = storage_client.bucket(gcs_bucket)

    # SpannerDataset falls back to ADC when no credentials file is present
    logger.info(
        f"Initializing SpannerDataset: instance={spanner_instance}, "
        f"db={spanner_database}, service_db={service_database}, "
        f"infra_db={infra_database}, snapshots={num_snapshots}, "
        f"interval={interval_minutes}m"
    )
    dataset = SpannerDataset(
        instance_id=spanner_instance,
        database_id=spanner_database,
        service_database_id=service_database,
        infra_database_id=infra_database,
        num_snapshots=num_snapshots,
        interval_minutes=interval_minutes,
        project_id=project,
    )

    timestamps = dataset._get_timestamps()
    logger.info(f"Fetching {len(timestamps)} point-in-time snapshots from Spanner...")

    def _fetch(ts):
        try:
            snap = dataset.fetch_snapshot(ts)
            if snap.get("nodes"):
                return snap
            logger.warning(f"Empty snapshot at {ts.isoformat()} — skipped")
        except Exception as e:
            logger.error(f"Failed to fetch snapshot at {ts.isoformat()}: {e}")
        return None

    # Parallel fetch — Spanner client is thread-safe; each worker opens its own
    # snapshot session via `with self.database.snapshot(multi_use=True) as sn:`
    # so no shared state to break. Cap at 8 to stay well under the 100 PU instance
    # limit while running concurrently with metricscollector. ThreadPoolExecutor.map
    # preserves input order, which compute_temporal_features() requires below.
    # Each snapshot fetches its OWN topology — necessary for fault detection where
    # the SCD topology tables change across the training window.
    max_workers = int(os.getenv("INGEST_PARALLELISM", "8"))
    logger.info(f"Fetching {len(timestamps)} snapshots with parallelism={max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_fetch, timestamps))
    snapshots = [s for s in results if s is not None]

    logger.info(f"Fetched {len(snapshots)} non-empty snapshots")

    if len(snapshots) < num_snapshots:
        logger.warning(
            f"Retrieved {len(snapshots)} snapshots out of {num_snapshots} requested. "
            f"Training will proceed with available data. "
            f"This may occur if Spanner does not have enough historical data for the requested time range."
        )

    if not snapshots:
        raise RuntimeError(
            f"No snapshots fetched from Spanner ({spanner_instance}/{spanner_database}). "
            "Check that the network operator is running and has written topology data."
        )

    # Compute temporal gradient / delta features across the ordered snapshot sequence.
    # This must be done before serialising — the temporal features (rx_err_gradient,
    # prefix_count_delta, vrf_route_count_delta, throughput_delta) require at least
    # two consecutive snapshots and cannot be recomputed from individual pickle files.
    logger.info("Computing temporal features across snapshot sequence...")
    dataset.compute_temporal_features(snapshots, interval_seconds=interval_minutes * 60)
    logger.info("Temporal features computed")

    # Write each snapshot as a pickle file and JSON file to GCS
    for i, snapshot in enumerate(snapshots):
        blob_name = f"{gcs_prefix}/snapshot_{i:04d}.pkl"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(pickle.dumps(snapshot))

        json_blob = bucket.blob(f"{gcs_prefix}/snapshot_{i:04d}.json")
        json_blob.upload_from_string(json.dumps(snapshot, indent=2))

    logger.info(f"Wrote {len(snapshots)} snapshot pickle+JSON files to {output_gcs_path}")

    # Write manifest so downstream components can verify the run
    manifest = {
        "run_id": run_id,
        "num_snapshots": len(snapshots),
        "gcs_path": output_gcs_path,
        "spanner_instance": spanner_instance,
        "spanner_database": spanner_database,
        "created_at": datetime.utcnow().isoformat(),
    }
    manifest_blob = bucket.blob(f"{gcs_prefix}/manifest.json")
    manifest_blob.upload_from_string(json.dumps(manifest, indent=2))
    logger.info(f"Wrote manifest to {output_gcs_path}/manifest.json")

    snapshots_gcs_path.uri = output_gcs_path
    return output_gcs_path
