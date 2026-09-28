#!/bin/bash
set -euo pipefail

export GOOGLE_API_USE_CLIENT_CERTIFICATE=false

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../../config.sh
. "$SCRIPT_DIR/../../../config.sh"

# Discover MCP server URLs independently (allows running this script standalone)
section "Discovering MCP server URLs..."

# Get shared MCP server URL
SHARED_MCP_URL=$(gcloud run services describe sharedmcp \
  --region="$GOOGLE_REGION" \
  --project="$GOOGLE_PROJECT" \
  --format='value(status.url)' 2>/dev/null || echo "")

if [ -n "$SHARED_MCP_URL" ]; then
  SHARED_MCP_URL="${SHARED_MCP_URL}/mcp"
fi

if [ -z "$SHARED_MCP_URL" ]; then
  error "Shared MCP service not found. Deploy it first: cd services/sharedmcp && ./install.sh"
  exit 1
fi
ok "Shared MCP: $SHARED_MCP_URL"

# Get Gitea service URL and form the MCP endpoint. Gitea is on GKE behind a
# reserved address; gitea-url.sh owns that lookup.
GITEA_SERVICE_URL=$(bash "$(dirname "${BASH_SOURCE[0]}")/../../../services/gitea/gitea-url.sh")

if [ -z "$GITEA_SERVICE_URL" ]; then
  error "Gitea service not found. Deploy it first: cd services/gitea && ./install.sh"
  exit 1
fi
GITEA_MCP_ENDPOINT="${GITEA_SERVICE_URL}/mcp"
ok "Gitea MCP: $GITEA_MCP_ENDPOINT"

section "Agents — deploying to Vertex AI Agent Runtime"
SPANNER_OPS_INSTANCE="${GOOGLE_SPANNER_OPS_INSTANCE:-networktopology-instance}"
SPANNER_OPS_DB="${GOOGLE_SPANNER_OPS_DATABASE:-agentops-db}"
AGENT_SERVICE_ACCOUNT="${AGENT_SERVICE_ACCOUNT:-networkagent@${GOOGLE_PROJECT}.iam.gserviceaccount.com}"

PROJECT_NUMBER=${GOOGLE_PROJECT_NUMBER:-$(gcloud projects describe "$GOOGLE_PROJECT" --format="value(projectNumber)" 2>/dev/null || echo "")}
REASONING_ENGINE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Ensure Service Account User and Spanner permissions for standalone deployment
section "Ensuring service account permissions..."
for SA in "${REASONING_ENGINE_SA}" "${COMPUTE_SA}"; do
    gcloud iam service-accounts add-iam-policy-binding "${AGENT_SERVICE_ACCOUNT}" \
        --member="serviceAccount:${SA}" \
        --role="roles/iam.serviceAccountUser" \
        --project="${GOOGLE_PROJECT}" \
        --no-user-output-enabled >/dev/null 2>&1 || true
done

for SA in "${AGENT_SERVICE_ACCOUNT}" "${REASONING_ENGINE_SA}"; do
    gcloud projects add-iam-policy-binding "${GOOGLE_PROJECT}" \
        --member="serviceAccount:${SA}" \
        --role="roles/spanner.databaseUser" \
        --no-user-output-enabled >/dev/null 2>&1 || true
done

if [ -n "${GEMINI_APP_ID:-}" ]; then
  GCP_PROJECT="$GOOGLE_PROJECT" \
  GCP_REGION="$GOOGLE_REGION" \
  AGENT_SERVICE_ACCOUNT="$AGENT_SERVICE_ACCOUNT" \
  SHARED_MCP_URL="$SHARED_MCP_URL" \
  GITEA_MCP_URL="$GITEA_MCP_ENDPOINT" \
  GEMINI_APP_ID="${GEMINI_APP_ID:-}" \
  GOOGLE_SPANNER_OPS_INSTANCE="$SPANNER_OPS_INSTANCE" \
  GOOGLE_SPANNER_OPS_DATABASE="$SPANNER_OPS_DB" \
  uv run --with google-auth --with requests --index https://pypi.org/simple python "$SCRIPT_DIR/deploy_agents.py"          # deploy + register in Gemini Enterprise
else
  GCP_PROJECT="$GOOGLE_PROJECT" \
  GCP_REGION="$GOOGLE_REGION" \
  AGENT_SERVICE_ACCOUNT="$AGENT_SERVICE_ACCOUNT" \
  SHARED_MCP_URL="$SHARED_MCP_URL" \
  GITEA_MCP_URL="$GITEA_MCP_ENDPOINT" \
  GEMINI_APP_ID="" \
  GOOGLE_SPANNER_OPS_INSTANCE="$SPANNER_OPS_INSTANCE" \
  GOOGLE_SPANNER_OPS_DATABASE="$SPANNER_OPS_DB" \
  uv run --with google-auth --with requests --index https://pypi.org/simple python "$SCRIPT_DIR/deploy_agents.py" --deploy # deploy only (GEMINI_APP_ID not configured)
fi

section "Ensuring sharedmcp IAM invoker permissions..."
for SA in "${REASONING_ENGINE_SA}" "${COMPUTE_SA}" "${AGENT_SERVICE_ACCOUNT}"; do
    gcloud run services add-iam-policy-binding sharedmcp \
        --region="$GOOGLE_REGION" \
        --member="serviceAccount:${SA}" \
        --role="roles/run.invoker" \
        --no-user-output-enabled >/dev/null 2>&1 || true
done

ok "Agents deployed"
