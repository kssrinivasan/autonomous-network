#!/bin/bash
#
# MCP Tools Installation Script
# Deploys Network Agent MCP tools server to Cloud Run
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
    info() { echo "ℹ $1"; }
    error() { echo "✗ $1"; }
fi

# Verify required environment variables
: "${GOOGLE_PROJECT:?GOOGLE_PROJECT must be set}"
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"
: "${GOOGLE_ZONE:?GOOGLE_ZONE must be set}"
: "${GOOGLE_REPO:?GOOGLE_REPO must be set}"

# Optional flags
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"
# shellcheck source=../lib/build.sh
. "$SCRIPT_DIR/../lib/build.sh"

section "MCP Tools — Cloud Run deployment"

# Generate cloudbuild.yaml from template
info "Generating cloudbuild.yaml from template..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

# Get service account
export GOOGLE_SERVICE_ACCOUNT=$(gcloud iam service-accounts list \
    --format="value(email)" \
    --filter="networkagent@${GOOGLE_PROJECT}." \
    --project="${GOOGLE_PROJECT}")

if [ -z "$GOOGLE_SERVICE_ACCOUNT" ]; then
    error "networkagent service account not found"
    exit 1
fi

# Build and push image
IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/sharedmcp:latest"

if should_build_image "$IMAGE_URI" "SharedMCP"; then
    gcloud builds submit --region=$GOOGLE_REGION --config "$SCRIPT_DIR/cloudbuild.yaml" "$SCRIPT_DIR/../.."
fi

# Get Gitea URL. Gitea is on GKE behind a reserved address; gitea-url.sh owns
# that lookup so the six copies of it cannot drift apart.
GITEA_URL=$(bash "$(dirname "${BASH_SOURCE[0]}")/../gitea/gitea-url.sh")

# Deploy to Cloud Run.
# min-instances 1 because a cold start sits directly on agent latency;
# max-instances 10 because every instance holds Spanner sessions.
info "Deploying sharedmcp to Cloud Run..."
gcloud run deploy sharedmcp \
    --image $IMAGE_URI \
    --region $GOOGLE_REGION \
    --min-instances 1 \
    --max-instances 10 \
    --memory 1Gi \
    --service-account $GOOGLE_SERVICE_ACCOUNT \
    --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
    --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
    --update-env-vars GOOGLE_ZONE=$GOOGLE_ZONE \
    --update-env-vars WEBAPPS_PWD=${WEBAPPS_PWD:-} \
    --update-env-vars WEBAPPS_LOGIN=${WEBAPPS_LOGIN:-admin} \
    --update-env-vars GITEA_URL=${GITEA_URL:-} \
    --update-env-vars GOOGLE_CLOUD_DISABLE_METRICS_EXPORT=true \
    --no-allow-unauthenticated

TOOLS_URL=$(gcloud run services describe sharedmcp \
    --region=$GOOGLE_REGION \
    --format="value(status.url)" 2>/dev/null)

if [ -z "$TOOLS_URL" ]; then
    error "Failed to get sharedmcp URL"
    exit 1
fi

PROJECT_NUMBER=$(gcloud projects describe ${GOOGLE_PROJECT} --format="value(projectNumber)")

# Set auth env vars now that TOOLS_URL and PROJECT_NUMBER are known
gcloud run services update sharedmcp \
    --region $GOOGLE_REGION \
    --update-env-vars GOOGLE_PROJECT_NUMBER=${PROJECT_NUMBER} \
    --update-env-vars MCP_AUDIENCE=${TOOLS_URL}

# Grant service accounts permission to invoke MCP server
info "Granting service accounts permission to invoke MCP server..."
REASONING_ENGINE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud run services add-iam-policy-binding sharedmcp \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:${REASONING_ENGINE_SA}" \
    --role="roles/run.invoker" \
    --quiet

gcloud run services add-iam-policy-binding sharedmcp \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/run.invoker" \
    --quiet



section "MCP Tools — Deployment Summary"
ok "MCP Tools deployed at $TOOLS_URL"
echo ""
echo "  To test the MCP server:"
echo "    npx @modelcontextprotocol/inspector"
echo ""
