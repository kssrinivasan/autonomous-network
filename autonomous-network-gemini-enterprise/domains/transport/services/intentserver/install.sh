#!/bin/bash
# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Source environment
if [ -f "$REPO_ROOT/config.sh" ]; then
    source "$REPO_ROOT/config.sh"
else
    echo "Error: config.sh not found. Run from repository root."
    exit 1
fi

# Optional flags from root install.sh
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"

echo "========================================="
echo "Transport Intent API Server - Deployment"
echo "========================================="
echo ""

# Check for required tools
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required but not installed"
    echo "Install with: sudo apt-get install jq (Debian/Ubuntu) or brew install jq (macOS)"
    exit 1
fi

# Validate required environment variables
if [ -z "$GOOGLE_PROJECT" ]; then
    echo "Error: GOOGLE_PROJECT not set"
    exit 1
fi

if [ -z "$GOOGLE_REGION" ]; then
    echo "Error: GOOGLE_REGION not set"
    exit 1
fi

echo "Configuration:"
echo "  Project: $GOOGLE_PROJECT"
echo "  Region: $GOOGLE_REGION"
echo "  Repository: $GOOGLE_REPO"
echo "  Spanner Instance: $GOOGLE_SPANNER_INSTANCE"
echo "  Spanner Database: $GOOGLE_SPANNER_OPS_DATABASE"
echo ""

# Render cloudbuild.yaml from template
echo "Rendering cloudbuild.yaml..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

# Build container image
IMAGE_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/transport-intentserver:latest"

if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
    read -p "Intent server image already exists. Rebuild? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "Building container image..."
        gcloud builds submit --region=$GOOGLE_REGION \
            --config "$SCRIPT_DIR/cloudbuild.yaml" "$REPO_ROOT"
    fi
elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
    echo "Intent server image already exists - skipping rebuild (NO_FLAG set)"
else
    echo ""
    echo "Building container image..."
    gcloud builds submit --region=$GOOGLE_REGION \
        --config "$SCRIPT_DIR/cloudbuild.yaml" "$REPO_ROOT"
fi

# Get orchestrator URL from deployment metadata
echo ""
echo "Discovering Transport Orchestrator URL..."
ORCHESTRATOR_METADATA="$REPO_ROOT/domains/transport/agents/transport-orchestrator/deployment_metadata.json"

if [ ! -f "$ORCHESTRATOR_METADATA" ]; then
    echo "Warning: transport-orchestrator deployment metadata not found."
    echo "         Deploy the orchestrator first:"
    echo "           cd domains/transport/agents && ./deploy_agents.py -d transport-orchestrator"
    echo "         API server will be deployed but will not function without orchestrator."
    ORCHESTRATOR_A2A_URL=""
else
    # Extract the Reasoning Engine resource ID
    REASONING_ENGINE_ID=$(jq -r '.remote_agent_runtime_id // empty' "$ORCHESTRATOR_METADATA")

    if [ -z "$REASONING_ENGINE_ID" ] || [ "$REASONING_ENGINE_ID" = "null" ]; then
        echo "Warning: No remote_agent_runtime_id found in deployment_metadata.json"
        echo "         Deploy the orchestrator first:"
        echo "           cd domains/transport/agents && ./deploy_agents.py -d transport-orchestrator"
        ORCHESTRATOR_A2A_URL=""
    else
        # Construct the A2A URL from the Reasoning Engine resource ID
        # Pattern: https://{region}-aiplatform.googleapis.com/reasoningEngines/v1/{resource_id}/api/a2a/app
        ORCHESTRATOR_A2A_URL="https://${GOOGLE_REGION}-aiplatform.googleapis.com/reasoningEngines/v1/${REASONING_ENGINE_ID}/api/a2a/app"
        echo "  Orchestrator A2A URL: $ORCHESTRATOR_A2A_URL"
    fi
fi

# Get service account (support both short name and full email in GOOGLE_SERVICE_ACCOUNT)
SA_BASE="${GOOGLE_SERVICE_ACCOUNT:-networkagent}"
if [[ "$SA_BASE" == *"@"* ]]; then
    SERVICE_ACCOUNT="$SA_BASE"
else
    SERVICE_ACCOUNT="${SA_BASE}@${GOOGLE_PROJECT}.iam.gserviceaccount.com"
fi

# Deploy to Cloud Run
echo ""
echo "Deploying to Cloud Run..."

# This service exposes POST /intent, PATCH and DELETE -- it mutates state and
# reaches the orchestrator with the networkagent SA's own credentials. It was
# previously deployed --allow-unauthenticated, which put those verbs on the
# public internet. Callers now need an identity token; grant run.invoker to
# whoever legitimately needs to call it.
gcloud run deploy transport-intentserver \
    --image "$IMAGE_URI" \
    --region "$GOOGLE_REGION" \
    --platform managed \
    --service-account "$SERVICE_ACCOUNT" \
    --no-allow-unauthenticated \
    --memory 512Mi \
    --timeout 300 \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 10 \
    --update-env-vars \
GOOGLE_PROJECT="${GOOGLE_PROJECT}",\
GOOGLE_REGION="${GOOGLE_REGION}",\
SPANNER_INSTANCE="${GOOGLE_SPANNER_INSTANCE}",\
GOOGLE_SPANNER_OPS_DATABASE="${GOOGLE_SPANNER_OPS_DATABASE}",\
ORCHESTRATOR_URL="${ORCHESTRATOR_A2A_URL}"

# Get service URL
API_URL=$(gcloud run services describe transport-intentserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)')

echo ""
echo "========================================="
echo "Transport Intent API Server Deployed!"
echo "========================================="
echo ""
echo "API URL: $API_URL"
echo ""
echo "Endpoints:"
echo "  POST   $API_URL/intent       - Create new intent"
echo "  GET    $API_URL/intents      - List all intents"
echo "  GET    $API_URL/intent/{id}  - Get intent status"
echo "  DELETE $API_URL/intent/{id}  - Delete intent"
echo ""
echo "This service requires authentication. Call it with an identity token:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \\"
echo "       $API_URL/intents"
echo ""
echo "To let another principal call it:"
echo "  gcloud run services add-iam-policy-binding transport-intentserver \\"
echo "      --region=$GOOGLE_REGION --member=<principal> --role=roles/run.invoker"
echo ""
echo "Test with:"
echo "  curl ${API_URL}/health"
echo "  curl ${API_URL}/intent -X POST -H 'Content-Type: application/json' -d @intent.json"
echo ""

# Save URL for reference
echo "$API_URL" > "$SCRIPT_DIR/.api_url"
