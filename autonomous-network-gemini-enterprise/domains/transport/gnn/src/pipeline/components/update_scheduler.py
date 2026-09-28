# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Create or update the Cloud Scheduler job that triggers
periodic GNN inference via the Vertex AI Endpoint."""

from kfp.dsl import component, Input, Artifact


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-scheduler==2.13.5",
        "google-auth==2.29.0",
    ],
)
def update_inference_scheduler(
    project: str,
    region: str,
    service_account: str,
    deployed_endpoint: Input[Artifact],
    schedule: str = "* * * * *",
    job_name: str = "gnn-inference-scheduler",
) -> str:
    """Creates or updates a Cloud Scheduler job that calls the Vertex AI
    Endpoint's /predict route on the given cron schedule.

    The Vertex AI Endpoint is called with OAuth 2.0 (access token) authentication.
    OIDC (ID tokens) are rejected by the Vertex AI Predict API — OAuth with
    cloud-platform scope is required.

    Args:
        schedule: Cron expression (default: every minute).
        job_name: Cloud Scheduler job name (idempotent — updated if exists).

    Returns:
        The full resource name of the Cloud Scheduler job.
    """
    import logging
    from google.cloud import scheduler_v1
    from google.protobuf import duration_pb2

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("update_inference_scheduler")

    endpoint_resource_name = deployed_endpoint.uri  # e.g. projects/.../endpoints/12345

    # Build the Vertex AI prediction URL from the endpoint resource name.
    # Format: https://{region}-aiplatform.googleapis.com/v1/{resource_name}:predict
    predict_url = (
        f"https://{region}-aiplatform.googleapis.com/v1/"
        f"{endpoint_resource_name}:predict"
    )
    logger.info(f"Endpoint predict URL: {predict_url}")

    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{project}/locations/{region}"
    job_resource_name = f"{parent}/jobs/{job_name}"

    job = scheduler_v1.Job(
        name=job_resource_name,
        description="Trigger GNN HetGNN inference via Vertex AI Endpoint every minute",
        schedule=schedule,
        time_zone="UTC",
        http_target=scheduler_v1.HttpTarget(
            uri=predict_url,
            http_method=scheduler_v1.HttpMethod.POST,
            body=b'{"instances": [{}]}',
            headers={"Content-Type": "application/json"},
            oauth_token=scheduler_v1.OAuthToken(
                service_account_email=service_account,
                scope="https://www.googleapis.com/auth/cloud-platform",
            ),
        ),
        attempt_deadline=duration_pb2.Duration(seconds=320),
    )

    try:
        # Try to update an existing job first
        existing = client.get_job(name=job_resource_name)
        logger.info(f"Updating existing Cloud Scheduler job: {job_resource_name}")
        result = client.update_job(job=job)
        logger.info(f"Scheduler job updated: {result.name}")
    except Exception:
        # Job doesn't exist — create it
        logger.info(f"Creating Cloud Scheduler job: {job_resource_name}")
        result = client.create_job(parent=parent, job=job)
        logger.info(f"Scheduler job created: {result.name}")

    return result.name
