#!/bin/bash
#
# Gitea Webhook Uninstall Script
# Removes Gitea webhook Cloud Run service
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
fi

# Verify required environment variables
: "${GOOGLE_REGION:?GOOGLE_REGION must be set}"

section "Gitea Webhook Cleanup — Removing Cloud Run service"

echo "Deleting gitea-webhook Cloud Run service..."
gcloud run services delete gitea-webhook \
    --region=$GOOGLE_REGION \
    --quiet \
    --async 2>/dev/null || warn "Gitea webhook service not found or already deleted"

ok "Gitea webhook cleanup complete"
