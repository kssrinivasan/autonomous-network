#!/bin/bash
#
# GNN Uninstall Script
# Cleans up Vertex AI GNN resources
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
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"

section "Cleaning up Vertex AI GNN resources"

# Delete Cloud Scheduler GNN inference trigger
echo "Checking for Cloud Scheduler job gnn-inference-scheduler..."
if gcloud scheduler jobs describe gnn-inference-scheduler --location=$GOOGLE_REGION >/dev/null 2>&1; then
    echo "Deleting Cloud Scheduler job gnn-inference-scheduler..."
    gcloud scheduler jobs delete gnn-inference-scheduler \
        --location=$GOOGLE_REGION --quiet 2>/dev/null || true
fi

# Get all endpoints and undeploy models first
ENDPOINTS=$(gcloud ai endpoints list --region=$GOOGLE_REGION --format="value(name)" 2>/dev/null || true)
if [ -n "$ENDPOINTS" ]; then
    for endpoint in $ENDPOINTS; do
        # Get endpoint display name to check if it's a GNN endpoint
        ENDPOINT_NAME=$(gcloud ai endpoints describe $endpoint --region=$GOOGLE_REGION --format="value(displayName)" 2>/dev/null || true)
        if [[ "$ENDPOINT_NAME" == *"gnn"* ]]; then
            echo "Undeploying models from endpoint: $ENDPOINT_NAME ($endpoint)"
            DEPLOYED_MODELS=$(gcloud ai endpoints describe $endpoint --region=$GOOGLE_REGION --format="value(deployedModels[].id)" 2>/dev/null || true)
            if [ -n "$DEPLOYED_MODELS" ]; then
                for model_id in $DEPLOYED_MODELS; do
                    echo "  Undeploying model: $model_id"
                    gcloud ai endpoints undeploy-model $endpoint --region=$GOOGLE_REGION --deployed-model-id=$model_id --quiet 2>/dev/null || true
                done
            fi
            echo "Deleting endpoint: $ENDPOINT_NAME ($endpoint)"
            gcloud ai endpoints delete $endpoint --region=$GOOGLE_REGION --quiet 2>/dev/null || true
        fi
    done
fi

# Delete Vertex AI models registered by the KFP pipeline
MODELS=$(gcloud ai models list --region=$GOOGLE_REGION --format="value(displayName,name)" 2>/dev/null || true)
if [ -n "$MODELS" ]; then
    while IFS=$'\t' read -r display_name model_id; do
        if [[ "$display_name" == *"gnn"* ]]; then
            echo "Deleting Vertex AI model: $display_name ($model_id)"
            gcloud ai models delete $model_id --region=$GOOGLE_REGION --quiet 2>/dev/null || true
        fi
    done <<< "$MODELS"
fi

# Note: Pipeline job cancellation is not possible via gcloud ai commands
# The 'gcloud ai pipeline-jobs' command does not exist
# Pipeline jobs are managed through the Vertex AI console or API directly
warn "Active pipeline jobs should be cancelled manually via Vertex AI console if needed"

ok "GNN resource cleanup complete"
