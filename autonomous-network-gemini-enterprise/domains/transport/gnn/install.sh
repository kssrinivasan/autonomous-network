#!/bin/bash
#
# GNN Installation Script
# Deploys GNN training and serving infrastructure to Vertex AI
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source common utilities if available
if [ -f "$SCRIPT_DIR/../../../config.sh" ]; then
    # shellcheck source=../../../config.sh
    . "$SCRIPT_DIR/../../../config.sh"
else
    # Fallback: define minimal utilities
    section() { echo ""; echo "### $1"; }
    ok() { echo "✓ $1"; }
    warn() { echo "⚠ $1"; }
fi

# Verify required environment variables
: "${GOOGLE_PROJECT:?GOOGLE_PROJECT must be set}"
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"
: "${GOOGLE_REPO:?GOOGLE_REPO must be set}"
: "${GOOGLE_PROJECT_NUMBER:?GOOGLE_PROJECT_NUMBER must be set}"
: "${GOOGLE_GNN_BUCKET:?GOOGLE_GNN_BUCKET must be set}"
: "${GOOGLE_PIPELINE_ROOT:?GOOGLE_PIPELINE_ROOT must be set}"
: "${GOOGLE_SPANNER_INSTANCE:?GOOGLE_SPANNER_INSTANCE must be set}"
: "${GOOGLE_SPANNER_TRANSPORT_DATABASE:?GOOGLE_SPANNER_TRANSPORT_DATABASE must be set}"
: "${GOOGLE_USER:?GOOGLE_USER must be set}"

# Optional flags
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"

# Generate cloudbuild.yaml from template
echo "Generating cloudbuild.yaml from template..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

section "GNN Deployment — Vertex AI Pipelines"

export GOOGLE_SERVICE_ACCOUNT=$(gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}.")
TRAIN_VERTEX_IMAGE="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/traingnn-vertex:latest"

# ── 1. Build all GNN Docker images via Cloud Build ─────────────────────────
if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && \
   $(gcloud artifacts docker images describe $TRAIN_VERTEX_IMAGE >/dev/null 2>&1); then
    read -p "GNN Vertex AI images already exist. Rebuild? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gcloud builds submit --region=$GOOGLE_REGION --config="$SCRIPT_DIR/cloudbuild.yaml" "$SCRIPT_DIR/.."
    fi
elif [[ $NO_FLAG == "y" ]] && \
     $(gcloud artifacts docker images describe $TRAIN_VERTEX_IMAGE >/dev/null 2>&1); then
    echo "GNN images already exist - not rebuilding (NO_FLAG set)"
else
    echo "Building all GNN Docker images (Vertex AI training + serving + Cloud Run inference)..."
    gcloud builds submit --region=$GOOGLE_REGION --config="$SCRIPT_DIR/cloudbuild.yaml" "$SCRIPT_DIR/.."
fi

# ── 2. Create GNN artefacts GCS bucket ─────────────────────────────────────
echo "Ensuring GNN artefacts bucket gs://$GOOGLE_GNN_BUCKET exists..."
gcloud storage buckets describe gs://$GOOGLE_GNN_BUCKET > /dev/null 2>&1
if [[ $? -ne 0 ]]; then
    gcloud storage buckets create gs://$GOOGLE_GNN_BUCKET \
        --location=$GOOGLE_REGION \
        --project=$GOOGLE_PROJECT \
        --uniform-bucket-level-access
    echo "  Created gs://$GOOGLE_GNN_BUCKET"
fi
# Grant the Vertex AI Service Agent read access to Artifact Registry so it
# can pull the GNN training/serving container images when creating pipeline jobs.
echo "Granting Vertex AI Service Agent read access to Artifact Registry..."
gcloud artifacts repositories add-iam-policy-binding $GOOGLE_REPO \
    --location=$GOOGLE_REGION \
    --member="serviceAccount:service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform-cc.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader" \
    --project=$GOOGLE_PROJECT > /dev/null 2>&1

# ── 3. Check for existing model artifacts (decide: train or skip) ──────────
echo "Checking for existing model artifacts in central storage..."
LATEST_RUN_JSON="gs://$GOOGLE_GNN_BUCKET/models/latest/latest_run.json"
SKIP_TRAINING=false

if gsutil -q stat "$LATEST_RUN_JSON" 2>/dev/null; then
    # Artifacts exist - show info and ask user
    MANIFEST_CONTENT=$(gsutil cat "$LATEST_RUN_JSON" 2>/dev/null)
    RUN_ID=$(echo "$MANIFEST_CONTENT" | grep -o '"run_id": "[^"]*"' | cut -d'"' -f4)
    REGISTERED_AT=$(echo "$MANIFEST_CONTENT" | grep -o '"registered_at": "[^"]*"' | cut -d'"' -f4)
    VAL_LOSS=$(echo "$MANIFEST_CONTENT" | grep -o '"val_loss": [0-9.]*' | awk '{print $2}')

    echo ""
    echo "Found existing GNN model artifacts in gs://$GOOGLE_GNN_BUCKET/models/latest/"
    echo "  Run ID:          $RUN_ID"
    echo "  Registered:      $REGISTERED_AT"
    echo "  Validation Loss: $VAL_LOSS"
    echo ""

    # Prompt user: delete and retrain, or reuse artifacts
    if [[ $YES_FLAG == "y" ]]; then
        echo "YES_FLAG set - deleting artifacts and retraining"
        gsutil -m rm -r "gs://$GOOGLE_GNN_BUCKET/models/" 2>/dev/null || true
        echo "Artifacts deleted. Proceeding with full training pipeline..."
        SKIP_TRAINING=false
    elif [[ $NO_FLAG == "y" ]]; then
        echo "NO_FLAG set - reusing existing artifacts (skip training)"
        SKIP_TRAINING=true
    else
        read -p "Delete artifacts and retrain (y), or reuse existing artifacts (n)? " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Deleting artifacts and proceeding with full training..."
            gsutil -m rm -r "gs://$GOOGLE_GNN_BUCKET/models/" 2>/dev/null || true
            SKIP_TRAINING=false
        else
            echo "Reusing existing artifacts (skip training mode)..."
            SKIP_TRAINING=true
        fi
    fi
else
    echo "No existing artifacts found - will run full training pipeline"
    SKIP_TRAINING=false
fi
echo ""

# ── 4. Generate submit_pipeline.py from template ─────────────────────
echo "Regenerating gnn/src/pipeline/submit_pipeline.py..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_PIPELINE_ROOT}${GOOGLE_GNN_BUCKET}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/src/pipeline/submit_pipeline.tpl.py" > "$SCRIPT_DIR/src/pipeline/submit_pipeline.py"

# ── 5. Install pipeline submission deps and submit the pipeline ────────────
# Use python3 -m pip so the packages land in the same interpreter that will
# run submit_pipeline.py.  requirements.vertex.txt is the single source of
# truth for KFP SDK + Vertex AI pipeline deps.
echo "Installing pipeline submission dependencies (requirements.vertex.txt)..."
python3 -m pip install --quiet -r "$SCRIPT_DIR/requirements.vertex.txt"

# Build --skip-training flag based on artifact check above
SKIP_TRAINING_FLAG=""
if [[ $SKIP_TRAINING == true ]]; then
    SKIP_TRAINING_FLAG="--skip-training"
    echo "Submitting GNN pipeline in deploy-only mode (skipping training)..."
else
    echo "Submitting GNN training pipeline to Vertex AI Pipelines..."
fi

# Use the networkagent SA key so the Vertex AI SDK authenticates as the SA rather
# than falling back to gcloud ADC (which may be a different Google account).
# The SA has storage.objectAdmin on the bucket and can act as itself.
GOOGLE_APPLICATION_CREDENTIALS="$SCRIPT_DIR/src/networkagent.json" \
python3 "$SCRIPT_DIR/src/pipeline/submit_pipeline.py" \
    --project "$GOOGLE_PROJECT" \
    --region "$GOOGLE_REGION" \
    --pipeline-root "$GOOGLE_PIPELINE_ROOT" \
    --spanner-instance "$GOOGLE_SPANNER_INSTANCE" \
    --spanner-database "$GOOGLE_SPANNER_TRANSPORT_DATABASE" \
    --gcs-bucket "$GOOGLE_GNN_BUCKET" \
    --service-account "$GOOGLE_SERVICE_ACCOUNT" \
    $SKIP_TRAINING_FLAG

echo ""
echo "GNN deployment complete!"
echo "  Pipeline:   https://console.cloud.google.com/vertex-ai/pipelines?project=$GOOGLE_PROJECT"
echo "  Endpoint:   https://console.cloud.google.com/vertex-ai/endpoints?project=$GOOGLE_PROJECT"
echo "  Scheduler:  https://console.cloud.google.com/cloudscheduler?project=$GOOGLE_PROJECT"
echo "  (Cloud Scheduler job will be created/updated by the pipeline after training completes)"

ok "GNN deployed"
