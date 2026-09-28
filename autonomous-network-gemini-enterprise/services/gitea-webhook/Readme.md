# Gitea Webhook Service

A Cloud Run service that receives webhooks from Gitea and asks the Trust Agent to
validate changes pushed to the `update` branch of a network repository.

## Overview

When changes are pushed to the `update` branch:
1. Gitea sends a webhook POST to this service
2. Service fetches the commit diff from Gitea
3. Service calls the Trust Agent (a Vertex AI Reasoning Engine) for analysis
4. Service finds or opens a Pull Request from `update` into `main` and posts the
   analysis as a PR comment
5. If the verdict is `**PROCEED**` — and not `**DO NOT PROCEED**` or
   `**PROCEED WITH CAUTION**` — the PR is merged automatically

## Architecture

```
Gitea (push to update) → Webhook Service → Trust Agent (reasoningEngines/…:query)
                              ↓
                    find or create PR update → main
                              ↓
                    post comment, auto-merge on PROCEED
```

The Trust Agent is deployed to Vertex AI Agent Runtime, not Cloud Run, so
`TRUST_AGENT_URL` is a resource path (`projects/…/locations/…/reasoningEngines/…`),
not an HTTPS URL. `src/a2a_client.py` turns it into a `:query` call signed with
Application Default Credentials — despite the filename, no A2A protocol is involved.

## Deployment

### Prerequisites

- The agents deployed, so `agents-in-agent-runtime/trust/deployment_metadata.json`
  exists. Without it the webhook deploys with an empty `TRUST_AGENT_URL` and every
  delivery fails; re-run this script once the agents are up.
- Gitea deployed (optional at deploy time — the URL is discovered at runtime otherwise)

### Deploy

```bash
# Full installation
./install.sh --all

# Or deploy webhook service separately
cd services/gitea-webhook && ./install.sh
```

### Deployment Flow

1. **Build** — builds the Docker image and pushes it to Artifact Registry
2. **Discover** — reads the Trust Agent engine id from `deployment_metadata.json`
   and the Gitea URL from `services/gitea/gitea-url.sh`
3. **Deploy** — deploys to Cloud Run with `--no-allow-unauthenticated`
4. **Register** — `services/gitea/seed.sh` registers the hook on each repo at
   `${WEBHOOK_URL}/webhook/gitea/trust`, filtered to the `update` branch

## Configuration

### Environment Variables

Set in the Cloud Run deployment:

- `TRUST_AGENT_URL` — Reasoning Engine resource path of the Trust Agent
- `GITEA_URL` — Gitea base URL, `https://<ip>`. If unset the service asks the Compute API
  for the reserved regional address (this image has no gcloud). Gitea runs on GKE behind an
  L4 load balancer with a self-signed certificate, so the client does not verify TLS.
- `GITEA_API_TOKEN` — Gitea token used to read diffs and write PR comments
- `WEBHOOK_SECRET` — HMAC secret; **if empty, signature verification is skipped**
- `GITEA_BASE_BRANCH` — PR target branch, default `main`. `seed.sh` creates every
  repo with default branch `main` and cuts `update` from it, so `master` would 404.
- `GITEA_ADDRESS_NAME` — reserved regional address backing Gitea, default `gitea-ip`

## Testing

### Manual Webhook Trigger

Push a change to the update branch of a seeded repo:

```bash
git checkout update
git commit --allow-empty -m "test webhook trigger"
git push origin update
```

### Check Webhook Delivery

1. Gitea UI → repo → Settings → Webhooks
2. Click on the webhook → Recent Deliveries
3. View request/response

### Check Logs

```bash
# Webhook service logs
gcloud run services logs read gitea-webhook --region=$GOOGLE_REGION

# Trust Agent logs (Agent Runtime, not Cloud Run)
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"' --limit 50
```

## API Endpoints

### `GET /health`

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "gitea-trust-webhook",
  "trust_agent_configured": true
}
```

### `POST /webhook/gitea/trust`
### `POST /webhook/gitea/impact`

Receives Gitea webhook push events. Both paths run the same handler: `seed.sh`
registers `/trust`, and `/impact` is kept as an alias for hooks registered before
the rename.

**Headers:**
- `X-Gitea-Signature` — HMAC-SHA256 signature of payload

**Payload:** Gitea push event JSON

**Response:**
```json
{
  "status": "success",
  "commit": "abc123d",
  "branch": "update",
  "pr_number": 7,
  "analysis_posted": true
}
```

Pushes to any branch other than `update`, and pushes with no commits, return
`{"status": "ignored", …}` without calling the agent.

## Security

- **HMAC Validation** — webhooks are validated using a shared secret. `install.sh`
  derives it deterministically from the project id, so anyone who knows the project
  id can forge a delivery; and if `WEBHOOK_SECRET` is empty, `verify_signature()`
  returns `True` and validation is skipped entirely.
- **HTTPS Only** — all communication over HTTPS
- **Service Account** — the Cloud Run service account's ADC token is what
  authenticates the `:query` call to the Reasoning Engine
- **Authenticated Access** — deployed `--no-allow-unauthenticated`; Gitea must call
  it with an identity that holds `roles/run.invoker`

## Troubleshooting

### Webhook Not Firing

Check the Gitea webhook configuration:
```bash
GITEA_URL=$(bash services/gitea/gitea-url.sh)
# -k because Gitea terminates TLS with a self-signed certificate
curl -sk "$GITEA_URL/api/v1/repos/$WEBAPPS_LOGIN/network/hooks" \
  -u "$WEBAPPS_LOGIN:$WEBAPPS_PWD"
```

The hook is registered with `branch_filter: update` — pushes to `main` are ignored on
purpose, so only the designer agent's proposals wake the trust agent.

### Trust Agent Fires but the Installer Exited 60

curl exit 60 is "peer certificate not OK". Every curl against Gitea needs `-k`. If the
installer instead fails at hook registration with HTTP 401, the stored `GITEA_API_TOKEN`
is stale — it was minted against a Gitea database that no longer exists. The installer
now probes `/api/v1/user` with the stored token and re-mints on anything but 200.

### Trust Agent Not Responding

Verify the engine id the webhook was deployed with resolves:
```bash
gcloud run services describe gitea-webhook --region=$GOOGLE_REGION \
  --format='value(spec.template.spec.containers[0].env)' | tr ',' '\n' | grep TRUST_AGENT_URL
```

### PR Not Created or Comment Not Posted

The most common causes are an invalid `GITEA_API_TOKEN` and a base branch that does
not exist. Confirm the repo's default branch is `main`, or set `GITEA_BASE_BRANCH`.

### Signature Validation Failing

The secret is `sha256("${GOOGLE_PROJECT}-webhook-secret")`; re-running both
`services/gitea-webhook/install.sh` and `services/gitea/install.sh` keeps the two
sides in sync.

## Development

### Local Testing

```bash
cd services/gitea-webhook

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TRUST_AGENT_URL="projects/123/locations/europe-west3/reasoningEngines/456"
export GITEA_URL="https://34.156.121.236"   # the reserved 'gitea-ip' address
export GITEA_API_TOKEN="your_token"
export WEBHOOK_SECRET="your_secret"

# Run locally
python src/main.py
```

### Testing Webhook Locally

Use ngrok or similar to expose the local port, then configure a Gitea webhook to
point at your tunnel URL.

## Architecture Components

### `main.py`
FastAPI application that:
- Receives and validates webhooks
- Filters for the `update` branch only
- Orchestrates diff fetching and analysis
- Finds or creates the PR, comments on it, and auto-merges on `**PROCEED**`

### `a2a_client.py`
`ImpactAgentClient` — REST client for the Trust Agent Reasoning Engine:
- Acquires an ADC token
- Derives the region from the resource path
- POSTs `{"classMethod": "query", "input": {"request": prompt}}` to `:query`
- Returns the analysis text

### `gitea_client.py`
Gitea API client:
- Fetches commit diffs and file lists
- Lists, creates, comments on and merges Pull Requests
- Posts commit comments
- Handles SSL cert validation

## Related Documentation

- [Gitea Service](../gitea/Readme.md)
