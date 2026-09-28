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

# Keep one instance warm. ADK gives an MCPToolset 30s to complete the MCP
# session handshake, and a Cloud Run cold start on this image does not reliably
# fit inside that. A toolset that times out does not fail loudly -- it comes up
# with an empty tool list, and the agent then invents a tool name against it.
# The Gemini Enterprise twin is left scaling to zero: it is called
# interactively, not from an agent that gives up after one handshake.
MIN_INSTANCES=$(python3 -c "
import json
print(json.load(open('$VARS_FILE')).get('MIN_INSTANCES', 1))
")

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

echo "🚀 Deploying Cloud Run microservice '$SERVICE_NAME' in region $GCP_REGION..."

# These tools mutate state -- they push configs to Gitea, inject faults and
# drive the operator -- so the service is closed to anonymous callers. IAM
# gates who may invoke it at all; the OIDC verifier in src/utils/auth.py then
# checks the caller's identity inside the app. MCP_AUDIENCE is set in a second
# pass below, once Cloud Run has assigned the URL that is the audience.
#
# MCP_ENABLE_MUTATING_TOOLS is what makes deployDescriptors, deleteDescriptor,
# create_device, delete_device, runTest and deleteTest visible over MCP at all.
# It is off unless set, so the Gemini Enterprise twin -- which cannot require
# an ID token -- does not inherit them from the shared tool surface. Only opt
# in here, behind IAM and OIDC.
gcloud run deploy "$SERVICE_NAME" \
  --source="$SCRIPT_DIR" \
  --project="$GCP_PROJECT" \
  --region="$GCP_REGION" \
  --service-account="$SERVICE_ACCOUNT" \
  --concurrency="$CONCURRENCY" \
  --min-instances="$MIN_INSTANCES" \
  --max-instances="$MAX_INSTANCES" \
  --set-env-vars="${SPANNER_ENV},${GITEA_ENV},GOOGLE_PROJECT=${GCP_PROJECT},GOOGLE_PROJECT_NUMBER=${PROJECT_NUMBER},GOOGLE_REGION=${GCP_REGION},MCP_ENABLE_MUTATING_TOOLS=true" \
  --no-allow-unauthenticated

TOOLS_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$GCP_PROJECT" \
  --region="$GCP_REGION" \
  --format="value(status.url)" 2>/dev/null)

if [ -z "$TOOLS_URL" ]; then
  echo "❌ Error: could not read the URL of '$SERVICE_NAME'; OIDC stays disabled."
  exit 1
fi

echo "🔐 Enabling OIDC verification with audience $TOOLS_URL..."
gcloud run services update "$SERVICE_NAME" \
  --project="$GCP_PROJECT" \
  --region="$GCP_REGION" \
  --update-env-vars="MCP_AUDIENCE=${TOOLS_URL}"

# The deployed agents call these tools as the Vertex AI Reasoning Engine
# service agent, so that identity needs invoke rights. It is also on the
# allowlist in src/utils/globals.py -- both halves are required.
#
# On a clean project the Reasoning Engine service agent does not exist yet --
# Google creates it when the first engine is deployed, which happens after this
# script. Under set -e that one failure used to abort the loop, so the other two
# grants were skipped too and the script exited 1 on an otherwise good deploy.
# Warn and carry on instead; re-run this script once the fleet is up.
echo "🔑 Granting run.invoker to the callers of '$SERVICE_NAME'..."
MISSING_GRANTS=0
for SA in "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
          "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
          "$SERVICE_ACCOUNT"; do
  if gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
      --project="$GCP_PROJECT" \
      --region="$GCP_REGION" \
      --member="serviceAccount:${SA}" \
      --role="roles/run.invoker" \
      --quiet >/dev/null 2>&1; then
    echo "   ✓ ${SA}"
  else
    MISSING_GRANTS=1
    echo "   ⚠️  could not grant run.invoker to ${SA} (it may not exist yet)"
  fi
done

if [ "$MISSING_GRANTS" -ne 0 ]; then
  cat <<EOF

⚠️  At least one run.invoker grant did not land. The usual cause on a clean
   project is that service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com
   does not exist yet -- Google creates it when the first Reasoning Engine is
   deployed. Re-run this script after the agent fleet is up; until then the
   agents will get 403 from '$SERVICE_NAME'.
EOF
fi

echo "✅ '$SERVICE_NAME' deployed at $TOOLS_URL (authenticated)."
