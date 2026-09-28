#!/bin/bash
#
# Metrics Collector Installation Script
# Deploys VyOS metrics collector Cloud Run worker pool
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source common utilities if available
if [ -f "$SCRIPT_DIR/../../config.sh" ]; then
    # shellcheck source=../../config.sh
    . "$SCRIPT_DIR/../../config.sh"
else
    # Fallback: define minimal utilities
    section() { echo ""; echo "### $1"; }
    ok() { echo "✓ $1"; }
    warn() { echo "⚠ $1"; }
fi

# Verify required environment variables
if [ -z "${GOOGLE_PROJECT}" ]; then
  echo "Error: GOOGLE_PROJECT must be set"
  exit 1
fi
if [ -z "${GOOGLE_REGION}" ]; then
  echo "Error: GOOGLE_REGION must be set"
  exit 1
fi
if [ -z "${GOOGLE_ZONE}" ]; then
  echo "Error: GOOGLE_ZONE must be set"
  exit 1
fi
if [ -z "${GOOGLE_REPO}" ]; then
  echo "Error: GOOGLE_REPO must be set"
  exit 1
fi
if [ -z "${GOOGLE_SPANNER_INSTANCE}" ]; then
  echo "Error: GOOGLE_SPANNER_INSTANCE must be set"
  exit 1
fi
if [ -z "${GOOGLE_SPANNER_TRANSPORT_DATABASE}" ]; then
  echo "Error: GOOGLE_SPANNER_TRANSPORT_DATABASE must be set"
  exit 1
fi
if [ -z "${GOOGLE_SPANNER_SERVICE_DATABASE}" ]; then
  echo "Error: GOOGLE_SPANNER_SERVICE_DATABASE must be set"
  exit 1
fi

# Optional flags
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"
# shellcheck source=../lib/build.sh
. "$SCRIPT_DIR/../lib/build.sh"

section "Metrics Collector Deployment — VyOS metrics to Spanner"

export GOOGLE_SERVICE_ACCOUNT=$(gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}.")

cd "$SCRIPT_DIR"

# Build and push image
IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/metricscollector:latest"
if should_build_image "$IMAGE_URI" "Metrics collector"; then
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
fi

# Deploy Cloud Run worker pool
echo "Deploying metrics collector worker pool..."
gcloud beta run worker-pools deploy metricscollector \
    --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/metricscollector:latest \
    --region $GOOGLE_REGION \
    --instances 1 \
    --cpu 1 \
    --memory 1Gi \
    --service-account $GOOGLE_SERVICE_ACCOUNT \
    --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
    --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
    --update-env-vars GOOGLE_ZONE=$GOOGLE_ZONE \
    --update-env-vars GOOGLE_SPANNER_INSTANCE=$GOOGLE_SPANNER_INSTANCE \
    --update-env-vars GOOGLE_SPANNER_TRANSPORT_DATABASE=$GOOGLE_SPANNER_TRANSPORT_DATABASE \
    --update-env-vars GOOGLE_SPANNER_SERVICE_DATABASE=$GOOGLE_SPANNER_SERVICE_DATABASE \
    --update-env-vars POLL_INTERVAL=15 \
    --update-env-vars BQ_DATASET=serviceperformance \
    --update-env-vars GOOGLE_CLOUD_DISABLE_METRICS_EXPORT=true

cd - > /dev/null

ok "Metrics collector deployed"
