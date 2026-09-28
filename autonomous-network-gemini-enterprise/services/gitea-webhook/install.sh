#!/bin/bash
#
# Gitea Webhook Installation Script
# Deploys Gitea webhook service to Cloud Run
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
if [ -z "${GOOGLE_PROJECT}" ]; then
  echo "Error: GOOGLE_PROJECT must be set"
  exit 1
fi
if [ -z "${GOOGLE_REGION}" ]; then
  echo "Error: GOOGLE_REGION must be set"
  exit 1
fi
if [ -z "${GOOGLE_ZONE}" ]; then
  echo "Error: GOOGLE_ZONE must be set"
  exit 1
fi
if [ -z "${GOOGLE_REPO}" ]; then
  echo "Error: GOOGLE_REPO must be set"
  exit 1
fi
# Needed to mint the Gitea API token and register the push hook at the end of
# this script -- both authenticate as the Gitea admin.
if [ -z "${WEBAPPS_LOGIN:-}" ] || [ -z "${WEBAPPS_PWD:-}" ]; then
  echo "Error: WEBAPPS_LOGIN and WEBAPPS_PWD must be set (source ./config.sh)"
  exit 1
fi

# Optional flags
YES_FLAG="${YES_FLAG:-}"
NO_FLAG="${NO_FLAG:-}"

# shellcheck source=../lib/build.sh
. "$SCRIPT_DIR/../lib/build.sh"

section "Gitea Webhook Deployment — Cloud Run service"

export GOOGLE_SERVICE_ACCOUNT=$(gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}.")

# Render cloudbuild.yaml template
echo "Rendering cloudbuild.yaml from template..."
TEMPLATE_VARS='${GOOGLE_PROJECT}${GOOGLE_REGION}${GOOGLE_ZONE}${GOOGLE_REPO}'
envsubst "$TEMPLATE_VARS" < "$SCRIPT_DIR/cloudbuild.tpl.yaml" > "$SCRIPT_DIR/cloudbuild.yaml"

# Build container image
echo "Building gitea-webhook container..."
cd "$SCRIPT_DIR"
IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/gitea-webhook:latest"
if should_build_image "$IMAGE_URI" "Webhook"; then
    gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml ../..
fi
cd - > /dev/null

# Generate deterministic webhook secret based on project name
# This ensures consistency across redeployments so Gitea and webhook stay in sync
echo "Generating stable webhook secret from project identifier..."
export WEBHOOK_SECRET=$(echo -n "${GOOGLE_PROJECT}-webhook-secret" | sha256sum | cut -d' ' -f1)

# Try to retrieve the existing API token, so a redeploy reuses it instead of
# minting a new one. Read the env list as JSON and pick the entry out in python:
# gcloud's `env.filter(...)` projection silently yields nothing here, which made
# every run mint a fresh token and burn an extra Cloud Run revision.
export GITEA_API_TOKEN=""
if gcloud run services describe gitea-webhook --region=${GOOGLE_REGION} &>/dev/null; then
    echo "Retrieving existing webhook API token..."
    GITEA_API_TOKEN=$(gcloud run services describe gitea-webhook \
        --region=${GOOGLE_REGION} \
        --format='json(spec.template.spec.containers[0].env)' 2>/dev/null \
        | python3 -c '
import json, sys
try:
    env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env") or []
except Exception:
    env = []
print(next((e.get("value", "") for e in env if e.get("name") == "GITEA_API_TOKEN"), ""))
' 2>/dev/null || echo "")

    if [[ -n "$GITEA_API_TOKEN" ]]; then
        echo "Using existing Gitea API token"
    fi
fi

# Get the Trust Agent's Reasoning Engine resource path. The agent is deployed to
# Vertex AI Agent Runtime, not Cloud Run, so there is no "trust-agent" service to
# describe; deploy_agents.py writes the engine id into deployment_metadata.json.
# The webhook turns this path into a :query call -- see src/a2a_client.py.
TRUST_AGENT_URL=$(python3 -c "import json;print(json.load(open('$SCRIPT_DIR/../../agents-in-agent-runtime/trust/deployment_metadata.json')).get('remote_agent_runtime_id',''))" 2>/dev/null || echo "")

if [[ -z "$TRUST_AGENT_URL" ]]; then
    warn "Trust agent not deployed yet. Deploy the agents, then re-run this script."
    TRUST_AGENT_URL=""
else
    echo "Trust agent found at: $TRUST_AGENT_URL"
fi

# Get Gitea URL if Gitea is already deployed. Gitea runs on GKE behind a load
# balancer pinned to a reserved address (services/gitea/deployment.tpl.yaml);
# gitea-url.sh is the single place that lookup lives.
GITEA_URL=$(bash "$(dirname "${BASH_SOURCE[0]}")/../gitea/gitea-url.sh")

if [[ -n "$GITEA_URL" ]]; then
    echo "Gitea found at: $GITEA_URL"
else
    echo "Gitea not deployed yet - webhook will discover URL at runtime"
    GITEA_URL=""
fi

# Deploy to Cloud Run
#
# --allow-unauthenticated, deliberately. The only caller is Gitea's own webhook
# sender, which does a plain HTTP POST: it cannot mint a Google OIDC token, so
# under --no-allow-unauthenticated every delivery is rejected by Cloud Run's IAM
# layer with a 403 before it ever reaches the app, and the trust path is dark.
# The gate is the HMAC signature instead -- WEBHOOK_SECRET is always set below,
# so verify_signature() in src/main.py never takes its skip-verification branch.
#
# Caveat worth knowing: that secret is sha256("${GOOGLE_PROJECT}-webhook-secret"),
# so anyone who knows the project id can compute it and forge a delivery. The
# blast radius is a spurious PR comment on the network repo, and an auto-merge
# only if the trust agent independently returns **PROCEED** on the real diff.
# One delivery per push, so max-instances 3 is enough to absorb a multi-commit
# push without letting a hook storm fan out to the platform default of 100.
echo "Deploying gitea-webhook to Cloud Run..."
gcloud run deploy gitea-webhook \
    --image ${GOOGLE_REGION}-docker.pkg.dev/${GOOGLE_PROJECT}/${GOOGLE_REPO}/gitea-webhook:latest \
    --region ${GOOGLE_REGION} \
    --platform managed \
    --allow-unauthenticated \
    --service-account ${GOOGLE_SERVICE_ACCOUNT} \
    --set-env-vars TRUST_AGENT_URL=${TRUST_AGENT_URL},WEBHOOK_SECRET=${WEBHOOK_SECRET},GITEA_API_TOKEN=${GITEA_API_TOKEN},GITEA_URL=${GITEA_URL} \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 3 \
    --timeout 300 \
    --quiet

# Get deployed service URL
WEBHOOK_URL=$(gcloud run services describe gitea-webhook \
    --region ${GOOGLE_REGION} \
    --format 'value(status.url)')

if [[ -z "$WEBHOOK_URL" ]]; then
    echo "ERROR: Failed to get webhook service URL"
    exit 1
fi

echo "Webhook service deployed at: $WEBHOOK_URL"

# --- Wire Gitea to the service -------------------------------------------
#
# This script owns both halves of the wiring -- the API token the service uses
# to talk back to Gitea, and the push hook Gitea uses to reach the service.
# It has to: the hook needs WEBHOOK_URL, which only exists once the Cloud Run
# service above is deployed, and that in turn needs the trust engine id from
# deploy_agents.py. services/gitea/install.sh runs long before either, so it
# cannot register the hook itself -- it only creates the `update` branch.
#
# Both steps are idempotent, so re-running this script after a Gitea rebuild
# (which drops the database, and with it the token and the hook) restores them.
if [[ -z "$GITEA_URL" ]]; then
    warn "Gitea is not deployed -- skipping API token and hook registration."
    warn "Deploy Gitea, then re-run this script to finish the wiring."
else
    # A token carried over from a previous install can be silently dead: it was
    # minted against a Gitea database that no longer exists. That happened on
    # every deploy while Gitea was on Cloud Run with an ephemeral filesystem, and
    # it still happens if the disk is ever purged. Reusing it produced a service
    # that answered deliveries and then failed every API call with 401, so check
    # the token actually works before trusting it.
    if [[ -n "$GITEA_API_TOKEN" ]]; then
        TOKEN_CHECK=$(curl -sk -o /dev/null -w '%{http_code}' \
            -H "Authorization: token ${GITEA_API_TOKEN}" \
            "${GITEA_URL}/api/v1/user" || echo "000")
        if [[ "$TOKEN_CHECK" != "200" ]]; then
            warn "The existing Gitea API token is no longer valid (HTTP ${TOKEN_CHECK}) -- minting a new one"
            GITEA_API_TOKEN=""
        fi
    fi

    # The service needs a Gitea API token to fetch the commit diff, open the PR,
    # comment on it and merge it. Gitea returns the token value only at creation,
    # so if the service has no token we delete any same-named leftover (a token
    # whose value we can no longer recover) and mint a fresh one.
    if [[ -z "$GITEA_API_TOKEN" ]]; then
        echo "Creating a Gitea API token for the webhook service..."
        curl -sk -o /dev/null -X DELETE \
            -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
            "${GITEA_URL}/api/v1/users/${WEBAPPS_LOGIN}/tokens/webhook-automation" || true

        # Least privilege: the service reads and writes repository content (diffs,
        # pull requests, merges) and issues (the PR comment). It never touches
        # users, orgs or admin endpoints.
        GITEA_API_TOKEN=$(curl -sk -X POST \
            -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
            -H "Content-Type: application/json" \
            -d '{"name":"webhook-automation","scopes":["write:repository","write:issue","read:user"]}' \
            "${GITEA_URL}/api/v1/users/${WEBAPPS_LOGIN}/tokens" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha1",""))' 2>/dev/null || echo "")

        if [[ -n "$GITEA_API_TOKEN" ]]; then
            gcloud run services update gitea-webhook \
                --region "${GOOGLE_REGION}" \
                --update-env-vars "GITEA_URL=${GITEA_URL},GITEA_API_TOKEN=${GITEA_API_TOKEN}" \
                --quiet > /dev/null
            ok "Gitea API token created and set on the service"
        else
            warn "Failed to create a Gitea API token -- the service can receive"
            warn "deliveries but cannot open or merge pull requests."
        fi
    else
        echo "Reusing the existing Gitea API token"
    fi

    # Register the push hook on the network repo. Gitea has no upsert for hooks,
    # so drop any hook already pointing at this path before creating one --
    # otherwise every re-run adds a duplicate and the trust agent runs twice per
    # push. branch_filter keeps main pushes (including the auto-merge this very
    # service performs) from re-triggering the analysis.
    HOOK_PATH="${WEBHOOK_URL}/webhook/gitea/trust"
    echo "Registering the push hook on ${WEBAPPS_LOGIN}/network..."

    curl -sk -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
        "${GITEA_URL}/api/v1/repos/${WEBAPPS_LOGIN}/network/hooks" \
        | python3 -c '
import json, sys
try:
    hooks = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(hooks, list):
    for h in hooks:
        if h.get("config", {}).get("url", "").endswith("/webhook/gitea/trust"):
            print(h["id"])
' 2>/dev/null | while read -r hook_id; do
        [ -n "$hook_id" ] || continue
        curl -sk -o /dev/null -X DELETE -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
            "${GITEA_URL}/api/v1/repos/${WEBAPPS_LOGIN}/network/hooks/${hook_id}" || true
    done

    HOOK_STATUS=$(curl -sk -o /dev/null -w '%{http_code}' -X POST \
        -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
        -H "Content-Type: application/json" \
        -d "{
          \"type\":\"gitea\",
          \"config\":{
            \"url\":\"${HOOK_PATH}\",
            \"content_type\":\"json\",
            \"secret\":\"${WEBHOOK_SECRET}\"
          },
          \"events\":[\"push\"],
          \"branch_filter\":\"update\",
          \"active\":true
        }" \
        "${GITEA_URL}/api/v1/repos/${WEBAPPS_LOGIN}/network/hooks")

    if [[ "$HOOK_STATUS" == "201" ]]; then
        ok "Push hook registered on ${WEBAPPS_LOGIN}/network (branch filter: update)"
    else
        warn "Hook registration returned HTTP ${HOOK_STATUS} -- the trust path is dark."
        warn "Check that the network repo exists (services/gitea/install.sh seeds it)."
    fi
fi

ok "Gitea webhook deployed"
echo ""
echo "Webhook URL: $WEBHOOK_URL"
echo ""
echo "Flow: designer pushes CRs to the 'update' branch of the network repo,"
echo "      Gitea posts the push here, the trust agent reviews the diff, and"
echo "      a PROCEED verdict auto-merges the PR into main (which Config Sync"
echo "      then applies to the cluster)."
