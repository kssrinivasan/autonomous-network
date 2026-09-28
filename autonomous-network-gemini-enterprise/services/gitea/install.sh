#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../config.sh
. "$SCRIPT_DIR/../../config.sh"

YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"
# shellcheck source=../lib/build.sh
. "$SCRIPT_DIR/../lib/build.sh"

IMAGE="${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/gitea:latest"
TEMPLATE="${SCRIPT_DIR}/cloudbuild.tpl.yaml"
TEMPLATE_VARS='${GOOGLE_REGION}${GOOGLE_PROJECT}${GOOGLE_REPO}'

# Use WEBAPPS_PWD from environment instead of Secret Manager
if [ -z "${WEBAPPS_PWD}" ]; then
  echo "Error: WEBAPPS_PWD must be set"
  exit 1
fi
ok "Using WEBAPPS_PWD for Gitea admin password"

# --- Cloud Build: build and push image ---
section "Gitea — Cloud Build (build + push)"
tmp=$(mktemp --suffix=.yaml)
trap "rm -f '$tmp'" EXIT
envsubst "$TEMPLATE_VARS" < "$TEMPLATE" > "$tmp"

if should_build_image "$IMAGE" "Gitea"; then
    gcloud builds submit --config="$tmp" --project="$GOOGLE_PROJECT" "$SCRIPT_DIR/"
fi

# --- Reserve the stable address ---
# Gitea used to live on Cloud Run, which handed out a stable *.run.app URL for
# free. On GKE the address is ours to keep: it is pinned into the Service, baked
# into the self-signed certificate's SAN list, and written into both RootSyncs
# and every consumer's GITEA_URL. It must survive redeploys, so it is a reserved
# regional address rather than an ephemeral one.
section "Gitea — Reserving static address"
if ! gcloud compute addresses describe gitea-ip \
      --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT" >/dev/null 2>&1; then
  gcloud compute addresses create gitea-ip \
    --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT"
fi
export GITEA_LB_IP=$(gcloud compute addresses describe gitea-ip \
  --region="$GOOGLE_REGION" --project="$GOOGLE_PROJECT" --format='value(address)')
ok "Gitea address: ${GITEA_LB_IP}"

SERVICE_URL="https://${GITEA_LB_IP}"

# --- TLS certificate ---
# Self-signed, because there is no domain name here to get a publicly-trusted
# certificate for: Google-managed certificates validate domains, not IPs. This
# encrypts the admin password and API tokens on the wire; it does not
# authenticate the server, so clients still pass -k / noSSLVerify. The SAN list
# covers both the public address and the in-cluster service names so the same
# certificate works for callers inside and outside the cluster.
section "Gitea — TLS certificate"
if kubectl get secret gitea-tls -n "$GOOGLE_NAMESPACE" >/dev/null 2>&1; then
  ok "Reusing existing gitea-tls secret"
else
  CERT_DIR=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "${CERT_DIR}/tls.key" -out "${CERT_DIR}/tls.crt" \
    -subj "/CN=${GITEA_LB_IP}" \
    -addext "subjectAltName=IP:${GITEA_LB_IP},DNS:gitea,DNS:gitea.${GOOGLE_NAMESPACE},DNS:gitea.${GOOGLE_NAMESPACE}.svc.cluster.local" \
    >/dev/null 2>&1
  kubectl create secret tls gitea-tls \
    --namespace="$GOOGLE_NAMESPACE" \
    --cert="${CERT_DIR}/tls.crt" --key="${CERT_DIR}/tls.key"
  rm -rf "$CERT_DIR"
  ok "Created self-signed certificate for ${GITEA_LB_IP}"
fi

# The admin credentials go in a Secret rather than inline in the Deployment, so
# the rendered deployment.yaml on disk carries no password.
kubectl create secret generic gitea-admin \
  --namespace="$GOOGLE_NAMESPACE" \
  --from-literal=username="${WEBAPPS_LOGIN}" \
  --from-literal=password="${WEBAPPS_PWD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# --- GKE deploy ---
section "Gitea — GKE deploy"
envsubst '${GOOGLE_NAMESPACE}${GOOGLE_REGION}${GOOGLE_PROJECT}${GOOGLE_REPO}${GITEA_LB_IP}${GITEA_ADMIN_EMAIL}' \
  < "${SCRIPT_DIR}/deployment.tpl.yaml" > "${SCRIPT_DIR}/deployment.yaml"
kubectl apply -f "${SCRIPT_DIR}/deployment.yaml"

echo "  Waiting for Gitea rollout..."
kubectl rollout status deployment/gitea -n "$GOOGLE_NAMESPACE" --timeout=5m
ok "Gitea running at ${SERVICE_URL}"

# --- Seed repositories ---
# Idempotent: on an existing disk this only fills in anything missing. See seed.sh.
section "Gitea — Seeding repositories"
GITEA_URL="$SERVICE_URL" \
  bash "$SCRIPT_DIR/seed.sh"

# --- Create RootSync ---
section "Gitea — Creating RootSync"
GITEA_URL="$SERVICE_URL" \
  bash "$SCRIPT_DIR/create-rootsync.sh"

# The trust review hook on the network repo is *not* registered here. It needs
# the gitea-webhook Cloud Run URL, which needs the trust agent's engine id from
# deploy_agents.py -- neither exists this early. services/gitea-webhook/install.sh
# registers the hook and mints the API token once both are up. That used to need
# re-running after every Gitea deploy, because the SQLite database holding the
# hook lived on an ephemeral filesystem; on the persistent disk the hook and the
# API token now survive, so it is only needed once.

# --- Summary ---
section "Gitea — Deployment Summary"
ok "Gitea deployed at $SERVICE_URL"
echo ""
echo "  Repositories:"
echo "    ${SERVICE_URL}/${WEBAPPS_LOGIN}/networkdesign"
echo "    ${SERVICE_URL}/${WEBAPPS_LOGIN}/network"
echo "    ${SERVICE_URL}/${WEBAPPS_LOGIN}/root-repo"
echo ""
echo "  Clone example:"
echo "    git clone ${SERVICE_URL}/${WEBAPPS_LOGIN}/networkdesign"
echo ""
