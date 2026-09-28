# IAM and Service Account Strategy

This document explains the IAM (Identity and Access Management) and service account architecture for the Autonomous Network Lab.

## Service Account Overview

The system uses two types of service accounts:

### 1. Custom Service Account: `networkagent@{PROJECT}.iam.gserviceaccount.com`

**Used by:**
- Cloud Run services (sharedmcp, transport-intentserver, etc.)
- GKE workloads (controller, network simulator)

**Permissions (project-level, granted by root `install.sh`):**
- `roles/editor` — broad write access (legacy; target for reduction)
- `roles/run.admin` — full Cloud Run admin; implicitly covers invoking all services
- `roles/container.admin` — GKE full admin
- `roles/spanner.databaseUser` — all Spanner databases
- `roles/storage.objectAdmin` — all GCS buckets
- `roles/iam.serviceAccountAdmin` + `roles/iam.serviceAccountTokenCreator`
- `roles/aiplatform.user` + `roles/aiplatform.admin`
- `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/monitoring.viewer`
- `roles/pubsub.editor`, `roles/pubsub.subscriber`

**Purpose:**
Primary service account for all workloads. Deliberately broad for this lab; see Future Improvements for the reduction plan.

**Note on `run.admin`:** Because `networkagent` has project-level `roles/run.admin`, per-service `roles/run.invoker` bindings for this account are redundant and are not added by individual service install scripts.

### 2. Google-Managed Service Account: `service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`

**Used by:**
- Vertex AI Reasoning Engines (transport-orchestrator, core-orchestrator, radio-orchestrator, service-orchestrator)

**Permissions:**
- Automatically granted by Google Cloud for Vertex AI Agent Runtime
- Plus custom grants: Cloud Run Invoker for specific MCP servers

**Purpose:**
This is Google's service agent for Vertex AI Reasoning Engines. It runs the agent code and needs to call out to MCP tool servers.

## Architecture Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                          User/Client                            │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTPS
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  transport-intentserver (Cloud Run)                             │
│  Service Account: networkagent@PROJECT.iam.gserviceaccount.com  │
│  Authentication: --no-allow-unauthenticated                     │
│  Caller needs roles/run.invoker                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ A2A (HTTPS + Bearer token)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  transport-orchestrator (Vertex AI Agent Runtime)               │
│  Service Account: service-PROJNUM@gcp-sa-aiplatform-re.iam...   │
│  Authentication: Google Cloud default credentials               │
└──────────────┬────────────────────────────┬─────────────────────┘
               │ MCP over HTTPS             │ MCP over HTTPS
               │ (Bearer token)             │ (no auth)
               ▼                            ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│  sharedmcp (Cloud Run)       │  │  gitea (GKE Deployment)      │
│  SA: networkagent@...        │  │  L4 LB on reserved `gitea-ip`│
│  Auth: IAM invoker required  │  │  Auth: Gitea basic auth      │
│  + GoogleOIDCTokenVerifier   │  │  (WEBAPPS_LOGIN/WEBAPPS_PWD) │
│                              │  │                              │
│  IAM Policy:                 │  │  TLS: self-signed — clients  │
│  - service-PROJNUM@...       │  │  do not verify the cert      │
│    role: roles/run.invoker   │  │                              │
└──────────────────────────────┘  └──────────────────────────────┘
```

**Outside this chain: the Gemini Enterprise MCP server.**

```
┌─────────────────────────────────────────────────────────────────┐
│  Gemini Enterprise (Tool Data Store)                            │
│  Cannot present a Google ID token on this connection            │
└────────────────────────────┬────────────────────────────────────┘
                             │ MCP over HTTPS
                             │ Authorization: Bearer <shared secret>
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  mcp_server_for_ge/ge (Cloud Run)                               │
│  SA: networkagent@...                                           │
│  Auth: --allow-unauthenticated at the edge; the app rejects any  │
│        call without MCP_SHARED_SECRET (Secret Manager)          │
│  Tool set: read-only — MCP_ENABLE_MUTATING_TOOLS is unset, so    │
│        the six write tools are never registered                 │
│  IAM Policy: allUsers (required; GE has no principal to grant)   │
└─────────────────────────────────────────────────────────────────┘
```

Its twin, `mcp_server_for_ge/re`, *is* on the chain above: `--no-allow-unauthenticated` plus an
in-app OIDC audience check, and mutating tools enabled. The two share every source file except
`src/main.py`. See [SECURITY.md](SECURITY.md#gemini-enterprise-why-one-mcp-server-is-public).

## Permission Flows

### 1. Intent API → Orchestrator (A2A Protocol)

**Request Flow:**
1. Client POSTs intent to `transport-intentserver`, presenting an identity token
   (the service is deployed `--no-allow-unauthenticated`; the caller needs
   `roles/run.invoker`)
2. `transport-intentserver` authenticates to `transport-orchestrator` using Google Cloud default credentials
3. Orchestrator processes the request using its service account

**Code Location:** [domains/transport/services/intentserver/src/clients/orchestrator_client.py](../domains/transport/services/intentserver/src/clients/orchestrator_client.py)

**Authentication Implementation:**
```python
class GoogleCloudAuth(httpx.Auth):
    """Custom auth handler for Google Cloud API requests"""
    def __init__(self):
        self.credentials, self.project = default()

    def auth_flow(self, request):
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        request.headers['Authorization'] = f'Bearer {self.credentials.token}'
        yield request

# In client initialization:
httpx_client = httpx.AsyncClient(
    auth=GoogleCloudAuth(),
    timeout=httpx.Timeout(timeout=300.0, connect=10.0, read=120.0, write=30.0)
)
```

**Why this works:**
- `transport-intentserver` runs as `networkagent@...` service account
- When deployed to Cloud Run, it automatically gets credentials for this service account
- `google.auth.default()` picks up these credentials
- The credentials are used to generate Bearer tokens for authenticated requests

### 2. Orchestrator → MCP Servers

**sharedmcp (authenticated):**
1. Orchestrator calls `https://sharedmcp-v7kvgiazva-ew.a.run.app` with Bearer token
2. Cloud Run validates the token and checks IAM policy
3. IAM policy allows `service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`
4. Request is allowed

**Setup:** IAM binding added in [services/sharedmcp/install.sh](../services/sharedmcp/install.sh):
```bash
gcloud run services add-iam-policy-binding sharedmcp \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

**gitea (no GCP-level auth):**
1. Orchestrator calls `https://<gitea-ip>/api/v1/...` — the reserved regional address
   `gitea-ip`, served by Gitea's L4 LoadBalancer Service on GKE
2. There is no IAM check in front of it; Gitea authenticates the caller itself with the
   `WEBAPPS_LOGIN`/`WEBAPPS_PWD` basic-auth pair or an API token
3. TLS is terminated by Gitea using a self-signed certificate whose SAN covers the IP, so
   every client passes `verify=False` / `-k` / `noSSLVerify: true`

**Why different strategies:**
- **sharedmcp**: Authenticated because it provides privileged tools (Spanner access, K8s access, GCP API access)
- **gitea**: No GCP-level auth because it's already a public-facing Git server with its own auth layer

### 3. Orchestrator → Spanner / GCP APIs

**Flow:**
1. Orchestrator uses MCP tools from `sharedmcp`
2. `sharedmcp` runs as `networkagent@...` service account
3. This service account has `roles/spanner.databaseUser` for database access
4. Spanner allows the connection

## Security Model

### Defense in Depth Layers

1. **Network Layer**
   - All services use HTTPS
   - Cloud Run services are only accessible via HTTPS endpoints
   - No direct access to Spanner or GKE from public internet

2. **Authentication Layer**
   - Service-to-service calls use Google Cloud authentication (Bearer tokens)
   - Tokens are short-lived and automatically rotated
   - No static API keys or passwords

3. **Authorization Layer (IAM)**
   - Least privilege: Each service account only gets permissions it needs
   - Explicit grants: IAM bindings are explicit (no wildcards like `allUsers` except for truly public services)
   - Auditability: All IAM changes are logged in Cloud Audit Logs

4. **Application Layer**
   - Intent API validates TMF921 schemas before processing
   - Orchestrator validates network designs before deployment
   - MCP tools validate inputs and enforce business logic

### Service Account Permission Matrix

| Service Account | Cloud Run Invoke | Spanner Access | K8s Access | Artifact Registry |
|-----------------|------------------|----------------|------------|-------------------|
| `networkagent@...` | ✓ (`run.admin` — all services) | ✓ (all DBs) | ✓ (`container.admin`) | ✓ (read + write) |
| `service-PROJNUM@gcp-sa-aiplatform-re...` | ✓ (`run.invoker` on sharedmcp) | ✓ (`databaseUser` on agentops-db) | ✗ | ✗ |
| `PROJNUM-compute@...` | ✓ (`run.invoker` on logcollector) | ✓ (`databaseUser` on agentops-db) | ✗ | ✗ |

## Common IAM Patterns

### Pattern 1: Cloud Run Service Calling Another Cloud Run Service

**Example:** supervisor → sharedmcp (both run as `networkagent`)

Because `networkagent` has project-level `roles/run.admin`, no per-service `run.invoker` binding is needed. The caller just fetches an ID token for the target service URL:

**Code:**
```python
import google.auth.transport.requests
import google.oauth2.id_token

audience = "https://sharedmcp-<hash>-ew.a.run.app"
token = google.oauth2.id_token.fetch_id_token(
    google.auth.transport.requests.Request(), audience
)
headers = {"Authorization": f"Bearer {token}"}
response = requests.get(f"{audience}/mcp", headers=headers)
```

For services running as a **different** service account (not `networkagent`), add a per-service binding:
```bash
gcloud run services add-iam-policy-binding sharedmcp \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:OTHER_SA@${GOOGLE_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

### Pattern 2: Vertex AI Agent Calling MCP Server

**Example:** transport-orchestrator → sharedmcp

**IAM setup** (done once in `services/sharedmcp/install.sh`):
```bash
gcloud run services add-iam-policy-binding sharedmcp \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

**Application-level auth** (`sharedmcp` validates the caller's Google OIDC token):
- `sharedmcp` is deployed with `MCP_AUDIENCE` set to its own URL
- `GoogleOIDCTokenVerifier` (in `services/sharedmcp/src/utils/auth.py`) verifies the token and enforces an email allowlist of trusted service accounts
- Agents set `MCP_AUTH_ENABLED=true` (default) to attach Bearer tokens via `mcp_auth.py`; set `MCP_AUTH_ENABLED=false` for local dev

**Code:** Google ADK / MCP SDK handles token fetching automatically when `httpx_client_factory` is wired up

### Pattern 3: Authenticated API with Internal Authentication

**Example:** transport-intentserver (IAM-gated) → transport-orchestrator (authenticated)

**Setup:**
```bash
# Deploy intentserver IAM-gated. It exposes POST/PATCH/DELETE and reaches the
# orchestrator with its own SA credentials, so it must not be public.
gcloud run deploy transport-intentserver --no-allow-unauthenticated

# Grant run.invoker to each principal that legitimately calls it
gcloud run services add-iam-policy-binding transport-intentserver \
    --region="$GOOGLE_REGION" --member=<principal> --role=roles/run.invoker

# Orchestrator uses default GCP authentication (automatic)
```

**Code:** See orchestrator_client.py GoogleCloudAuth class above

## Troubleshooting IAM Issues

### 403 Forbidden Errors

**Symptom:** Cloud Run service returns 403 when called

**Diagnosis:**
```bash
# Check who can invoke the service
gcloud run services get-iam-policy SERVICE_NAME --region=REGION

# Check what service account is calling
# (look at Cloud Run logs for "Authorization: Bearer ..." header)
```

**Fix:**
```bash
# Grant invoker permission
gcloud run services add-iam-policy-binding SERVICE_NAME \
    --region=REGION \
    --member="serviceAccount:CALLER_SA@..." \
    --role="roles/run.invoker"
```

### 401 Unauthorized Errors

**Symptom:** Request fails with "Client error '401 Unauthorized'"

**Diagnosis:**
- No authentication token is being sent
- Check if the calling code is using `google.auth.default()` or similar

**Fix:**
- Add Google Cloud authentication to the HTTP client (see GoogleCloudAuth example above)

### Permission Denied on Spanner

**Symptom:** "Permission denied" when accessing Spanner

**Diagnosis:**
```bash
# Check service account permissions
gcloud projects get-iam-policy PROJECT_ID \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:SA_EMAIL"
```

**Fix:**
```bash
# Grant Spanner database user role
gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:SA_EMAIL" \
    --role="roles/spanner.databaseUser"
```

## Best Practices

1. **Use service accounts, not user accounts:** All automated workloads should use service accounts
2. **Principle of least privilege:** Grant only the permissions needed for the specific task
3. **Avoid `allUsers` and `allAuthenticatedUsers`:** Only use for truly public services (like gitea)
4. **Use IAM conditions when possible:** Restrict access by time, IP, or resource attributes
5. **Audit regularly:** Review IAM bindings periodically to remove unnecessary grants
6. **Rotate service account keys:** Minimise key file usage; the GKE controller now uses Workload Identity (no key file mounted)
7. **Workload Identity for GKE:** The controller pod uses a KSA annotated with `iam.gke.io/gcp-service-account`; ADC picks up the federated token automatically — no key file needed

## References

- [Cloud Run Authentication](https://cloud.google.com/run/docs/authenticating/service-to-service)
- [Vertex AI Agent Runtime Authentication](https://cloud.google.com/vertex-ai/docs/reasoning-engine/authentication)
- [Service Accounts Best Practices](https://cloud.google.com/iam/docs/best-practices-service-accounts)
- [A2A Protocol Specification](https://github.com/google/a2a)
