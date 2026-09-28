#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../config.sh
. "$SCRIPT_DIR/../../config.sh"

section "Gitea — Git credentials secrets"

# Delete git-creds from config-management-system
if kubectl get secret git-creds -n config-management-system &>/dev/null; then
  kubectl delete secret git-creds -n config-management-system
  ok "Secret 'git-creds' deleted from config-management-system"
else
  warn "Secret 'git-creds' not found in config-management-system — skipping"
fi

# Delete git-creds from network namespace
if kubectl get secret git-creds -n network &>/dev/null; then
  kubectl delete secret git-creds -n network
  ok "Secret 'git-creds' deleted from network namespace"
else
  warn "Secret 'git-creds' not found in network namespace — skipping"
fi

section "Gitea — RootSync CR"
if kubectl get rootsync networkagent-root -n config-management-system &>/dev/null; then
  kubectl delete rootsync networkagent-root -n config-management-system
  ok "RootSync 'networkagent-root' deleted"
else
  warn "RootSync 'networkagent-root' not found — skipping"
fi

section "Gitea — GKE workload"
kubectl delete deployment gitea -n "$GOOGLE_NAMESPACE" --ignore-not-found
kubectl delete service gitea -n "$GOOGLE_NAMESPACE" --ignore-not-found
kubectl delete secret gitea-tls gitea-admin -n "$GOOGLE_NAMESPACE" --ignore-not-found
ok "Gitea workload deleted"

# The PVC, its PersistentVolume and the reserved address are deliberately left
# behind. They are the whole point of migration plan 7.2: the repositories and
# the Gitea database live on that disk, and the address is what keeps the URL
# stable across redeploys. Re-running install.sh re-attaches both and the data
# is still there. Use --purge only when you genuinely want the repositories gone.
if [[ "${1:-}" == "--purge" ]]; then
  section "Gitea — Purging persistent data"
  warn "Deleting the Gitea disk and address. All repositories will be lost."
  kubectl delete pvc gitea-data-pvc -n "$GOOGLE_NAMESPACE" --ignore-not-found
  # The StorageClass is reclaimPolicy: Retain, so deleting the PVC releases the
  # PersistentVolume but does not delete it or the underlying disk.
  kubectl delete pv -l application=gitea --ignore-not-found 2>/dev/null || true
  gcloud compute addresses delete gitea-ip \
    --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT" --quiet 2>/dev/null || true
  ok "Gitea persistent data purged"
else
  ok "Kept: PVC 'gitea-data-pvc' and address 'gitea-ip' (re-run with --purge to delete)"
fi

ok "Gitea cleanup complete"
