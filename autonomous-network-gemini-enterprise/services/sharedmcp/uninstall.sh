#!/bin/bash
#
# MCP Tools Uninstallation Script
# Deletes Network Agent MCP tools Cloud Run service
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source environment
if [ -f "$REPO_ROOT/config.sh" ]; then
    source "$REPO_ROOT/config.sh"
else
    echo "Error: config.sh not found"
    exit 1
fi

# Verify required environment variables
: "${GOOGLE_PROJECT:?GOOGLE_PROJECT must be set}"
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"

section "MCP Tools — Cloud Run service"
if gcloud run services describe sharedmcp \
    --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT" &>/dev/null; then
  gcloud run services delete sharedmcp \
    --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT" --quiet
  ok "Service 'sharedmcp' deleted"
else
  warn "Service 'sharedmcp' not found — skipping"
fi

ok "MCP Tools cleanup complete"
