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

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Source environment
if [ -f "$REPO_ROOT/config.sh" ]; then
    source "$REPO_ROOT/config.sh"
else
    echo "Error: config.sh not found. Run from repository root."
    exit 1
fi

echo "========================================="
echo "Multi-Domain MCP Server - Deployment"
echo "========================================="
echo ""

# Validate required environment variables
if [ -z "$GOOGLE_PROJECT" ]; then
    echo "Error: GOOGLE_PROJECT not set"
    exit 1
fi

if [ -z "$GOOGLE_REGION" ]; then
    echo "Error: GOOGLE_REGION not set"
    exit 1
fi

# Set defaults
GOOGLE_REPO=${GOOGLE_REPO:-"network-agent"}

echo "Configuration:"
echo "  Project: $GOOGLE_PROJECT"
echo "  Region: $GOOGLE_REGION"
echo "  Repository: $GOOGLE_REPO"
echo ""

# Render cloudbuild.yaml from template
echo "Rendering cloudbuild.yaml..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

# Build container image
echo ""
echo "Building container image..."
gcloud builds submit --region=$GOOGLE_REGION \
    --config "$SCRIPT_DIR/cloudbuild.yaml" "$REPO_ROOT"

# Discover all domain API URLs
echo ""
echo "Discovering domain API URLs..."

# Transport Domain
echo "  Transport Domain..."
TRANSPORT_INTENT_API_URL=$(gcloud run services describe transport-apiserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")
TRANSPORT_TOPOLOGY_API_URL=$(gcloud run services describe transport-topologyserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")

# Core Domain
echo "  Core Domain..."
CORE_INTENT_API_URL=$(gcloud run services describe core-apiserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")
CORE_TOPOLOGY_API_URL=$(gcloud run services describe core-topologyserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")

# Radio Domain
echo "  Radio Domain..."
RADIO_INTENT_API_URL=$(gcloud run services describe radio-apiserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")
RADIO_TOPOLOGY_API_URL=$(gcloud run services describe radio-topologyserver \
    --region=$GOOGLE_REGION \
    --format='value(status.url)' 2>/dev/null || echo "")

echo ""
echo "Discovered URLs:"
echo "  Transport Intent API:   ${TRANSPORT_INTENT_API_URL:-NOT FOUND}"
echo "  Transport Topology API: ${TRANSPORT_TOPOLOGY_API_URL:-NOT FOUND}"
echo "  Core Intent API:        ${CORE_INTENT_API_URL:-NOT FOUND}"
echo "  Core Topology API:      ${CORE_TOPOLOGY_API_URL:-NOT FOUND}"
echo "  Radio Intent API:       ${RADIO_INTENT_API_URL:-NOT FOUND}"
echo "  Radio Topology API:     ${RADIO_TOPOLOGY_API_URL:-NOT FOUND}"

# Get service account
SERVICE_ACCOUNT="${GOOGLE_SERVICE_ACCOUNT:-networkagent}@${GOOGLE_PROJECT}.iam.gserviceaccount.com"

# Deploy to Cloud Run
echo ""
echo "Deploying to Cloud Run..."
IMAGE_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/domain-mcp:latest"

gcloud run deploy domain-mcp \
    --image "$IMAGE_URI" \
    --region "$GOOGLE_REGION" \
    --platform managed \
    --service-account "$SERVICE_ACCOUNT" \
    --set-env-vars "TRANSPORT_INTENT_API_URL=${TRANSPORT_INTENT_API_URL}" \
    --set-env-vars "TRANSPORT_TOPOLOGY_API_URL=${TRANSPORT_TOPOLOGY_API_URL}" \
    --set-env-vars "CORE_INTENT_API_URL=${CORE_INTENT_API_URL}" \
    --set-env-vars "CORE_TOPOLOGY_API_URL=${CORE_TOPOLOGY_API_URL}" \
    --set-env-vars "RADIO_INTENT_API_URL=${RADIO_INTENT_API_URL}" \
    --set-env-vars "RADIO_TOPOLOGY_API_URL=${RADIO_TOPOLOGY_API_URL}" \
    --no-allow-unauthenticated \
    --max-instances 10 \
    --memory 512Mi \
    --cpu 1 \
    --timeout 300

# Get service URL
SERVICE_URL=$(gcloud run services describe domain-mcp \
    --region=$GOOGLE_REGION \
    --format='value(status.url)')

echo ""
echo "========================================="
echo "Deployment Complete!"
echo "========================================="
echo ""
echo "Multi-Domain MCP Server URL: $SERVICE_URL"
echo "MCP SSE Endpoint: $SERVICE_URL/sse"
echo ""
echo "Connected APIs:"
echo "  Transport Intent API:   ${TRANSPORT_INTENT_API_URL:-NOT CONFIGURED}"
echo "  Transport Topology API: ${TRANSPORT_TOPOLOGY_API_URL:-NOT CONFIGURED}"
echo "  Core Intent API:        ${CORE_INTENT_API_URL:-NOT CONFIGURED}"
echo "  Core Topology API:      ${CORE_TOPOLOGY_API_URL:-NOT CONFIGURED}"
echo "  Radio Intent API:       ${RADIO_INTENT_API_URL:-NOT CONFIGURED}"
echo "  Radio Topology API:     ${RADIO_TOPOLOGY_API_URL:-NOT CONFIGURED}"
echo ""
echo "Test the server:"
echo "  curl $SERVICE_URL/sse"
echo ""
echo "Note: Tools for domains with 'NOT CONFIGURED' APIs will fail gracefully"
echo "      when called. Deploy domain APIs to enable their respective tools."
echo ""
