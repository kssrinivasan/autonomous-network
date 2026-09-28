# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
GNN Training Pipeline — Vertex AI Pipelines (KFP v2)

Trains HetGNN on Vertex AI Custom Training Jobs, evaluates it, registers to
Vertex AI Model Registry, and conditionally deploys to a Vertex AI Endpoint.

Usage:
    python submit_pipeline.py --project=<PROJECT> --region=<REGION> \
        --pipeline-root=gs://<BUCKET>/pipeline-root
"""

from kfp import dsl
from kfp.dsl import pipeline

from components.ingest import ingest_snapshots
from components.fit_scalers import fit_scalers
from components.train import train_model
from components.evaluate import evaluate_model
from components.register import register_model
from components.deploy import deploy_endpoint
from components.update_scheduler import update_inference_scheduler
from components.load_artifacts import load_existing_artifacts


@pipeline(
    name="gnn-training-pipeline",
    description="Trains HetGNN on Vertex AI Custom Training Jobs",
)
def gnn_training_pipeline(
    project: str,
    region: str,
    train_image_uri: str,
    serve_image_uri: str,
    spanner_instance: str = "networktopology-instance",
    spanner_database: str = "transporttopology-db",
    service_database: str = "servicetopology-db",
    infra_database: str = "infratopology-db",
    gcs_bucket: str = "network-model-artifacts",
    # 360 snapshots at 30 s intervals = 3 real hours = ~30 compressed days.
    # Calibrated to fit inside metricscollector's RETENTION_HOURS=3 window
    # (logservices/metricscollector/src/main.py:40). Larger windows produced
    # zero-metric padding for the older third of snapshots — model learned
    # "all-zero metrics" as healthy → false positives at serve time when
    # live snapshots have populated metrics. 360 keeps every training
    # snapshot fully populated. Raise this if you also raise RETENTION_HOURS.
    # Each 30 s step = 2 simulated hours; 12 steps = one full compressed day.
    num_snapshots: int = 360,
    skip_training: bool = False,
    interval_minutes: float = 0.5,
    machine_type: str = "n1-standard-4",
    accelerator_type: str = "",
    accelerator_count: int = 0,
    max_hetgnn_val_loss: float = 1.0,
    endpoint_resource_name: str = "",
    service_account: str = "",
):
    run_id = dsl.PIPELINE_JOB_ID_PLACEHOLDER

    # Path 1: Full training pipeline
    with dsl.Condition(skip_training == False, name="train-and-deploy"):
        # Step 1 — Fetch snapshots from Spanner and serialise to GCS
        ingest_task = ingest_snapshots(
            spanner_instance=spanner_instance,
            spanner_database=spanner_database,
            service_database=service_database,
            infra_database=infra_database,
            project=project,
            num_snapshots=num_snapshots,
            interval_minutes=interval_minutes,
            gcs_bucket=gcs_bucket,
            run_id=run_id,
        )

        # Step 2 — Fit sklearn scalers from the serialised snapshots
        scalers_task = fit_scalers(
            snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
            gcs_bucket=gcs_bucket,
            run_id=run_id,
        )

        # Step 3 — Train HetGNN on a Vertex AI Custom Job
        hetgnn_train_task = train_model(
            project=project,
            region=region,
            model_name="hetgnn",
            train_image_uri=train_image_uri,
            snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
            scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
            gcs_bucket=gcs_bucket,
            run_id=run_id,
            machine_type=machine_type,
            accelerator_type=accelerator_type,
            accelerator_count=accelerator_count,
            service_account=service_account,
        )

        # Step 4 — Evaluate HetGNN on the held-out validation set
        hetgnn_eval_task = evaluate_model(
            project=project,
            region=region,
            model_name="hetgnn",
            model_gcs_path=hetgnn_train_task.outputs["model_gcs_path"],
            snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
            scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
            train_image_uri=train_image_uri,
            gcs_bucket=gcs_bucket,
            service_account=service_account,
        )

        # Step 5 — Register HetGNN to Vertex AI Model Registry
        register_task = register_model(
            project=project,
            region=region,
            serve_image_uri=serve_image_uri,
            hetgnn_model_gcs_path=hetgnn_train_task.outputs["model_gcs_path"],
            hetgnn_val_loss=hetgnn_eval_task.outputs["Output"],
            gcs_bucket=gcs_bucket,
            run_id=run_id,
            spanner_instance=spanner_instance,
            spanner_database=spanner_database,
            service_database=service_database,
            infra_database=infra_database,
        )

        # Step 6 — Conditional deployment: only deploy if val loss is below threshold
        with dsl.Condition(
            hetgnn_eval_task.outputs["Output"] < max_hetgnn_val_loss,
            name="hetgnn-quality-gate",
        ):
            deploy_task = deploy_endpoint(
                project=project,
                region=region,
                hetgnn_model_resource_name=register_task.outputs["hetgnn_model_resource_name"],
                endpoint_resource_name=endpoint_resource_name,
                service_account=service_account,
            )

            # Step 7 — Update Cloud Scheduler to call the newly deployed endpoint
            update_inference_scheduler(
                project=project,
                region=region,
                service_account=service_account,
                deployed_endpoint=deploy_task.outputs["deployed_endpoint"],
            )

    # Path 2: Deploy from existing artifacts
    with dsl.Condition(skip_training == True, name="deploy-from-artifacts"):
        # Load existing artifacts from latest_run.json
        load_task = load_existing_artifacts(
            gcs_bucket=gcs_bucket,
            run_id=run_id,
        )

        # Register the loaded model to Vertex AI Model Registry
        register_task_skip = register_model(
            project=project,
            region=region,
            serve_image_uri=serve_image_uri,
            hetgnn_model_gcs_path=load_task.outputs["model_gcs_path"],
            hetgnn_val_loss=load_task.outputs["val_loss"],
            gcs_bucket=gcs_bucket,
            run_id=run_id,
            spanner_instance=spanner_instance,
            spanner_database=spanner_database,
            service_database=service_database,
            infra_database=infra_database,
        )

        # Always deploy when using existing artifacts (they already passed quality gate)
        deploy_task_skip = deploy_endpoint(
            project=project,
            region=region,
            hetgnn_model_resource_name=register_task_skip.outputs["hetgnn_model_resource_name"],
            endpoint_resource_name=endpoint_resource_name,
            service_account=service_account,
        )

        # Update Cloud Scheduler to call the newly deployed endpoint
        update_inference_scheduler_skip = update_inference_scheduler(
            project=project,
            region=region,
            service_account=service_account,
            deployed_endpoint=deploy_task_skip.outputs["deployed_endpoint"],
        )
