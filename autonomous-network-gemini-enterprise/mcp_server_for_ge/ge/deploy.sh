#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
VARS_FILE="$SCRIPT_DIR/variables.json"

if [ ! -f "$VARS_FILE" ]; then
  echo "❌ Error: variables.json not found in $SCRIPT_DIR."
  exit 1
fi

echo "📋 Loading deployment configuration from variables.json..."
GCP_PROJECT=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['GCP_PROJECT'])")
GCP_REGION=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['GCP_REGION'])")
SERVICE_NAME=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['SERVICE_NAME'])")
SERVICE_ACCOUNT=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['SERVICE_ACCOUNT'])")
CONCURRENCY=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['CONCURRENCY'])")
MAX_INSTANCES=$(python3 -c "import json; print(json.load(open('$VARS_FILE'))['MAX_INSTANCES'])")

# The tools read the topology from six Spanner databases (see
# src/utils/database.py). Their defaults match config.sh.template, but a
# project that overrides any of these names would otherwise have the deployed
# service silently fall back to the defaults, so pass them through explicitly.
SPANNER_ENV="GOOGLE_SPANNER_INSTANCE=${GOOGLE_SPANNER_INSTANCE:-networktopology-instance}"
SPANNER_ENV+=",GOOGLE_SPANNER_TRANSPORT_DATABASE=${GOOGLE_SPANNER_TRANSPORT_DATABASE:-transporttopology-db}"
SPANNER_ENV+=",GOOGLE_SPANNER_INFRA_DATABASE=${GOOGLE_SPANNER_INFRA_DATABASE:-infratopology-db}"
SPANNER_ENV+=",GOOGLE_SPANNER_CORE_DATABASE=${GOOGLE_SPANNER_CORE_DATABASE:-coretopology-db}"
SPANNER_ENV+=",GOOGLE_SPANNER_RADIO_DATABASE=${GOOGLE_SPANNER_RADIO_DATABASE:-radiotopology-db}"
SPANNER_ENV+=",GOOGLE_SPANNER_SERVICE_DATABASE=${GOOGLE_SPANNER_SERVICE_DATABASE:-servicetopology-db}"
SPANNER_ENV+=",GOOGLE_SPANNER_OPS_DATABASE=${GOOGLE_SPANNER_OPS_DATABASE:-agentops-db}"

# The Gitea tools (getTransportDesignDoc, deployDescriptors, deleteDescriptor)
# authenticate with basic auth and need a base URL. Without these three the
# tools deploy but every call fails: src/utils/git_helpers.py falls back to
# 'admin'/'password', and get_gitea_url() has no service to discover.
GITEA_URL=$(GOOGLE_PROJECT="$GCP_PROJECT" GOOGLE_REGION="$GCP_REGION" \
  bash "$(dirname "${BASH_SOURCE[0]}")/../../services/gitea/gitea-url.sh")
if [ -z "$GITEA_URL" ]; then
  echo "⚠️  Gitea not deployed yet; the service will discover its URL at runtime."
fi
GITEA_ENV="GITEA_URL=${GITEA_URL}"
GITEA_ENV+=",WEBAPPS_LOGIN=${WEBAPPS_LOGIN:-}"
GITEA_ENV+=",WEBAPPS_PWD=${WEBAPPS_PWD:-}"

PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT" --format="value(projectNumber)")

# --- Vendor the shared Gitea library -------------------------------------
#
# src/utils/git_helpers.py imports agent_library, which lives in lib/ at the
# repo root -- outside this service's --source build context. Copy it in, the
# same way agents-in-agent-runtime/deploy_agents.py does for the agents.
#
# The copy is git-ignored, so it must be re-made on every deploy; that is the
# point, since a stale one would silently ship an old Gitea client. .gcloudignore
# in this directory exists to stop gcloud synthesizing an ignore file from
# .gitignore and dropping the copy from the upload.
LIB_SRC="$( cd -- "$SCRIPT_DIR/../../lib/src/agent_library" &> /dev/null && pwd )"
if [ -z "$LIB_SRC" ]; then
  echo "❌ Error: lib/src/agent_library not found; cannot vendor the shared library."
  exit 1
fi
echo "📦 Vendoring agent_library from ${LIB_SRC}..."
rm -rf "$SCRIPT_DIR/src/agent_library"
cp -R "$LIB_SRC" "$SCRIPT_DIR/src/agent_library"
find "$SCRIPT_DIR/src/agent_library" -name __pycache__ -type d -exec rm -rf {} +

# --- Shared secret -------------------------------------------------------
#
# Gemini Enterprise cannot present a Google ID token, so this service has to
# stay --allow-unauthenticated for GE to reach it, and the OIDC verifier the
# re/ twin uses cannot apply. Without something in its place the endpoint is
# open to anyone who finds the URL. A bearer secret is that something: it is
# generated once, kept in Secret Manager, and configured on the GE tool data
# store as an Authorization header.
#
# Generated with openssl rand, not derived from the project id. A secret you
# can recompute from public facts is not a secret -- see the WEBHOOK_SECRET in
# services/gitea-webhook/install.sh, which has exactly that problem.
SECRET_NAME="${MCP_SECRET_NAME:-mcp-ge-shared-secret}"

if ! gcloud secrets describe "$SECRET_NAME" --project="$GCP_PROJECT" >/dev/null 2>&1; then
  echo "🔑 Creating Secret Manager secret '$SECRET_NAME'..."
  gcloud secrets create "$SECRET_NAME" \
    --project="$GCP_PROJECT" \
    --replication-policy="automatic" >/dev/null
  # tr -d '\n': openssl ends its output with a newline and Secret Manager stores
  # it verbatim, so the mounted MCP_SHARED_SECRET would be one character longer
  # than the value anyone pastes out of `gcloud secrets versions access`.
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add "$SECRET_NAME" \
    --project="$GCP_PROJECT" --data-file=- >/dev/null
  echo "   Created. Rotate with: gcloud secrets versions add $SECRET_NAME --data-file=-"
else
  echo "🔑 Reusing existing Secret Manager secret '$SECRET_NAME'."
fi

# The runtime SA reads the secret at container start, so it needs accessor.
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --project="$GCP_PROJECT" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None >/dev/null 2>&1 || \
  echo "⚠️  Could not grant secretAccessor to ${SERVICE_ACCOUNT}; the service will fail to start."

echo "🚀 Deploying Cloud Run microservice '$SERVICE_NAME' in region $GCP_REGION..."

# NOTE: unlike the Reasoning Engine twin (re/deploy.sh), Cloud Run IAM stays
# open here -- Gemini Enterprise connects as a Tool Data Store and presents no
# Google ID token, so --no-allow-unauthenticated would break the integration
# outright. MCP_AUDIENCE is left unset for the same reason.
#
# Two things close the gap that used to leave:
#   1. MCP_SHARED_SECRET -- every call must carry the bearer secret above, so
#      "can reach the URL" is no longer the same as "can call the tools".
#   2. MCP_ENABLE_MUTATING_TOOLS is NOT set, so deployDescriptors,
#      deleteDescriptor, create_device, delete_device, runTest and deleteTest
#      are never registered on this server. Even with the secret, this endpoint
#      cannot write to the GitOps repo or the cluster.
gcloud run deploy "$SERVICE_NAME" \
  --source="$SCRIPT_DIR" \
  --project="$GCP_PROJECT" \
  --region="$GCP_REGION" \
  --service-account="$SERVICE_ACCOUNT" \
  --concurrency="$CONCURRENCY" \
  --max-instances="$MAX_INSTANCES" \
  --set-env-vars="${SPANNER_ENV},${GITEA_ENV},GOOGLE_PROJECT=${GCP_PROJECT},GOOGLE_PROJECT_NUMBER=${PROJECT_NUMBER},GOOGLE_REGION=${GCP_REGION}" \
  --set-secrets="MCP_SHARED_SECRET=${SECRET_NAME}:latest" \
  --allow-unauthenticated

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$GCP_PROJECT" --region="$GCP_REGION" \
  --format="value(status.url)" 2>/dev/null)

cat <<EOF

✅ Deployed. This endpoint is publicly routable but no longer publicly usable:
   every call must present the shared secret, and no state-changing tool is
   registered on it.

   To finish wiring Gemini Enterprise, configure the tool data store at
     ${SERVICE_URL:-<service url>}
   with an Authorization header of "Bearer <secret>", where <secret> is:

     gcloud secrets versions access latest --secret=${SECRET_NAME} --project=${GCP_PROJECT}

   Until that header is configured, GE calls will be rejected -- that is the
   intended behaviour, not a deployment failure.
EOF
