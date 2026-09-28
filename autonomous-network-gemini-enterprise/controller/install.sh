#!/bin/bash
#
# Operator Installation Script
# Deploys the Network Operator and Git Operator to GKE
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source common utilities if available
if [ -f "$SCRIPT_DIR/../config.sh" ]; then
    # shellcheck source=../config.sh
    . "$SCRIPT_DIR/../config.sh"
else
    # Fallback: define minimal utilities
    section() { echo ""; echo "### $1"; }
    ok() { echo "✓ $1"; }
    warn() { echo "⚠ $1"; }
fi

# Verify required environment variables
: "${GOOGLE_PROJECT:?GOOGLE_PROJECT must be set}"
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"
: "${GOOGLE_ZONE:?GOOGLE_ZONE must be set}"
: "${GOOGLE_REPO:?GOOGLE_REPO must be set}"
: "${GOOGLE_NAMESPACE:?GOOGLE_NAMESPACE must be set}"
: "${NETWORK_OPERATOR:?NETWORK_OPERATOR must be set}"

# Optional flags
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"

section "Operator Deployment — Network Operator"

# The operator's device container copies the traffic-agent binary from
# the traffic-agent image, so it must be built and pushed first.
echo "Building traffic-agent image (required by operator)..."
TRAFFIC_IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/traffic-agent:latest"
if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $TRAFFIC_IMAGE_URI >/dev/null 2>&1); then
    read -p "Traffic-agent image already exists. Rebuild? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gcloud builds submit --region=$GOOGLE_REGION --config "$SCRIPT_DIR/../traffic-agent/cloudbuild.yaml" "$SCRIPT_DIR/.."
    fi
elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $TRAFFIC_IMAGE_URI >/dev/null 2>&1); then
    echo "Traffic-agent image already exists - not rebuilding (NO_FLAG set)"
else
    gcloud builds submit --region=$GOOGLE_REGION --config "$SCRIPT_DIR/../traffic-agent/cloudbuild.yaml" "$SCRIPT_DIR/.."
fi

# Generate deployment.yaml and cloudbuild.yaml from templates
echo "Generating deployment and build configuration files from templates..."
# Empty when Gitea is not up yet; utils/git.py then falls back to discovering
# the address itself, so an early controller install is not broken by this.
export GITEA_URL="${GITEA_URL:-$(bash "$SCRIPT_DIR/../services/gitea/gitea-url.sh")}"
TEMPLATE_VARS='${GOOGLE_VM_USER}${GOOGLE_PROJECT}${GOOGLE_PROJECT_NUMBER}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_REPO}${WEBAPPS_LOGIN}${WEBAPPS_PWD}${NETWORK_OPERATOR}${GOOGLE_ORG_NAME}${GOOGLE_NAMESPACE}${GOOGLE_SPANNER_TRANSPORT_DATABASE}${GOOGLE_SPANNER_INFRA_DATABASE}${GOOGLE_SPANNER_CORE_DATABASE}${GOOGLE_SPANNER_RADIO_DATABASE}${GOOGLE_SPANNER_SERVICE_DATABASE}${GOOGLE_SPANNER_OPS_DATABASE}${GITEA_URL}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/deployment.tpl.yaml" > "$SCRIPT_DIR/deployment.yaml"

TEMPLATE_VARS='${GOOGLE_VM_USER}${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

echo "######################################"
echo "Deploy the Operator, networks and CRDs"
echo "######################################"

if ! test -f "$SCRIPT_DIR/deployment.yaml"; then
    echo "Error: deployment.yaml not found after template generation"
    exit 1
fi

cd "$SCRIPT_DIR"

# Build and push operator image
IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networkoperator:latest"
if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
    read -p "Operator image already exists. Rebuild? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    fi
elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
    echo "Skipping operator image rebuild (NO_FLAG set)"
else
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
fi

# Deploy operators
echo "Deploying operators to GKE..."
kubectl delete -f deployment.yaml 2>/dev/null || true
kubectl apply -f deployment.yaml

echo "Waiting for deployment to be ready..."
kubectl rollout status deployment $NETWORK_OPERATOR -n $GOOGLE_NAMESPACE --timeout=120s

# Load the CRDs
echo "Applying CRDs..."
kubectl apply -f config
kubectl apply -f config/core
kubectl apply -f config/infrastructure
kubectl apply -f config/radio
kubectl apply -f config/transport

cd - > /dev/null

ok "Operators deployed successfully"
