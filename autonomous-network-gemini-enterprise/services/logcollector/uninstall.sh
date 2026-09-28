#!/bin/bash
#
# Log Collector Uninstall Script
# Removes log sink, Pub/Sub topic, and Cloud Function
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

# Log capture configuration
SINK_NAME="${SINK_NAME:-nwoplogs-sink}"
TOPIC_NAME="${TOPIC_NAME:-nwoplogs-topic}"
CAPTURE_LOG_FUNCTION="${CAPTURE_LOG_FUNCTION:-capture_log}"

section "Log Collector Cleanup — Removing log sink, topic, and function"

# Delete log sink
echo "Deleting logging sink..."
gcloud logging sinks delete $SINK_NAME --quiet 2>/dev/null || warn "Logging sink not found or already deleted"

# Delete Pub/Sub topic
echo "Deleting Pub/Sub topic..."
gcloud pubsub topics delete $TOPIC_NAME --quiet 2>/dev/null || warn "Pub/Sub topic not found or already deleted"

# Delete Cloud Function
echo "Deleting Cloud Function..."
gcloud functions delete $CAPTURE_LOG_FUNCTION --region=$GOOGLE_REGION --quiet 2>/dev/null || warn "Cloud Function not found or already deleted"

ok "Log collector cleanup complete"
