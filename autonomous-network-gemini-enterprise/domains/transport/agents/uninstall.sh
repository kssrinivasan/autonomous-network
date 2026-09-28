#!/bin/bash
set -euo pipefail

export GOOGLE_API_USE_CLIENT_CERTIFICATE=false

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../../config.sh
. "$SCRIPT_DIR/../../../config.sh"

section "Agents — undeploying from Vertex AI"
GCP_PROJECT="$GOOGLE_PROJECT" \
GCP_REGION="$GOOGLE_REGION" \
AGENT_SERVICE_ACCOUNT="${AGENT_SERVICE_ACCOUNT:-}" \
GITEA_MCP_URL="" \
GEMINI_APP_ID="${GEMINI_APP_ID:-}" \
uv run --with google-auth --with requests --index https://pypi.org/simple python "$SCRIPT_DIR/deploy_agents.py" --undeploy || warn "Agent undeploy encountered errors — continuing"
ok "Agents undeployed"
