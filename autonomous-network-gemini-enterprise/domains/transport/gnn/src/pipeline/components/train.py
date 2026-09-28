# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Submit a Vertex AI Custom Training Job for one GNN model."""

from kfp.dsl import component, Input, Output, Artifact


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform==1.49.0",
    ],
)
def train_model(
    project: str,
    region: str,
    model_name: str,
    train_image_uri: str,
    snapshots_gcs_path: Input[Artifact],
    scalers_gcs_path: Input[Artifact],
    gcs_bucket: str,
    run_id: str,
    machine_type: str,
    accelerator_type: str,
    accelerator_count: int,
    service_account: str,
    model_gcs_path: Output[Artifact],
) -> str:
    """Submits a Vertex AI Custom Training Job for a single GNN model.

    The training container is driven by the MODEL_NAME environment variable
    which selects the appropriate train_*.py script inside the container.

    Args:
        model_name: One of 'dgat', 'hetgnn', or 'stgnn'.

    Returns:
        GCS path to the directory containing model.pth and model_stats.pth.
    """
    import logging
    from google.cloud import aiplatform

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(f"train_{model_name}")

    aiplatform.init(project=project, location=region, staging_bucket=f"gs://{gcs_bucket}")

    output_path = f"gs://{gcs_bucket}/models/{model_name}/{run_id}"

    machine_spec: dict = {"machine_type": machine_type}
    if accelerator_count > 0 and accelerator_type:
        machine_spec["accelerator_type"] = accelerator_type
        machine_spec["accelerator_count"] = accelerator_count

    worker_pool_specs = [
        {
            "machine_spec": machine_spec,
            "replica_count": 1,
            "disk_spec": {
                "boot_disk_type": "pd-ssd",
                "boot_disk_size_gb": 100,
            },
            "container_spec": {
                "image_uri": train_image_uri,
                "env": [
                    {"name": "MODEL_NAME", "value": model_name},
                    {"name": "GCS_BUCKET_NAME", "value": gcs_bucket},
                    {"name": "SNAPSHOTS_GCS_PATH", "value": snapshots_gcs_path.uri},
                    {"name": "SCALERS_GCS_PATH", "value": scalers_gcs_path.uri},
                    {"name": "AIP_MODEL_DIR", "value": output_path},
                    {"name": "MODEL_OUTPUT_PATH", "value": output_path},
                ],
            },
        }
    ]

    job = aiplatform.CustomJob(
        display_name=f"gnn-train-{model_name}-{run_id[:8]}",
        worker_pool_specs=worker_pool_specs,
        base_output_dir=output_path,
        project=project,
        location=region,
    )

    logger.info(f"Submitting Vertex AI training job for {model_name}...")
    job.run(
        service_account=service_account if service_account else None,
        sync=True,  # Wait for job completion before returning
        restart_job_on_worker_restart=False,
        timeout=7200,  # Fail after 2 h instead of hanging forever (e.g. on quota PENDING)
    )

    if job.state.name != "JOB_STATE_SUCCEEDED":
        raise RuntimeError(
            f"Training job for {model_name} failed with state: {job.state.name}"
        )

    logger.info(f"Training complete for {model_name}. Artefacts at: {output_path}")
    model_gcs_path.uri = output_path
    return output_path
