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
    echo "Error: config.sh not found at $REPO_ROOT"
    exit 1
fi

echo "========================================="
echo "Transport Topology API - Deployment"
echo "========================================="

# Validate environment
if [ -z "$GOOGLE_PROJECT" ] || [ -z "$GOOGLE_REGION" ]; then
    echo "Error: GOOGLE_PROJECT and GOOGLE_REGION must be set in config.sh"
    exit 1
fi

# Set defaults
GOOGLE_REPO=${GOOGLE_REPO:-"network-agent"}
SPANNER_INSTANCE=${SPANNER_INSTANCE:-"networktopology-instance"}
GOOGLE_SPANNER_TRANSPORT_DATABASE=${GOOGLE_SPANNER_TRANSPORT_DATABASE:-"transporttopology-db"}
GOOGLE_SERVICE_ACCOUNT=${GOOGLE_SERVICE_ACCOUNT:-"networkagent"}

echo "Configuration:"
echo "  Project: $GOOGLE_PROJECT"
echo "  Region: $GOOGLE_REGION"
echo "  Repository: $GOOGLE_REPO"
echo "  Spanner Instance: $SPANNER_INSTANCE"
echo "  Spanner Database: $GOOGLE_SPANNER_TRANSPORT_DATABASE"
echo "  Service Account: $GOOGLE_SERVICE_ACCOUNT"

# Render cloudbuild.yaml
echo ""
echo "Rendering cloudbuild.yaml from template..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

# Build container
echo ""
echo "Building container image..."
gcloud builds submit --region="$GOOGLE_REGION" \
    --config "$SCRIPT_DIR/cloudbuild.yaml" "$REPO_ROOT"

# Get service account. GOOGLE_SERVICE_ACCOUNT arrives either as a bare name (the
# default on line 43, when this script is run standalone) or as a full email --
# the root install.sh exports the full form from both Create() and
# StartPlatform(). Appending the suffix unconditionally produced
# "networkagent@PROJECT.iam.gserviceaccount.com@PROJECT.iam.gserviceaccount.com"
# and Cloud Run rejected the deploy.
case "$GOOGLE_SERVICE_ACCOUNT" in
    *@*) SERVICE_ACCOUNT="$GOOGLE_SERVICE_ACCOUNT" ;;
    *)   SERVICE_ACCOUNT="${GOOGLE_SERVICE_ACCOUNT}@${GOOGLE_PROJECT}.iam.gserviceaccount.com" ;;
esac

# Deploy to Cloud Run
echo ""
echo "Deploying to Cloud Run..."
IMAGE_URI="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/transport-topologyserver:latest"

gcloud run deploy transport-topologyserver \
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
    --set-env-vars \
GOOGLE_PROJECT="${GOOGLE_PROJECT}",\
GOOGLE_REGION="${GOOGLE_REGION}",\
SPANNER_INSTANCE="${SPANNER_INSTANCE}",\
GOOGLE_SPANNER_TRANSPORT_DATABASE="${GOOGLE_SPANNER_TRANSPORT_DATABASE}"

# Get service URL
API_URL=$(gcloud run services describe transport-topologyserver \
    --region="$GOOGLE_REGION" \
    --format='value(status.url)')

echo ""
echo "========================================="
echo "Transport Topology API Deployed!"
echo "========================================="
echo ""
echo "API URL: $API_URL"
echo ""
echo "Endpoints:"
echo "  Health:     $API_URL/health"
echo "  Resources:  $API_URL/tmf-api/resourceInventoryManagement/v4/transport/resource"
echo "  Graph:      $API_URL/tmf-api/resourceInventoryManagement/v4/transport/graph/query"
echo "  Events:     ws://${API_URL#https://}/tmf-api/resourceInventoryManagement/v4/transport/events"
echo "  Hub:        $API_URL/tmf-api/resourceInventoryManagement/v4/transport/hub"
echo "  Swagger:    $API_URL/docs"
echo "  ReDoc:      $API_URL/redoc"
echo ""

# Save URL
echo "$API_URL" > "$SCRIPT_DIR/.api_url"

echo "API URL saved to .api_url"
echo ""
