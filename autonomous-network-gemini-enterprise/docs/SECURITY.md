# Security Architecture

This document describes the security architecture and authentication strategy for the Autonomous Network Lab platform.

## Overview

The platform implements **defense-in-depth** with multiple authentication layers from the internet to backend resources. Each layer enforces authentication and authorization appropriate to its security requirements.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Security Layers                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Layer 1: Cloud Run IAM                                        │
│  ┌──────────┐                                                  │
│  │ Browser  │ ─────► dashboard-v2 (Cloud Run,                 │
│  └──────────┘        --no-allow-unauthenticated)              │
│       │              Flutter web app + aiohttp server.py      │
│       │              Caller needs roles/run.invoker           │
│       │                                                        │
│       │ Layer 2: Service Account (server.py acts as an        │
│       │ IAM proxy; it mints an ID token for the engine)       │
│       ▼                                                        │
│  Supervisor (Vertex AI Agent Runtime / Reasoning Engine)      │
│       │                                                        │
│       │ Layer 3: Service Account (A2A Protocol)              │
│       │ A2A client library handles auth                      │
│       ▼                                                        │
│  Domain Orchestrators (Vertex AI Agent Runtime)              │
│  - transport-orchestrator                                     │
│  - core-orchestrator                                          │
│  - radio-orchestrator                                         │
│  - service-orchestrator                                       │
│       │                                                        │
│       │ Layer 4: Service Account (SSE)                       │
│       │ ADK adds service account credentials                 │
│       ▼                                                        │
│  MCP Servers (Cloud Run, --no-allow-unauthenticated)         │
│  - transport-mcp                                              │
│  - sharedmcp                                                  │
│  - mcp_server_for_ge/re  (OIDC audience check in-app)        │
│                                                                │
│  MCP Server for Gemini Enterprise — off this chain            │
│  - mcp_server_for_ge/ge  (--allow-unauthenticated +           │
│    shared bearer secret; read-only tool set. GE cannot        │
│    present a Google ID token, so it cannot join the chain     │
│    above. See "Gemini Enterprise" below.)                     │
│       │                                                        │
│       │ Layer 5: Service Account (ID Tokens)                 │
│       │ Authorization: Bearer <id_token>                     │
│       ▼                                                        │
│  API Servers (Cloud Run, --no-allow-unauthenticated)         │
│  - transport-apiserver                                        │
│  - transport-topologyserver                                   │
│       │                                                        │
│       │ Layer 6: IAM Permissions                             │
│       │ roles/run.invoker on specific services              │
│       │ roles/spanner.databaseUser                           │
│       ▼                                                        │
│  Backend Resources                                            │
│  - Spanner databases                                          │
│  - Kubernetes API                                             │
│  - Cloud APIs (Logging, Trace, Monitoring)                   │
│                                                                │
└─────────────────────────────────────────────────────────────────┘
```

## Authentication Layers

### Layer 1: Cloud Run IAM (Browser → Dashboard)

**Service**: `dashboard-v2` (Cloud Run)
**Authentication**: Cloud Run IAM — deployed `--no-allow-unauthenticated`
**Authorization**: caller needs `roles/run.invoker`

`dashboard-v2` is a Flutter web app served by an aiohttp `server.py`, deployed together as one
Cloud Run service. It is **not** public: `install.sh` deploys it `--no-allow-unauthenticated`, so
the browser must present an identity Cloud Run accepts.

> **Superseded design.** This used to be a public static-content dashboard fronting a separate
> `networksupervisor` Cloud Run service protected by an API key in Secret Manager
> (`supervisor-api-key`). Neither exists any more — the supervisor moved to Vertex AI Agent Runtime,
> and no `SUPERVISOR_API_KEY` appears anywhere in the tree. See [Layer 2](#layer-2-service-account-dashboard--supervisor).

---

### Layer 2: Service Account (Dashboard → Supervisor)

**Service**: supervisor, as a Vertex AI Agent Runtime (Reasoning Engine) — not a Cloud Run service
**Authentication**: Google-signed ID token, minted server-side
**Authorization**: the dashboard service account's IAM on the Reasoning Engine

**How it works**:
1. The browser calls the dashboard's own aiohttp routes; no credential crosses the browser
   boundary and none is embedded in the Flutter bundle.
2. `server.py` acts as an IAM proxy: `_get_auth_token()` obtains a token from the runtime's
   application-default credentials.
3. It calls the supervisor Reasoning Engine by resource name
   (`projects/<p>/locations/<r>/reasoningEngines/<id>`) with that token.

**Implementation**:
- Proxy and token minting: [dashboard-v2/server.py](../dashboard-v2/server.py)
- Dashboard REST: [dashboard-v2/lib/utils/APIService.dart](../dashboard-v2/lib/utils/APIService.dart)
- Vestigial Socket.IO client: [dashboard-v2/lib/appstate.dart](../dashboard-v2/lib/appstate.dart)
  and [dashboard-v2/lib/agent/socket_provider.dart](../dashboard-v2/lib/agent/socket_provider.dart).
  Both are dead: the `network-agent-supervisor` gateway they dial no longer exists, and nothing
  references `socket_provider.dart` at all. No channel, so no auth boundary — but the code is
  still in the tree, so do not read it as one.

**Security properties**:
- ✅ No long-lived shared secret anywhere in the path; tokens are short-lived and Google-signed
- ✅ Nothing sensitive is reachable from the browser's own credentials
- ✅ Removes the "API key visible in browser dev tools" weakness of the previous design

---

### Layer 3: Service Account Authentication (Supervisor → Agents)

**Service**: Domain Orchestrators (Vertex AI Agent Runtime)  
**Authentication**: A2A Protocol  
**Authorization**: Service account permissions

**How it works**:
1. Supervisor obtains service account credentials from the Agent Runtime metadata server via
   `google.auth.default()` — no key file is baked into the deployment
2. A2A client library (`a2a.client.create_client`) automatically adds credentials to requests
3. Agent Runtime validates service account identity

**Implementation**:
- Client: [agents-in-agent-runtime/supervisor/app/](../agents-in-agent-runtime/supervisor/app/)
- Service account: `networkagent@{PROJECT}.iam.gserviceaccount.com`

**Security properties**:
- ✅ Strong cryptographic authentication (service account keys)
- ✅ Managed by Google Cloud IAM
- ✅ Audit trail in Cloud Logging
- ✅ Automatic token refresh

---

### Layer 4: Service Account Authentication (Agents → MCP Servers)

**Service**: MCP Servers (Cloud Run)  
**Authentication**: Google OIDC ID tokens (Bearer) + Cloud Run IAM  
**Authorization**: Cloud Run IAM (outer) + `GoogleOIDCTokenVerifier` (inner, application-level)

**How it works**:
1. ADK transport-orchestrator subagents (`planner`, `designer`, `deploy_crs`) use `create_authenticated_mcp_client` when `MCP_AUTH_ENABLED=true`
2. The client fetches a Google OIDC ID token for the sharedmcp audience and attaches it as `Authorization: Bearer <id_token>`
3. Cloud Run validates the token at the platform level (`--no-allow-unauthenticated`)
4. sharedmcp's `GoogleOIDCTokenVerifier` validates the token a second time at the application level, enforcing an email allowlist

**Implementation**:
- Auth client: [domains/transport/agents/transport-orchestrator/app/mcp_auth.py](../domains/transport/agents/transport-orchestrator/app/mcp_auth.py)
- Auth config: `MCP_AUTH_ENABLED` in [app/config.py](../domains/transport/agents/transport-orchestrator/app/config.py) (decoupled from `DEBUG`)
- Server verifier: [services/sharedmcp/src/utils/auth.py](../services/sharedmcp/src/utils/auth.py)
- Wired into FastMCP: [services/sharedmcp/src/utils/globals.py](../services/sharedmcp/src/utils/globals.py) — activates when `MCP_AUDIENCE` env var is set

**Security properties**:
- ✅ Defense-in-depth: Cloud Run IAM + application-level OIDC verification
- ✅ Caller email allowlist enforced in `GoogleOIDCTokenVerifier`
- ✅ `MCP_AUTH_ENABLED` defaults to `true`; local dev sets it to `false` (does not disable via `DEBUG`)
- ✅ MCP servers are private (not publicly accessible)

---

### Layer 5: Service Account ID Tokens (MCP Servers → API Servers)

**Service**: API Servers (Cloud Run)  
**Authentication**: HTTP with ID tokens  
**Authorization**: Cloud Run IAM

**How it works**:
1. MCP HTTP client generates ID token for target API server URL
2. Uses `google.oauth2.id_token.fetch_id_token(audience=<url>)`
3. Adds `Authorization: Bearer <id_token>` header to requests
4. API server validates token and service account identity

**Implementation**:
- HTTP client: [domains/service/services/mcp/src/utils/http_client.py](../domains/service/services/mcp/src/utils/http_client.py)
- Dependencies: `google-auth>=2.36.0` in [pyproject.toml](../domains/service/services/mcp/pyproject.toml)

**Key code**:
```python
def _get_auth_headers(self, url: str) -> dict:
    parsed = urlparse(url)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    id_token = google.oauth2.id_token.fetch_id_token(self._auth_request, audience)
    return {"Authorization": f"Bearer {id_token}"}
```

**Security properties**:
- ✅ ID tokens are short-lived (1 hour)
- ✅ Audience-specific (bound to target service URL)
- ✅ Cryptographically signed by Google
- ✅ API servers validate signature and expiration
- ✅ Automatic credential refresh from metadata server

---

### Layer 6: IAM Permissions (Services → Backend Resources)

**Services**: All Cloud Run services, Kubernetes operator  
**Authentication**: Service account credentials (ADC)  
**Authorization**: IAM roles on specific resources

**Service Account**: `networkagent@{PROJECT}.iam.gserviceaccount.com`

**IAM Roles**:
- `roles/aiplatform.user` - Deploy and invoke Agent Runtime
- `roles/spanner.databaseUser` - Read/write Spanner databases
- `roles/logging.logWriter` - Write logs to Cloud Logging
- `roles/cloudtrace.agent` - Send traces to Cloud Trace
- `roles/monitoring.metricWriter` - Send metrics to Cloud Monitoring
- `roles/storage.objectAdmin` - Access Cloud Storage
- `roles/iam.serviceAccountTokenCreator` - Generate ID tokens for Cloud Run invocation
- `roles/secretmanager.secretAccessor` - Read supervisor API key from Secret Manager
- `roles/container.admin` - Manage GKE resources
- `roles/compute.admin` - Manage Compute Engine VMs
- `roles/pubsub.editor` - Publish/subscribe to Pub/Sub topics

**Per-Service IAM Bindings**:
- `networkagent` has `roles/run.invoker` on:
  - `transport-apiserver`
  - `transport-topologyserver`
  - (others as needed)

**Security properties**:
- ✅ Least-privilege IAM (specific roles, not `roles/editor`)
- ✅ Per-service access control (explicit invoker grants)
- ✅ Audit trail in Cloud Logging (service account attribution)
- ✅ Secrets stored in Secret Manager (not environment variables)

---

## Service Inventory

### Public Services (--allow-unauthenticated)

| Service | Path | Authentication | Purpose |
|---------|------|----------------|---------|
| `gitea` | services/gitea | Gitea built-in auth | Git server for network designs |
| `mcp_server_for_ge/ge` | mcp_server_for_ge/ge | **Shared bearer secret, in-app** | MCP tools for Gemini Enterprise. Unauthenticated at the Cloud Run edge by necessity, not by choice — see below |

### Protected Services (--no-allow-unauthenticated)

| Service | Path | Caller | Authentication Method |
|---------|------|--------|----------------------|
| `dashboard-v2` | dashboard-v2/ | Browser | Cloud Run IAM; `server.py` proxies to the supervisor engine |
| `transport-mcp` | domains/service/services/mcp | transport-orchestrator | SSE (ADK auto-auth) |
| `sharedmcp` | services/sharedmcp | All orchestrators | SSE (ADK auto-auth) |
| `transport-apiserver` | domains/transport/services/apiserver | transport-mcp | HTTP ID tokens |
| `transport-topologyserver` | domains/transport/services/topologyserver | transport-mcp | HTTP ID tokens |
| `gitea-webhook` | services/gitea-webhook | Gitea's webhook sender | Public Cloud Run + HMAC-SHA256 body signature (see below) |
| `mcp_server_for_ge/re` | mcp_server_for_ge/re | Reasoning Engine agents | Cloud Run IAM, plus an in-app OIDC audience check (`MCP_AUDIENCE`) |

### Gemini Enterprise: why one MCP server is public

Gemini Enterprise connects to an MCP server as a **Tool Data Store**. It cannot present a Google
ID token on that connection, so `--no-allow-unauthenticated` and the in-app OIDC path both break
the integration outright rather than securing it. A pre-shared bearer secret is the only credential
GE can carry.

`mcp_server_for_ge/{re,ge}` are otherwise the same server — every file under `src/tools/` and
`src/utils/` is byte-identical, and only `src/main.py` differs (`re/` serves SSE, `ge/` serves
streamable-HTTP). The security difference is entirely in configuration, in two layers:

1. **`MCP_SHARED_SECRET`** — every call must carry the bearer secret, held in Secret Manager and
   injected via `--set-secrets`. Reaching the URL is not the same as calling a tool.
2. **`MCP_ENABLE_MUTATING_TOOLS`** — unset on `ge/`, so the six write tools (`deployDescriptors`,
   `deleteDescriptor`, `create_device`, `delete_device`, `runTest`, `deleteTest`) are never
   registered. The GE endpoint has no write surface to attack, independent of layer 1.

**Residual risk, accepted:** the secret identifies the *deployment*, not a principal, and does not
expire. Rotation is `gcloud secrets versions add mcp-ge-shared-secret --data-file=-` followed by a
redeploy. Do not add write tools to `ge/` without replacing layer 1 with something better.

### gitea-webhook: why it is public

Same shape of problem, different caller. The only client is Gitea's own webhook sender, which does
a plain HTTP POST — it cannot mint a Google OIDC token. Under `--no-allow-unauthenticated` every
delivery is rejected by Cloud Run's IAM layer with a 403 before it reaches the app, and the trust
review loop is permanently dark. The service is therefore deployed `--allow-unauthenticated`, and
the gate is the signature instead: Gitea HMAC-SHA256s the raw request body with `WEBHOOK_SECRET`
and sends it as `X-Gitea-Signature`; `verify_signature()` in `src/main.py` recomputes and compares.

`verify_signature()` returns `True` with a warning when `WEBHOOK_SECRET` is empty — a develop-mode
escape hatch. `services/gitea-webhook/install.sh` always sets the variable, so that branch is not
reachable in a deployed environment. Do not deploy the service by hand without it.

**Residual risk, accepted:** the secret is `sha256("${GOOGLE_PROJECT}-webhook-secret")`, not random,
so anyone who knows the project id can compute it and forge a delivery. It is derived rather than
generated so that Gitea and the service stay in sync across independent redeployments of either.
The blast radius is bounded: a forged delivery can produce a spurious PR comment on the `network`
repo, and an auto-merge only if the trust agent independently returns `**PROCEED**` on the real
diff it fetches from Gitea — the attacker does not supply the diff being reviewed. Moving this to a
Secret Manager value read by both sides is the fix; see Future Improvements.

---

## Secret Management

### Secrets Stored in Secret Manager

| Secret Name | Purpose | Created By | Used By |
|-------------|---------|------------|---------|
| `mcp-ge-shared-secret` | Gemini Enterprise → `ge` MCP server | `mcp_server_for_ge/ge/deploy.sh` (auto-created, `openssl rand -hex 32`) | `ge` MCP server |

**Access control**:
- `networkagent` service account has `roles/secretmanager.secretAccessor`
- Secrets retrieved during deployment, injected as environment variables

### Service Account Keys

| Key File | Purpose | Location | Used By |
|----------|---------|----------|---------|
| `networkagent.json` | Service account credentials | Repo root only (local dev) | Local development only |

**Security notes**:
- ✅ Key is **no longer copied into any service or container** — `install.sh` stopped distributing it to `controller/src`, `services/*/src`, etc.
- ✅ GKE controller uses **Workload Identity** — `networkoperator-account` KSA is annotated with `iam.gke.io/gcp-service-account` and no key file is baked into the image
- ✅ All GCP clients in the controller use `google.auth.default()` — they pick up the WI token automatically
- ⚠️ Cloud Run services (sharedmcp, the MCP twins, the collectors, gitea-webhook, the dashboard) rely on ADC from the Cloud Run metadata server — no key needed at runtime. The agent fleet does the same from Vertex AI Agent Runtime
- 🎯 **Remaining**: Formally delete the key file after Workload Identity is confirmed stable; cloud services already work without it

---

## Deployment Security

### Cloud Run Deployment Patterns

**Public services** — one on Cloud Run, and one reachable outside it:

- `mcp_server_for_ge/ge` (`mcp_server_for_ge/ge/deploy.sh`) — Gemini Enterprise cannot present
  an ID token. Authenticated in-app by a shared bearer secret instead; see the section above.
- `gitea` — **not a Cloud Run service any more.** It is a GKE `Deployment` behind an L4
  `Service type=LoadBalancer` on the reserved regional address `gitea-ip`, so Cloud Run IAM does
  not apply to it at all. It carries its own login (`WEBAPPS_LOGIN`/`WEBAPPS_PWD`) and serves
  HTTPS with a self-signed certificate, which is why every client passes `verify=False` / `-k` /
  `noSSLVerify: true`. Anyone who can route to the address reaches the sign-in page.

```bash
gcloud run deploy <service> \
    --allow-unauthenticated \
    --service-account networkagent@${PROJECT}.iam.gserviceaccount.com
```

> **Superseded.** This list used to read "dashboard, supervisor, gitea". Neither of the first two
> applies now: the dashboard deploys `--no-allow-unauthenticated` behind IAP (`install.sh:1086`),
> and the supervisor is no longer a Cloud Run service at all — it moved to Vertex AI Agent Runtime.

**Protected services** (dashboard, MCP, API servers):
```bash
gcloud run deploy <service> \
    --no-allow-unauthenticated \
    --service-account networkagent@${PROJECT}.iam.gserviceaccount.com
```

**Grant invoker permissions** (per-service IAM):
```bash
gcloud run services add-iam-policy-binding <service> \
    --member="serviceAccount:networkagent@${PROJECT}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

### Agent Runtime Deployment

```bash
agents-cli deploy \
    --project ${PROJECT} \
    --region ${REGION} \
    --service-account networkagent@${PROJECT}.iam.gserviceaccount.com
```

---

## Authentication Flow Examples

### Example 1: User Submits Intent via Dashboard

```
1. User opens dashboard in browser
   ↓ (no auth)

2. Dashboard loads (Cloud Run, public)
   ↓ (API key: Authorization: Bearer <key>)

3. Supervisor receives request
   ↓ Middleware validates API key
   ↓ (A2A: service account auth)

4. Supervisor calls transport-orchestrator
   ↓ A2A client adds credentials
   ↓ Agent Runtime validates service account
   ↓ (SSE: service account auth)

5. Orchestrator calls MCP tools
   ↓ ADK adds service account credentials
   ↓ MCP server validates via Cloud Run
   ↓ (HTTP ID token: Authorization: Bearer <id_token>)

6. MCP server calls transport-apiserver
   ↓ HTTP client generates ID token
   ↓ API server validates token
   ↓ (A2A: service account auth)

7. API server calls orchestrator to create VPN CR
   ↓ A2A client handles auth
   ↓ Orchestrator creates Kubernetes CR
   ↓ (Service account with K8s RBAC)

8. Result flows back through same chain
```

**Security checkpoints**: 5 authentication layers validated (API key, A2A, SSE, ID token, IAM)

---

### Example 2: Agent Queries Topology

```
1. Agent calls MCP tool: getTopology()
   ↓ (SSE: service account auth)

2. MCP server receives SSE request
   ↓ Cloud Run validates service account
   ↓ (HTTP ID token: Authorization: Bearer <id_token>)

3. MCP HTTP client calls transport-topologyserver
   ↓ Generates ID token for audience
   ↓ (Service account with Spanner permissions)

4. Topology server queries Spanner
   ↓ Uses ADC (service account from metadata server)
   ↓ Spanner validates roles/spanner.databaseUser

5. Result returned to agent
```

**Security checkpoints**: 3 authentication layers validated (SSE, ID token, Spanner IAM)

---

## Security Best Practices

### ✅ Implemented

1. **Defense-in-depth**: Multiple authentication layers
2. **Secret Manager**: API keys stored in Secret Manager, not env vars
3. **Audit trails**: All authenticated requests logged with service account attribution
4. **ID tokens**: Short-lived, audience-specific tokens for Cloud Run
5. **Workload Identity (controller)**: GKE operator uses WI — no key file in container image
6. **Application-level MCP auth**: `GoogleOIDCTokenVerifier` in sharedmcp validates caller identity beyond Cloud Run IAM
7. **MCP auth decoupled from debug mode**: `MCP_AUTH_ENABLED` flag controls auth independently of `DEBUG`
8. **IAM cleanup**: Removed redundant per-service `run.invoker` bindings for `networkagent` (covered by project-level `run.admin`) and duplicate invoker blocks across domain agent scripts

### 🎯 Future Improvements

1. **Workload Identity for Cloud Run services**: Supervisor, sharedmcp, etc. already use ADC from Cloud Run metadata server; formally delete `networkagent.json` once confirmed
2. **Domain-specific service accounts**: Separate credentials per domain (transport, core, radio)
3. **Remove `roles/editor` and `roles/run.admin`**: Replace with minimal permissions
4. **IAM audit logging**: Enable Data Access logs for Cloud Run
5. **Identity-Aware Proxy (IAP)**: Replace API key auth with Google Sign-In for production
6. **Rotate secrets**: Implement automatic API key rotation
7. **Webhook secret**: `gitea-webhook` already validates an HMAC signature, but the secret is derived from the project name rather than random. Generate it once, store it in Secret Manager, and have both `services/gitea-webhook/install.sh` and the hook registration read it from there

---

## Threat Model

### Threats Mitigated

| Threat | Mitigation |
|--------|------------|
| Unauthorized dashboard access | Cloud Run IAM (`--no-allow-unauthenticated`); the caller needs `roles/run.invoker` |
| Unauthorized API server access | Cloud Run IAM (`--no-allow-unauthenticated`) |
| Anonymous writes to the GitOps repo via the Gemini Enterprise MCP endpoint | Two layers: a shared bearer secret gates every call, and the six mutating tools are not registered on `ge/` at all |
| Service account compromise | Least-privilege IAM, per-service invoker grants |
| Credential exposure | Secrets in Secret Manager, not env vars |
| Man-in-the-middle | HTTPS/TLS for all Cloud Run services |
| Privilege escalation | No roles/editor on runtime services |

### Residual Risks

| Risk | Severity | Mitigation Plan |
|------|----------|-----------------|
| ~~API key visible in browser dev tools~~ | ~~Medium~~ | **Gone with the design** — the supervisor is a Reasoning Engine now and `server.py` mints the token server-side; no credential reaches the browser |
| `networkagent.json` on developer disk | Low | Delete after WI confirmed stable; already excluded from all container builds |
| Shared service account across all services | Low | Create domain-specific service accounts |
| Webhook secret derived from project name (not random) | Medium | Generate random secret, store in Secret Manager |
| ~~`transport-intentserver` deployed `--allow-unauthenticated`~~ | ~~Medium~~ | **Fixed** — now deployed `--no-allow-unauthenticated`; callers need `roles/run.invoker` |
| `mcp-ge-shared-secret` is static and does not expire | Medium | Inherent to what Gemini Enterprise can carry. It identifies the deployment, not a principal, and rotation is a redeploy. Bounded by the `ge/` endpoint having no write tools; revisit if GE ever gains OIDC support |
| ~~Supervisor API key passed as Socket.IO query param~~ | ~~Low~~ | **Gone** — the Socket.IO gateway was retired with the v1 dashboard; the console now calls the supervisor Reasoning Engine through an IAM-token proxy |
| Gitea serves a self-signed certificate, so every client disables verification | Low | Accepted: the lab has no hostname to certify. A client that skips verification cannot detect interception on the path to the address |

---

## Compliance & Audit

### Audit Logging

**What is logged**:
- All Cloud Run requests (caller service account, HTTP method, URL, status code)
- IAM policy changes (role grants/revokes)
- Secret Manager access (who read which secret, when)

**How to query**:
```bash
# Find all requests to a Cloud Run service
gcloud logging read "resource.type=cloud_run_revision \
  AND resource.labels.service_name=transport-apiserver" \
  --limit=100

# Find all requests from networkagent service account
gcloud logging read "protoPayload.authenticationInfo.principalEmail=networkagent@*" \
  --limit=100

# Find authentication failures
gcloud logging read "severity>=WARNING AND (jsonPayload.message=~'Invalid API key' \
  OR jsonPayload.message=~'403' OR jsonPayload.message=~'Forbidden')" \
  --limit=100
```

### Compliance Benefits

- ✅ **SOC2**: Defense-in-depth, audit trails, least-privilege access
- ✅ **GDPR**: Service account attribution for data access
- ✅ **HIPAA**: Encrypted in transit (TLS), access controls, audit logs

---

## Testing Authentication

**Quick smoke test**:
```bash
# Test 1: The dashboard rejects anonymous callers
curl https://<dashboard-v2>-<hash>.run.app/
# Expected: 403 Forbidden

# Test 2: A caller holding roles/run.invoker succeeds
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://<dashboard-v2>-<hash>.run.app/listagents
# Expected: 200 OK

# Test 3: Protected API servers reject unauthenticated requests
curl https://transport-apiserver-<hash>.run.app/intents
# Expected: 403 Forbidden

# Test 4: Dashboard loads and authenticates
# Open the dashboard in a browser (IAP sign-in), check the console for errors
# Expected: no 401/403; the chat panel streams from /streamQuery

# Test 5: The GE MCP endpoint is reachable but not callable without the secret
curl -X POST https://<ge-service>-<hash>.run.app/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# Expected: 401 — the Cloud Run edge lets the request through, the app does not

# Test 6: With the secret, only read tools are present
MCP_SECRET=$(gcloud secrets versions access latest --secret=mcp-ge-shared-secret)
curl -X POST https://<ge-service>-<hash>.run.app/mcp \
  -H "Authorization: Bearer $MCP_SECRET" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -c deployDescriptors
# Expected: 0 — the mutating tools are not registered on this deployment
```

---

## Support & References

- **Migration Plan**: [MIGRATION_PLAN.md](../MIGRATION_PLAN.md) — decisions and risk register
- **Google Cloud IAM**: https://cloud.google.com/iam/docs
- **Cloud Run Authentication**: https://cloud.google.com/run/docs/authenticating/service-to-service
- **Secret Manager**: https://cloud.google.com/secret-manager/docs
- **A2A Protocol**: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-runtime/a2a
- **ADK Documentation**: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-development-kit

For security issues or questions, see [CLAUDE.md](../CLAUDE.md) for development guidelines.
