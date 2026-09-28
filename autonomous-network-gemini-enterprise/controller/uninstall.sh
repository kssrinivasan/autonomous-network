#!/bin/bash
#
# Operator Uninstall Script
# Removes the Network Operator and Git Operator from GKE
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
: "${GOOGLE_NAMESPACE:?GOOGLE_NAMESPACE must be set}"

section "Operator Cleanup — Removing operators from GKE"

cd "$SCRIPT_DIR"

if [ -f "deployment.yaml" ]; then
    echo "Deleting operator deployments..."
    kubectl delete -f deployment.yaml 2>/dev/null || true
else
    warn "deployment.yaml not found - skipping operator deployment deletion"
fi

# Note: We don't delete CRDs as they may have custom resources in use
# If you want to delete CRDs, you need to delete all CRs first
warn "CRDs are not deleted automatically to prevent data loss"
warn "To delete CRDs, first delete all custom resources, then run:"
warn "  kubectl delete -f config/transport"
warn "  kubectl delete -f config/radio"
warn "  kubectl delete -f config/infrastructure"
warn "  kubectl delete -f config/core"
warn "  kubectl delete -f config"

cd - > /dev/null

ok "Operator cleanup complete"
