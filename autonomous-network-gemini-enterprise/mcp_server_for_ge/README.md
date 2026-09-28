# `mcp_server_for_ge/` — the MCP tool plane, deployed twice

This directory holds the MCP server that exposes the network's topology, faults, logs and
lifecycle verbs as tools. It is deployed **twice**, as two Cloud Run services, because the two
consumers speak different transports and cannot share one endpoint:

| | `ge/` | `re/` |
|---|---|---|
| Cloud Run service | `mcp-networktools-ge` | `mcp-networktools` |
| Transport | `streamable-http` — flat POST on `/mcp` | `sse` — `/sse` + `/messages` |
| Consumer | Gemini Enterprise, connecting as a Tool Data Store | The agents on Vertex AI Agent Runtime (`designer`, `chaos`, `tester`, …), via `SseConnectionParams` |
| Cloud Run IAM | `--allow-unauthenticated` (forced — see below) | `--no-allow-unauthenticated` |
| In-app auth | shared bearer secret | Google OIDC + email allowlist |
| State-changing tools | **not registered** | registered |

Gemini Enterprise will not follow a `307` redirect on its first JSON-RPC pass, which is why it
gets `streamable-http` rather than sharing the `re/` endpoint.

## The two trees are one tree

**`re/src/` and `ge/src/` are byte-identical except for `main.py`.** The only differences are the
transport passed to `FastMCP.run()` and a log line. Everything else — all 20 `.py` files under
`tools/` and `utils/` — is a duplicate.

> **If you change anything under `src/` other than `main.py`, copy it to the twin in the same
> commit.** There is no build step that does this for you, and a fix applied to one side only will
> look correct in review and be missing in production on the other.

```bash
# after editing re/src/utils/foo.py
cp re/src/utils/foo.py ge/src/utils/foo.py
# verify nothing but main.py differs
diff -rq re/src ge/src -x __pycache__
```

## Layout

```text
mcp_server_for_ge/
├── deploy.sh                  # deploys ge/ then re/
├── variables.json.template    # copy to variables.json (git-ignored)
├── ge/  ┐
│   ├── src/main.py            # transport="streamable-http"
│   ├── src/tools/             #   ─ identical between the two ─
│   ├── src/utils/             #   ─ identical between the two ─
│   ├── Dockerfile
│   ├── deploy.sh              # Secret Manager + --allow-unauthenticated
│   └── variables.json         # SERVICE_NAME: mcp-networktools-ge
└── re/  ┘
    ├── src/main.py            # transport="sse"
    ├── deploy.sh              # --no-allow-unauthenticated + OIDC + run.invoker grants
    └── variables.json         # SERVICE_NAME: mcp-networktools
```

## Tools

| Module | Tools | Mutating |
|---|---|---|
| `topology.py` | `query_router_metadata`, `query_ospf_paths`, `query_link_utilization`, `query_affected_flows`, `query_flow_metrics` | |
| `devices.py` | `list_devices`, `getDevices`, `getDeviceByName`, `searchDevices`, `getNetworkDeployment` | `create_device`, `delete_device` |
| `descriptors.py` | | `deployDescriptors`, `deleteDescriptor` |
| `tests.py` | `getTrafficTestDefinition`, `getRunningTests` | `runTest`, `deleteTest` |
| `crds.py` | `getTransportCRDs`, `getCoreCRDs`, `getRadioCRDs`, `getDeviceDefinition`, `getTransportDesignDoc`, `getDeployedTransportCRs` | |
| `configdrift.py` | `getElementConfig`, `getRelatedElements`, `diffConfigs`, `getLastStableTimestamp`, `getConfigHistory` | |
| `capacity.py` | `identify_bottlenecks`, `recommend_capacity_upgrades` | |
| `faults.py` | `query_fault_events` | |
| `logs.py` | `fetch_log_entries`, `query_log_entries` | |
| `simulations.py` | `simulate_remediation_impact`, `simulate_traffic_addition` | |

Reads come from Spanner (six databases, see `src/utils/database.py`); the mutating tools write to
the Gitea GitOps repository the controller reconciles from, or drive the Kubernetes API directly.

## Authentication

Because the two services share a tool surface, any tool added for the authenticated `re/` twin
also lands on `ge/`. The auth model exists to make that safe.

### `re/` — Cloud Run IAM + OIDC

Deployed `--no-allow-unauthenticated`, so IAM decides who may invoke it at all. `deploy.sh` grants
`roles/run.invoker` to the Agent Runtime service agent, the default compute SA and the service's
own SA. `GoogleOIDCTokenVerifier` (`src/utils/auth.py`) then checks *which* identity called: the
token's audience must be the service's own URL (`MCP_AUDIENCE`, set in a second pass once Cloud Run
has assigned it) and its email must be on the allowlist in `src/utils/globals.py`. **Both halves
are required** — an IAM grant without an allowlist entry still fails, and vice versa.

### `ge/` — shared bearer secret + no write surface

Gemini Enterprise connects as a Tool Data Store and presents **no Google ID token**. Neither
`--no-allow-unauthenticated` nor the OIDC path can be used; both break the integration outright.
So the service stays publicly *routable*, and two things stop it being publicly *usable*:

1. **`MCP_SHARED_SECRET`** → `SharedSecretTokenVerifier`. `ge/deploy.sh` generates 64 hex
   characters with `openssl rand`, stores them in Secret Manager (`mcp-ge-shared-secret`, override
   with `MCP_SECRET_NAME`), grants the runtime SA `secretAccessor`, and mounts the secret with
   `--set-secrets`. Compared with `hmac.compare_digest`; a value under 32 characters raises at
   startup so a placeholder cannot pass for authentication.

   Read the secret to configure the GE tool data store's `Authorization: Bearer` header:

   ```bash
   gcloud secrets versions access latest --secret=mcp-ge-shared-secret --project="$GOOGLE_PROJECT"
   ```

   Rotation is a new secret version plus a redeploy of the service and an update of the header —
   the secret does not expire on its own.

2. **`MCP_ENABLE_MUTATING_TOOLS` is not set.** The `mutating_tool()` decorator in
   `src/utils/globals.py` registers a tool only when this is truthy, so `deployDescriptors`,
   `deleteDescriptor`, `create_device`, `delete_device`, `runTest` and `deleteTest` do not exist on
   `ge/` at all. Even with the secret, this endpoint cannot write to the GitOps repo or the
   cluster. `re/deploy.sh` opts in, behind IAM and OIDC.

   The decorator leaves the *plain function* bound to its own name, so an unregistered tool stays
   callable in-process. `delete_device` depends on this — it calls `deleteDescriptor` directly.

### Environment variables

| Variable | Set by | Effect |
|---|---|---|
| `MCP_AUDIENCE` | `re/deploy.sh`, second pass | Enables the OIDC verifier with this audience |
| `MCP_SHARED_SECRET` | `ge/deploy.sh`, from Secret Manager | Enables the shared-secret verifier. Only consulted when `MCP_AUDIENCE` is unset |
| `MCP_ENABLE_MUTATING_TOOLS` | `re/deploy.sh` (`true`) | Registers the state-changing tools |
| `GITEA_URL`, `WEBAPPS_LOGIN`, `WEBAPPS_PWD` | both `deploy.sh` | Gitea base URL and basic-auth credentials. If `GITEA_URL` is empty the service resolves the reserved regional address `gitea-ip` through the Compute API at runtime |
| `GOOGLE_SPANNER_*` | both `deploy.sh` | The six database names; passed explicitly so an overridden name does not silently fall back to the default |
| `GOOGLE_PROJECT`, `GOOGLE_PROJECT_NUMBER`, `GOOGLE_REGION` | both `deploy.sh` | Used for Spanner, the OIDC allowlist and Gitea discovery |

**Set neither `MCP_AUDIENCE` nor `MCP_SHARED_SECRET` and the server starts with no verifier at
all**, accepting anonymous calls from anyone who can reach it. It logs a warning at startup when
it does. Do not deploy that way.

## Deploying

Each of the three levels has its own `variables.json`, all git-ignored, all seeded from a
`.template` alongside them:

```bash
source ./config.sh                        # repo root — WEBAPPS_LOGIN/PWD, Spanner names
cd mcp_server_for_ge
for d in . ge re; do cp "$d/variables.json.template" "$d/variables.json"; done
$EDITOR variables.json ge/variables.json re/variables.json   # GCP_PROJECT, GCP_REGION, SERVICE_ACCOUNT

bash deploy.sh                            # both services, ge/ then re/
# or one at a time:
cd re && bash deploy.sh
```

Deploy Gitea first if you can — both scripts resolve its URL through `services/gitea/gitea-url.sh`
and only fall back to runtime discovery if it is not there yet.
