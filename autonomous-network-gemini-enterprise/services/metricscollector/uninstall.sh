#!/bin/bash
#
# Metrics Collector Uninstall Script
# Removes VyOS metrics collector Cloud Run worker pool
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

section "Metrics Collector Cleanup — Removing worker pool"

echo "Deleting metrics collector worker pool..."
gcloud beta run worker-pools delete metricscollector \
    --region=$GOOGLE_REGION \
    --quiet \
    --async 2>/dev/null || warn "Metrics collector worker pool not found or already deleted"

ok "Metrics collector cleanup complete"
