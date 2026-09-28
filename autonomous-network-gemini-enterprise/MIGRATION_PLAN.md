# Migration Plan — `gnnsandbox` → `autonomous-network` base

Companion to [`REPO_COMPARISON.md`](REPO_COMPARISON.md). That document establishes *what* differs
and *why* this direction; this one is the execution plan.

**Target architecture**

```
cloud-gtm/autonomous-network            ← base: controller, 5G/radio, intent APIs, GNN, ingestion
  + agents-in-agent-runtime/  (7 agents, Agent Runtime / Reasoning Engine)
  + mcp_server_for_ge/{ge,re} (Gemini Enterprise + Agent Runtime tool planes)
  + dashboard-v2/             (aiohttp IAM proxy → Reasoning Engine :streamQuery)
  + lib/src/agent_library/    (shared lib + the only AgentTrace writer)
  – services/supervisor/      (dropped — dashboard-v2 carries this backend)
  – dashboard/                (dropped — Socket.IO, incompatible transport)
  – domains/{core,radio,service}/agents/  (dropped — starter-pack scaffolds)
```

**Why `services/supervisor` and `dashboard/` are dropped rather than kept.** REMOTE's supervisor
discovers agents via `A2ACardResolver(base_url=address).get_agent_card()`
(`host_agent.py:121-131`) — a plain HTTP GET for `/.well-known/agent-card.json`, which expects
Cloud Run A2A services. Our seven agents are Reasoning Engines: they build A2A cards
(`agent_runtime_app.py:44-66`) but expose them through the Reasoning Engine API behind IAM, not at
an HTTP base URL. Keeping REMOTE's dashboard would mean re-deploying all seven as Cloud Run
services and discarding `deploy_agents.py`, the `agents-cli` manifests, and Gemini Enterprise
registration. `dashboard-v2/server.py:548` already calls `reasoningEngines/{id}:streamQuery`.

Dropping them also removes REMOTE's worst security defect for free: the supervisor API key
compiled into a public browser bundle, and the fail-open middleware behind it.

---

## Where this stands — *(2026-08-26)*

Every verification gate is closed, each one against the live `an-gdemo` lab rather than by
inspection.

| Phase | Gate | Status |
|---|---|---|
| 0 | Decisions | ✅ all three decided and applied |
| 1 | Base prepared | ✅ 1.4 — static scan and a full `./install.sh --all` |
| 2 | Shared library and both tool planes | ✅ 2.3 — every tool on live data, all six databases reached |
| 3 | Agents | ✅ 3.2 — read *and* write: chaos injects and removes a fault, designer's CR survives the review chain |
| 4 | Dashboard | ✅ 4.4 — IAP-only, live topology, chat confirmed working in the browser |
| 5 | Cherry-picks | **On hold** — three optional dashboard features, none blocking |
| 6 | Re-add what REMOTE dropped | ✅ all four items |
| 7 | Operational hardening | ✅ 7.1–7.8, every finding fixed |

Two operational findings are recorded but deliberately not chased here: two of the ten traffic
tests sit in a failed phase after a controller restart, and `p1-pe2` measures above its configured
capacity — the latter independently corroborated by the trust agent's own simulation at 102–105%.
The out-of-scope list at the end of this document is unchanged.

---

## Inventory

### The fleet — 7 agents, not 8

| Agent | Tracked files | ADK pin | Notes |
|---|---|---|---|
| `chaos` | 43 | `>=1.15.0,<2.0.0` | Includes Terraform scaffolding |
| `detective` | 36 | `>=1.27.0,<2.0.0` | **This is the former logs agent** — `5121991` rebranded `logs` → `detective` |
| `tester` | 35 | `>=1.15.0,<2.0.0` | Includes Terraform scaffolding |
| `designer` | 17 | `>=1.27.0,<2.0.0` | Most recently worked on — Google Chat, GitOps validation |
| `impact` | 11 | `>=1.27.0,<2.0.0` | Read-only / analytical |
| `supervisor` | 10 | `>=1.27.0,<2.0.0` | Holds `AGENT_RUNTIME_MAP` of peer RE IDs |
| `trust` | 10 | `>=1.27.0,<2.0.0` | Read-only / analytical |

Two things to fix in passing:

- **`AGENTS_SUMMARY.md` is stale.** It lists "Logs (`logsagent`)" as a live agent and omits
  `detective` and `trust` entirely.
- **Six stray untracked directories** — `chaosagent/`, `designeragent/`, `impactagent/`,
  `logsagent/`, `supervisoragent/`, `testeragent/` — contain nothing but
  `app/__pycache__/*.pyc`, left over from the June rename (`fac7c52`). `logsagent/` in particular
  holds a single orphaned `.pyc` and no source. Delete all six; they are not in git.

### MCP servers — two consumers, one tool surface

| | `ge/` | `re/` |
|---|---|---|
| Transport | `streamable-http` (`main.py:39`) | `sse` (`main.py:39`) |
| Consumer | Gemini Enterprise app | Agents on Agent Runtime |
| Auth as found | **none** — `deploy.sh:42` `--allow-unauthenticated` | **none** — same |
| Auth now (0.2) | shared bearer secret; no mutating tools registered | Cloud Run IAM + OIDC; mutating tools registered |

Six of nine tool files are **byte-identical**: `capacity`, `configdrift` (778 lines), `devices`,
`faults`, `logs`, `tests`. Two have drifted and must be reconciled during the port — see Phase 2.

---

## Phase 0 — Decisions before any code moves

**All settled.** 0.1 and 0.2 are decided; 0.3 is the only one still open and it blocks nothing.

### 0.1 Spanner schema — **DECIDED: adopt REMOTE's six databases unchanged**

`gcp/spanner.tpl.yaml` ships as-is. No collapse, no re-partitioning, no table renames on our side.

| Database | Env var | Tables relevant to us |
|---|---|---|
| `transporttopology-db` | `GOOGLE_SPANNER_TRANSPORT_DATABASE` | `PhysicalRouter`, `PhysicalInterface`, `PhysicalLink`, `Interface_Link`, `VRF`, `BGPSession`, `L3VPNService`, `LogicalSubnet`, `Subnet_Association`, `RouterSystemMetrics`, `RouterRoutingSeries` (was `RouterRoutingMetrics`, see 7.5), `ResourcePools`, `NodeEmbedding`, `KgLogEntryNode` |
| `coretopology-db` | `..._CORE_DATABASE` | `FiveGCore`, `NetworkFunction`, `NetworkFunctionMetrics`, `NodeEmbedding`, `KgLogEntryNode` |
| `radiotopology-db` | `..._RADIO_DATABASE` | `CellSite`, `UE`, `CellSiteMetrics`, `NodeEmbedding`, `KgLogEntryNode` |
| `infratopology-db` | `..._INFRA_DATABASE` | `ComputeHost`, `LinuxNetwork`, `ComputeMetrics`, `NodeEmbedding` |
| `servicetopology-db` | `..._SERVICE_DATABASE` | `TrafficFlow`, `TrafficFlowSeries` (was `TrafficFlowMetrics`, see 7.8a), `NodeEmbedding` |
| `agentops-db` | `..._OPS_DATABASE` | `FaultEvent`, `AgentTrace`, `NetworkDescriptor`, `IntentRecords` |

**Consequences accepted by this decision**, carried forward so they are not rediscovered later:

- Cross-domain graph traversal (transport↔core↔radio in one query) is not possible. REMOTE's own
  schema comments at lines 580-590 and 703-707 state this. Any cross-domain correlation becomes an
  application-side join.
- `Device` no longer exists. It is `ComputeHost` in `infratopology-db`. `interface_id`/`vlan` are
  replaced by `extra_interfaces JSON` — a *list* of interface objects, strictly more capable than
  our single scalar pair — and `status` is now `phase`. What is genuinely lost is the
  `Device` → `PhysicalInterface` **join**, because the two tables are now in different databases.
- `NetworkMetrics` no longer exists. It is `RouterSystemMetrics` + `RouterRoutingSeries` in
  `transporttopology-db`, with a different primary key.
- `KgLogEntryNode` exists in **three** databases and `NodeEmbedding` in **five**. Any tool reading
  either must fan out and merge.
- `TrafficFlowSeries` carries `ROW DELETION POLICY (OLDER_THAN(timestamp, INTERVAL 1 DAY))`.
  Flow metrics older than 24h are deleted automatically.
- Graph node IDs change convention: `device:{name}` → `host:{name}`.

**`processingUnits` is not a schema question.** REMOTE sets 100 (`gcp/spanner.tpl.yaml:28`) vs our
400. Instance compute is scalable on a live instance without touching data or DDL, so this needs no
decision now — start at 100 and raise it if query latency warrants.

**No data-migration burden.** Both installers already `kubectl delete` the entire Spanner manifest —
instance included — before recreating (`install.sh:437-499`, `install-new.sh:471-524`), and both set
`--default-backup-schedule-type=NONE`. Confirm nobody is relying on live data, then build clean.

### 0.2 MCP server auth — **DECIDED: `re/` on OIDC, `ge/` on a shared secret** ✅ *(done, `ad73bd4b`)*

`re/` was straightforward and got done in Phase 2.2.d: service-account to service-account, so
REMOTE's OIDC verifier applied directly with the Agent Runtime SA on the allowlist.

`ge/` was originally deferred — "left open for now", on the unresolved question of what identity
Gemini Enterprise presents to an MCP server. **That deferral is closed.** The answer turned out to
be *none*: GE connects as a Tool Data Store and presents no Google ID token, so both
`--no-allow-unauthenticated` and the OIDC path break the integration outright. Follow-up item 1
below is therefore settled as "it cannot", and item 2's shared-secret fallback is what shipped.

Two layers, because the shared secret alone is weaker than what the tool surface warrants:

| Layer | Mechanism | Effect |
|---|---|---|
| Caller must prove *something* | `MCP_SHARED_SECRET` → `SharedSecretTokenVerifier` (`src/utils/auth.py`) | Reaching the URL is no longer the same as calling a tool. `ge/deploy.sh` generates 64 hex chars with `openssl rand`, stores them in Secret Manager, mounts with `--set-secrets`; the operator sets the same value as an `Authorization: Bearer` header on the GE tool data store. Compared with `hmac.compare_digest`; a secret under 32 characters raises at startup, so a placeholder cannot pass for authentication |
| Endpoint has no write surface | `MCP_ENABLE_MUTATING_TOOLS` → `mutating_tool()` (`src/utils/globals.py`) | `deployDescriptors`, `deleteDescriptor`, `create_device`, `delete_device`, `runTest`, `deleteTest` are not registered unless the env var is set. `re/deploy.sh` opts in, behind IAM and OIDC; `ge/deploy.sh` does not. Even a leaked secret exposes reads, not writes |

The decorator registers nothing but leaves the plain function bound to its own name, which
`delete_device` depends on — it calls `deleteDescriptor` in-process, so that must stay callable on
the deployment where it is not exposed.

Cloud Run IAM on `ge/` stays `--allow-unauthenticated`, and that is now a routing fact rather than
a security posture: the service is publicly *routable* and not publicly *usable*. Worth restating
because a future reader grepping for `--allow-unauthenticated` will find it and reasonably assume
the hole is still open.

**Residual risk, stated plainly rather than closed out:** a shared secret identifies the
*deployment*, not a principal, and does not expire — rotation is `gcloud secrets versions add`
followed by a redeploy of both the service and the GE tool data store config. The read surface
(devices, faults, logs, config drift, capacity — real Spanner data) sits behind exactly one static
credential. Acceptable for a lab and a large improvement on nothing; if this environment ever holds
real network data, the right move is ingress restriction to Google front-ends *in addition*.

Nothing downstream depends on this, so Phase 1.4 was never gated on it.

### 0.3 Terraform scaffolding — **DECIDED: dropped** ✅ *(done, `91cbd1d`)*

Removed 42 files: 33 `.tf`/`.tfvars` from `chaos`, `detective`, `tester`, plus 9 orphaned
artifacts under `deployment/terraform/shared/`. Nothing referenced them — the environment is
provisioned by `install.sh` + Config Connector and the agents by `deploy_agents.py`.

---

## Phase 1 — Prepare the base

**Approach decided:** not a fork or a separate repo — a regular branch, `migration/an-base`, inside
`gnnsandbox`. A branch off `main` (rather than `--orphan`) keeps the four keep-list components
present *with their history*, and makes an eventual merge back to `main` an ordinary merge instead
of an unrelated-histories import.

### 1.0 Tree reconciliation ✅ *(done)*

| Commit | What |
|---|---|
| `6c10467` | Import their complete tree — union of both, 1405 files. Nothing removed, so everything stays recoverable from here |
| `985efb0` | Remove our superseded components (248 files): `operator/`→`controller/`, `environment/`→`gcp/`, `gnn/`→`domains/transport/gnn/`, `logservices/`→`services/*`, `tools/`→`services/sharedmcp/`, plus `zinkworks/` and stale root files |
| `e8e2264` | Drop incoming components not carried (187 files): `services/supervisor/`, `dashboard/`, and the three scaffold orchestrators |
| `91cbd1d` | Drop Terraform (decision 0.3) |

696 + 798 → **928 files**. Keep-list verified intact: `agents-in-agent-runtime/` (123 after
Terraform removal), `mcp_server_for_ge/` (47), `dashboard-v2/` (91), `lib/` (11), history preserved.

Two findings worth carrying forward:

- **`.gitignore` was merged, not replaced.** Theirs ignores `config.sh` and has no entry for
  `setenv.sh` or `runtime_variables.json` — both of which hold live credentials and were ignored by
  ours. Taking theirs wholesale would have committed `WEBAPPS_PWD`. Re-check this whenever an
  incoming file replaces one of ours wholesale.
- **`traffic-agent` took theirs.** Its 300-line raw diff is ~95% license-header reformatting; real
  divergence is ~15 lines adding `source_ip` socket binding for `uesimtun` addresses (UERANSIM).
  Strictly ahead of ours.

> **Harvest note.** `services/supervisor/src/tools/` is deleted from the working tree but reachable
> at `git show 6c10467:services/supervisor/src/tools/`. That is the reference implementation for
> the Phase 2.2 six-database rewrite — take it from history.

### 1.0.b Rewire `install.sh` ✅ *(done, `7fa7ba8` + `efcdc3c`)*

`install.sh` rendered and invoked the two things 1.0 deleted and would have failed at `:940`
(`services/supervisor/install.sh not found`). `DeploySupervisor()` is replaced by
`DeployDashboardV2()`, carrying the IAP hardening from the old `install-new.sh:1586-1620`
(`--no-allow-unauthenticated`, `--iap`, `run.invoker` for the IAP service agent, domain-scoped
`iap.httpsResourceAccessor`). `Kill()`, `DisplayDemoInfo()`, the help text and a new `--dashboard`
target were updated to match.

Three things surfaced while doing it that were not in the original plan:

- **The Flutter bundle was never built by the installer.** `dashboard-v2/Dockerfile` does
  `ADD dashboard-v2/build/web/`, and `build/` is an untracked build output. Only the side-door
  `deploy_dashboard_v2.sh` ever ran `flutter build web`, so the image build worked on a machine
  that had built before and failed on a fresh clone. `DeployDashboardV2()` now compiles it.
- **`REASONING_ENGINE_ID` is derived, not configured.** It is the *supervisor* agent's resource
  path, read from `agents-in-agent-runtime/supervisor/deployment_metadata.json`. A `ReadEngineId`
  helper resolves it and the five per-agent IDs; all eleven `--dart-define` names were checked
  against `environment_config.dart:20-55`. This means **the dashboard cannot be deployed before
  the agents** — it will deploy, but with an empty supervisor ID.
- **`GITEA_HOST`, `TRAIN_GNN_URI`, `SERVE_GNN_URI` were referenced but defined nowhere** in the
  incoming tree. Recovered from `install-new.sh:1520-1524`.

Interim shim, since removed in 4.3a: `DeployDashboardV2()` used to set
`GOOGLE_SPANNER_DATABASE="$GOOGLE_SPANNER_TRANSPORT_DATABASE"` because
`dashboard-v2/tools/{metrics,topology}.py` still expected a single database.

### 1.1 Security fixes (non-negotiable, pre-deploy) ✅ *(done, `efcdc3c`)*

| Fix | Location | Status |
|---|---|---|
| Remove `roles/resourcemanager.projectIamAdmin` from the shared SA | `install.sh:353` | ✅ |
| Close the public `intentserver` — it exposes `POST`/`PATCH`/`DELETE /intent` that provisions and tears down L3VPN | `domains/transport/services/intentserver/install.sh:135` → `--no-allow-unauthenticated`; handlers at `src/main.py:274`, `:577`, `:614` | ✅ |
| Update `.gcloudignore` — it still ignored `environment/`/`operator/` and failed to exclude `config.sh` and `runtime_variables.json` from build context | `.gcloudignore` | ✅ |
| Remove the fail-open branch — a missing env var currently disables auth entirely | `services/supervisor/src/middleware/auth.py:63-66` | n/a — service dropped in 1.0, code reachable at `git show 6c10467:` if ever revived |

Notes from doing the work:

- The intentserver has **no in-repo programmatic callers**, so closing it affects only manual
  invocation. `install.sh` now prints the identity-token call and the `run.invoker` grant.
- REMOTE's own docs contradicted each other here: `docs/IAM_STRATEGY.md:83` and its "Pattern 3:
  Public API with Internal Authentication" described the open deployment as intentional, while
  `docs/SECURITY.md:420` listed it as a Medium risk. Both reconciled to the authenticated design.
- `domains/transport/services/intentserver/.api_url` was a **tracked file containing a live
  deployed URL**. Not a secret, and the endpoint is now IAM-gated, but it should not be in git.
  ✅ Fixed while bringing the branch up on `an-gdemo`, once the file dirtied the tree with the new
  project's URL: untracked, and `domains/transport/services/*/.api_url` added to `.gitignore`.
  Nothing reads the file back — each `install.sh` writes it and `uninstall.sh` deletes it.

While reviewing IAM: REMOTE grants 17 roles to one shared SA including `roles/editor`, which is
still far broader than necessary and would readmit most of what removing `projectIamAdmin` bought.
Trimming it is out of scope for this migration but should be tracked.

### 1.2 Deploy the Spanner schema unchanged ✅ *(verified live, 2026-08-26)*

`gcp/spanner.tpl.yaml` ships as authored. Populate the six env vars from `config.sh.template:58-63`.
No work here beyond confirming all six databases create cleanly and the five property graphs build —
both confirmed on `an-gdemo`; the listing is in 1.4, which is the same run.

One caveat the deploy does not surface: 7.1 had to *append* `ALTER TABLE` statements rather than
edit `CREATE TABLE ComputeHost`, because Config Connector's `ddl` list is append-only. "Ships as
authored" holds for a fresh install; on an existing database only appended statements take effect.

### 1.3 Remove what we are not carrying ✅ *(done, `e8e2264` + `8f823f85`)*

- `domains/{core,radio,service}/agents/` — three starter-pack scaffolds whose `app/agent.py` were
  byte-identical (`md5 a56ef353…`) and contained `get_weather()`. The packages went in `e8e2264`;
  `8f823f85` removed the orphaned remainder (three identical `deploy_agents.py` plus wrappers whose
  target directories no longer existed). `deploy_agents.py` discovers agents by listing
  subdirectories of its own location, so those wrappers would have granted IAM and deployed
  nothing. `install.sh`'s deploy loops were already narrowed to `transport`, so nothing live
  invoked them.
- `dashboard/` and `services/supervisor/` — removed from git in `985efb0`. `dashboard/` survives as
  an **ignored on-disk leftover** with 0 tracked files; harmless, but do not be misled by `ls`.

> ⚠️ **Do not delete `services/supervisor/src/tools/` before Phase 2.2 harvests it.** Those four
> files are the reference implementation for the six-database query rewrite. The service is
> discarded; its tool code is not.

### 1.4 Verification gate — **passed, static and live ✅**

**Static, done (`8f823f85`).** Everything checkable without a project has been checked:

| Check | Result |
|---|---|
| All `.tpl.yaml` files `install.sh` renders exist | ✅ 9/9 |
| All sub-installers `install.sh` invokes exist | ✅ 9/9 |
| No variable referenced without being defined | ✅ (`install.sh` and all 9 sub-installers) |
| `bash -n` on every touched script | ✅ |

That scan is what surfaced the always-true Cloud Shell guard and the missing `GOOGLE_ORG_NAME`;
it is worth re-running after any installer change. It is the same class of defect as the
`GITEA_HOST` / `TRAIN_GNN_URI` bugs found in 1.0.b, which is why it was made systematic.

**Live — passed on `an-gdemo` ✅** *(2026-08-26)*. `./install.sh --all` completes and the controller
reconciles. Spanner has all six databases and all five property graphs (this is also 1.2's gate —
the two are the same run):

```
agentops-db  coretopology-db  infratopology-db  radiotopology-db  servicetopology-db  transporttopology-db
transporttopology-db  ['transportGraph']      radiotopology-db    ['radioGraph']
infratopology-db      ['infraGraph']          servicetopology-db  ['serviceGraph']
coretopology-db       ['coreGraph']           agentops-db         (none — ops tables, no graph by design)
```

The public-service scan returns **two** services, not one, and the second is a later deliberate
exception the original wording did not anticipate:

| Public service | Why it is accepted |
|---|---|
| `mcp-networktools-ge` | 0.2 — Gemini Enterprise cannot present an ID token, so it is authenticated in-app by a shared bearer secret |
| `gitea-webhook` | 6.1 — Gitea's webhook sender posts plain HTTP and cannot mint an OIDC token, so IAM made the path permanently dark. The HMAC signature is the gate; the residual is accepted in `docs/SECURITY.md` |

Every other service, including the dashboard, rejects anonymous callers. Re-run with:

```bash
gcloud run services list --format='value(metadata.name)' | while read s; do
  gcloud run services get-iam-policy "$s" --region="$GOOGLE_REGION" \
    --flatten=bindings --format='value(bindings.members)' | grep -q allUsers \
    && echo "PUBLIC: $s"
done
```

Known first-run ordering constraint from 1.0.b: the dashboard reads the supervisor's engine ID
from `deployment_metadata.json`, so on a clean project it must be deployed **after** the agents.
`--all` already orders it that way; a standalone `--dashboard` before the agents exist will warn
and deploy a dashboard that cannot reach the supervisor.

---

## Phase 2 — Foundation: shared library and tool planes

Everything in Phase 3 depends on this. Do not start agents until Phase 2's gate passes.

### 2.1 Port `lib/src/agent_library/` ✅ *(done, `a6b2528b`)*

Bring it across **as a package**, not dissolved. REMOTE inlined it into
`services/sharedmcp/src/utils/` and `services/supervisor/src/utils/`, and those two copies have
**already drifted by 11 hunks** — vendoring rot in progress. `k8s.py` is byte-identical to ours,
so the merge is mechanical.

Two things this unlocks:

- `deploy_agents.py` vendors it at deploy time; agents will not deploy without it.
- `trace/trace_plugin.py` is **the only `AgentTrace` writer in either tree**. REMOTE has trace
  readers on both sides and nothing feeding them — their trace UI has never displayed data. Porting
  this makes it work for the first time.

Back-port REMOTE's `gitea_utils.py` retry hardening for transient Gitea 404s while merging.

The package was already intact here, so the work was the hardening. Their **two copies have
drifted from each other** — the retry logic exists only in `services/supervisor`, the
nested-path `get_git_file` only in `services/sharedmcp` — so both halves were merged rather
than picking one. `git_file_exists` had the same root-listing flaw as `get_git_file` and was
fixed with it: a nested file that already existed looked absent, so `commit_git_file` took the
create path and the write failed. Their `get_gitea_url` (reads `GITEA_URL` from the
environment) was **not** taken — ours reads the `Gitea` custom resource, which Phase 6 restores.

> ⚠️ **Follow-up: a third copy exists.** `mcp_server_for_ge/{re,ge}/src/utils/git_helpers.py`
> is a slimmer reimplementation of the same helpers, kept separate because the tool servers
> deploy with `gcloud run deploy --source=<twin dir>` and `lib/` is outside that build context.
> It received the same fixes by hand. Consolidating means moving the twins to a repo-root build
> like `dashboard-v2/Dockerfile`, which `ADD lib/`s and `pip install`s the package. Until then
> the two files must be changed together.

### 2.2 Port both MCP servers

Keep the two-server split — it is a transport requirement, not duplication.

**Reconcile the two drifted files first.** `ge/src/tools/topology.py` advertises the same four tool
names as `re/`, but three return literals:

```python
def query_link_utilization(interface_name) -> str:
    """Retrieve real-time network throughput utilization metrics for specific router interfaces."""
    return json.dumps({"interface": interface_name, "status": "UP",
                       "utilization_pct": 34.2, "input_bps": 1245000, ...})
```

No Spanner call. `query_ospf_paths` always returns `[source, "p1", "p2", destination]`;
`query_affected_flows` always returns one hardcoded VoIP flow. `simulations.py` is the same — both
tools are `json.dumps` of fixtures (52 lines vs `re/`'s 190).

`re/` has the real implementations: 391 lines with BFS shortest-path and Spanner GQL. Signatures
are identical and `ge/` demonstrably has working Spanner access — the six byte-identical files
include `configdrift` and `capacity`, which both query it.

✅ **Already done.** Verified rather than assumed: `topology.py` and `simulations.py` are now
byte-identical between `re/` and `ge/`, and the `34.2` fixture literal quoted above appears
nowhere in the tree.

⚠️ **But that check was too narrow.** "The `34.2` literal is gone" and "the two twins agree" are
both true and neither one means the tools return live data — the twins agreed *on the same
fixtures*. Four more fabricators survived the drift reconciliation and were only caught when the
detective agent presented one of them to a human as a live incident. See 2.2.a-bis.

#### 2.2.a-bis Ground the remaining fabricating tools ✅ *(done)*

| Tool | What it returned | What it returns now |
|---|---|---|
| `faults.query_fault_events` | A hardcoded `mock_faults` list — `chaos-inj-8831` INTERFACE_FLAP on `pe2`, `chaos-inj-8810` PACKET_LOSS on `p1`. Detective surfaced these as a live incident on an empty project | Real `FaultEvent` rows from `agentops-db`, with ACTIVE/RESOLVED derived from the SCD-2 `valid_end_ts` |
| `capacity.identify_bottlenecks` | `curr_bps = cap_bps * 0.78  # Simulated 78% peak traffic` — every link reported exactly 78% | Measured peak throughput, by delegating to `query_link_utilization` |
| `capacity.recommend_capacity_upgrades` | An upgrade for `link-pe1-p1`, a link name it invented | Tier sizing computed from the measured peak and the link's real capacity |
| `simulations.simulate_remediation_impact` | `sim-gnn-9921`, `predicted_post_utilization_pct: 38.4`, `stability_confidence_score: 0.94`, behind a docstring claiming "Predictive GNN simulation" | Observed capacity/throughput plus an arithmetic forecast, with a `basis` field naming the source of every number and stating that no predictive model is involved. The invented confidence score and latency figure are gone |

The two `capacity.py` tools now call `query_link_utilization` rather than recomputing, so the one
implementation that reads `node_network_{transmit,receive}_bytes_total` out of `RouterSystemMetrics`
is the only place utilization is derived — the two tools can no longer disagree. The shared
`CAPACITY_TIERS` table lives in `topology.py` so `capacity.py` and `simulations.py` can both size an
upgrade without importing each other.

Each tool now returns an **explanatory sentence** rather than `[]` or a plausible-looking zero when
there is no data, so "nothing is deployed" cannot be misread as "nothing is wrong".

**This uncovered a controller bug.** `controller/src/networkfailure/lifecycle.py:25` imported
`close_fault_event` and never called it, so `valid_end_ts` stayed NULL on every fault ever
injected — the exact column the new `query_fault_events` reads. Every long-removed fault would
have read as ACTIVE. Fixed with a second `@kopf.on.delete` handler rather than a step appended to
`delete_network_failure_handler`, because that handler returns early on three separate restoration
paths; kopf runs both handlers for the same event, keyed by function name.

**The last fabricator, now closed** (`16fda607`). `topology.py:query_affected_flows` returned one
hardcoded `flow-voip-102` — pe1 to pe2, 10mbps — whatever routers you asked about, and whether or
not any traffic existed. It now resolves flows end to end from live state: `TrafficTest` CRs supply
the flows, each device is mapped to the CE router terminating its LAN by joining on the
`LinuxNetwork` name (the same field the controller uses to wire the container up, so the join is
exact rather than inferred from addresses), and CE-to-CE goes through `query_ospf_paths` — the same
shortest-path walk the simulation tools already use. A flow counts as affected when a named router
sits anywhere on that path, transit included.

This was the one tool that reads Kubernetes rather than Spanner, which is why it lagged the other
four. Only `Running` tests count as active; the remaining phases are reported as a count, so an
empty answer is distinguishable from an unexamined one.

#### 2.2.b Introduce a database-routing layer ✅ *(done, `9dbacd74`)*

Our tools assume a single database. `re/` creates a Spanner client in **two** places —
`src/utils/database.py:31-33` (a shared singleton) and `src/tools/configdrift.py:44-46` (its own,
bypassing the singleton). Both hardcode one `SPANNER_DATABASE`.

Replace the singleton with a factory keyed by domain, backed by the six env vars, and fix
`configdrift.py` to use it rather than building its own client.

**There is a reference implementation — harvest it.** `services/supervisor/src/tools/` already
contains our tools re-targeted at the six-database schema, because those files descend from the
same `networkagents/supervisor` code ours came from. `topology.py:106-112` is literally the
routing switch:

```python
db_name = os.getenv('GOOGLE_SPANNER_TRANSPORT_DATABASE', 'transporttopology-db')
db_name = os.getenv('GOOGLE_SPANNER_INFRA_DATABASE',     'infratopology-db')
db_name = os.getenv('GOOGLE_SPANNER_SERVICE_DATABASE',   'servicetopology-db')
db_name = os.getenv('GOOGLE_SPANNER_OPS_DATABASE',       'agentops-db')
```

`metrics.py:86,891-902` shows the transport + service dual-connection pattern; `logs.py` and
`gnn_embeddings.py` cover the other two. The shared lineage is visible in the function names —
their `_parse_bandwidth_bps` is our `calculate_bandwidth_bps`, and `fetch_log_entries` exists on
both sides.

A second precedent: `domains/transport/gnn/src/gnn/data/spanner.py:1266-1280` does a three-database
parallel fetch (transport + service + infra) for GNN ingest.

**This materially reduces the Phase 2 estimate.** We are adapting a working rewrite, not authoring
one.

#### 2.2.c The four query rewrites ✅ *(done, `9dbacd74`)*

Everything else is a config change — 6 of our 10 core topology tables live wholly in
`transporttopology-db`, so those tools just point at the right env var. These four are real work:

| # | Change | Affected | Approach |
|---|---|---|---|
| 1 | `Device` → `ComputeHost`, different DB, `interface_id`/`vlan` → `extra_interfaces JSON`, `status`→`phase` | **the join** at `simulations.py:68` and `configdrift.py:406`. ~~`devices.py`~~ — *corrected during the port: `devices.py` reads Kubernetes `Device` CRs, not the Spanner `Device` table, and the `Device` CRD still exists, so it needed no schema work* | Re-point at `infratopology-db` and copy their column handling verbatim (`services/supervisor/src/tools/topology.py:596-647`, incl. the `phase`→`status` remap for UI compat). The two `Device`→`PhysicalInterface` joins now cross a DB boundary and have **no reference solution** — see 2.2.f |
| 2 | `NetworkMetrics` → `RouterSystemMetrics` + `RouterRoutingMetrics`, new PK | `capacity.py`, `topology.py::query_link_utilization` | Split each read; decide per query which half it needs |
| 3 | `KgLogEntryNode` in 3 databases | `logs.py` (157 lines) | Fan out across transport/core/radio, merge and sort by timestamp |
| 4 | `NodeEmbedding` in 5 databases | GNN embedding lookups | Fan out, or scope to the caller's domain where that is sufficient |

`configdrift.py` (778 lines, our largest tool) is transport-only apart from the one join at `:406` —
expect a connection change rather than a rewrite. Note that its other six `interface_id` references
are `Interface_Link.interface_id`, an unrelated column wholly inside transport. Do not count them.

#### 2.2.f The one open question — the host→interface join ✅ *(resolved: option 1, `9dbacd74`)*

Two queries join `Device` to `PhysicalInterface`, and those tables now sit in different databases:

- `re/src/tools/simulations.py:68` — `JOIN PhysicalInterface pi ON pi.id = d.interface_id`
- `re/src/tools/configdrift.py:406` — "which VMs hang off this router"

REMOTE did not solve this; they dropped the capability. Their `ComputeHost` query
(`topology.py:596-647`) returns the host plus its `extra_interfaces` blob and stops. So there is no
implementation to copy here — this is the only part of the six-database decision that needs a
design choice rather than a port:

1. Two-step: read `extra_interfaces` from `infratopology-db`, extract interface name or IP, then
   look up `PhysicalInterface` in `transporttopology-db`. Join in Python.
2. Decide these two tools do not need the traversal.

**Resolved: option 1, but via a different column.** `extra_interfaces` turned out to be the
wrong side of the relationship — it lists a host's own extra NICs, not what it is attached to.
`LinuxNetwork.connected_routers` carries `[{router_name, interface, ip_address}]` and lives in
`infratopology-db` alongside `ComputeHost`, so host→router resolves with **no cross-database
query at all**. `src/utils/hosts.py` implements both directions (`hosts_on_router`,
`routers_for_hosts`); `simulations.py` and `configdrift.py` call it instead of joining.

#### 2.2.d Auth ✅ *(done, `1ebb76ab`; `ge/` closed in `ad73bd4b`)*

Apply REMOTE's OIDC verifier (`sharedmcp/src/utils/auth.py`) to `re/`, allowlisting the Agent
Runtime SA. `ge/` originally shipped unauthenticated and is no longer: see 0.2 for the
shared-secret verifier and the `MCP_ENABLE_MUTATING_TOOLS` gate that replaced that deferral.

#### 2.2.e Fold in REMOTE's tools ✅ *(done, `e7665411`)*

`services/sharedmcp` has **zero Spanner access** — grep returns nothing. Their MCP plane is
CRD and Gitea tools only; all their Spanner querying lives in `services/supervisor`. So our two
servers are purely additive to theirs, with no overlapping implementation to reconcile.

Take from `sharedmcp`: transport/core/radio CRD discovery tools, and the Gitea deploy verbs.

Landed as `src/tools/crds.py` and `src/tools/descriptors.py`, plus real
`create_device`/`delete_device` and a new `searchDevices` in `devices.py`. Two defects were
fixed rather than carried across: their CRD listers return an implicit `None` on any non-404
error, which an agent cannot distinguish from an empty cluster; and their `deployDescriptors`
is annotated `readOnlyHint=True` while it commits to git.

One tool was flagged as not yet ported: `getTransportDesignDoc` fetches `knowledge/designdoc.md`,
and `utils/git_helpers.py::get_git_file` used to match on basename only (`c.name == filename`), so
a nested path could not be fetched.

✅ **Already done** — verified, not assumed. The tool is at `src/tools/crds.py:150`, and
`git_helpers.py:50-56` handles nested paths. Note that with 6.3 the document is also published
flat as `designdoc.md` in the `networkdesign` repo, which is what the *other* three readers
(`getDesignDoc` in the designer agent, and both `DESIGN_REPO` lookups) fetch; the nested
`knowledge/designdoc.md` path is the per-domain knowledge repo `seed.sh` builds from
`domains/transport/knowledge/`. Both resolve to the same source file.

### 2.3 Verification gate — **passed ✅**

Both servers deploy. `re/` rejects unauthenticated calls and accepts the Agent Runtime SA. Every
tool returns live data — specifically, `query_link_utilization` on `ge/` no longer returns 34.2%.

"Returns live data" means *reaches the datastore*, not *returns rows* — on a project with no
network deployed the correct answer is the tool's no-data sentence, and that is what was checked.
Against `an-gdemo`: `query_fault_events` → "No fault injection events are recorded…";
`identify_bottlenecks` and `recommend_capacity_upgrades` → "No physical links with utilization
data…"; `simulate_remediation_impact('upgrade capacity', 'link-pe1-p1')` → rejects `link-pe1-p1`
as not a current `PhysicalLink` or `PhysicalRouter`, which is the same name the old fixture used to
invent.

**Positive case, on a live network ✅** *(2026-08-26)*. The no-data sentences above prove the tools
reach the datastore; they do not prove the numbers are real. With `transport/default-network`
deployed and all ten traffic tests running (see 2.3.a), every one of the five rewritten tools was
re-run and returns measured values:

| Tool | Measured result |
|---|---|
| `query_link_utilization` | `pe2-ce1-hub` 99.74 Mb of 100 Mb (99.74%), `p1-p2` 71.60 Mb of 1000 Mb (7.16%), `p1-rr1` 0.00 Mb |
| `identify_bottlenecks` | Ranks `pe2-ce1-hub` first, then `p1-pe2` 82.72%, `p3-pe2` 79.11% — no two links share the old fabricated 78% |
| `recommend_capacity_upgrades` | 100 Mbit → 1gbit on `pe2-ce1-hub`, projecting the same measured peak at 9.97%, with the arithmetic spelled out in `rationale` |
| `simulate_remediation_impact` | Reports observed 99,737,481 bps against a 100,000,000 bps link and names its `basis`; no invented confidence score |
| `query_affected_flows` | 11 real flows across `p1` with correct end-to-end paths; 0 across `rr1` |

Two independent cross-checks fell out of this. `pe2-ce1-hub` at 99.74% is exactly the hub-ingress
congestion point the BLUE hub-and-spoke test was designed to create, so the measurement matches the
intent of the fixture. And `rr1` carrying no flows agrees with `query_link_utilization` measuring
`p1-rr1` at 0 bps — the route reflectors are iBGP-only, and two tools reading different tables say
so independently.

Schema-specific checks, since 2.2 is where the six-database decision lands:
- Each of the six env vars is set and each is actually reached by at least one tool. **All six ✅**
  *(2026-08-26)*. `ops` (`agentops-db`) was reached by nothing until `query_fault_events` was
  grounded; `service` (`servicetopology-db`) was the last one, and 2.3.b below closes it.
- `configdrift.py` no longer builds its own client.
- A `KgLogEntryNode` query returns entries from more than one domain in one call.
- A `devices.py` call returns `ComputeHost` rows with `phase` populated, and every caller that
  previously read `interface_id`/`vlan` either has a new path or has been explicitly retired.

#### 2.3.a The network the gate was measured against ✅ *(done)*

`transport/default-network`, brought up on `an-gdemo` through the GitOps path rather than
`kubectl apply`, so the commit → Config Sync → controller loop was exercised as well as the
manifests. Staged deliberately, verifying each layer before adding the next: infrastructure (18
routers, 8 devices, as Docker containers on `networkvm`), then the underlay (flat OSPF area 0,
MPLS/LDP, iBGP through two RRs), then both L3VPNs (BLUE hub-and-spoke via Nottingham, RED
any-to-any), then the ten traffic tests.

Verified at the data plane, not just the control plane: OSPF Full on every backbone adjacency, LDP
OPERATIONAL, VPNv4 exchanging 8 sent / 32 received per PE, and four ping tests — including a
*negative* one, BLUE spoke to RED, which must fail and does. Spoke-to-spoke is also correctly
unreachable, because spoke VRFs import only the hub's route target. Note the VRF names are
`BLUE_SPOKE` and `RED_MESH`, not `BLUE`/`RED`.

Two controller bugs surfaced, both fixed in `3a4cefa3`:

**Every GitOps commit re-rendered the whole transport stack.** Config Sync stamps the current commit
SHA as an annotation onto every object it manages, on every commit, which produces a metadata-only
update event for resources whose spec never changed. `update_vyosinfrastructure`,
`update_vyosunderlay` and `update_vyosl3vpn` all reacted to it. For the infrastructure handler that
is actively destructive once VPNs exist: re-rendering `l3vpn-infra` rewrites each router's interface
configuration from the infrastructure spec, dropping the VRF bindings `VyOSL3VPN` layered on top, so
every PE-CE eBGP session goes down. Committing an unrelated file would have taken both VPNs down.
All three now filter the diff the way `update_vyosrouter` already did. Confirmed on the traffic-test
commit: no re-render, and the PE-CE sessions on `pe1` showed 12h43m uptime afterwards — never
bounced.

**`VyOSInfrastructure` could stick in `Creating` forever.** `create_vyosinfrastructure`
unconditionally sets `Creating`, which is only right while children are converging; the sole route
back to `Ready` is the `on.field` watchers on child `status.phase`, and those return early when
`old == new`. A reconcile over already-Running children therefore left the parent stuck, with the
underlay and both VPNs stalled behind it. It now settles the phase against the live children at the
end of the handler.

One fixture problem, worth recording because it is not obvious from the CR: `ce1-spoke` came up
`Failed` with a status message truncated to Ansible deprecation warnings, so `_is_transient_error()`
matched nothing and classified it permanent. The container was running but had no addresses on any
interface — created, never configured. Deleting the CR did not make the parent recreate it. The fix
was committing a `vyos.google.dev/reconcile-trigger` annotation onto the `VyOSInfrastructure` CR,
which is safe only *before* VPNs exist, for the reason above.

**⚠️ Gitea has no durable storage.** The Cloud Run service runs with no volume mount, so its SQLite
database and every repository live on the container's ephemeral filesystem. After an idle night the
instance was recycled and all three repos — `network`, `root-repo`, `networkdesign` — were gone,
both Config Sync reconcilers were crash-looping on a 404, and the trust webhook and API token went
with the database. The deployed network survived only because the controller had already reconciled
it into the cluster; the GitOps source of truth had not. Restored from a local clone plus
`services/gitea/seed.sh`, and the hook re-registered with `./install.sh --webhook -n`. That was a
restore, not a fix. **Closed by 7.2** ✅ — Gitea now runs on GKE with `/data` on a `Retain` PVC and
a stable reserved address, and the durability was proven by deleting the pod and confirming all
seven repositories, both refs and the admin user came back intact.

**Two install scripts hang on a TTY prompt.** `controller/install.sh:41` and
`services/gitea-webhook/install.sh:64` both `read -p` to ask whether to rebuild an existing image,
which fails with no terminal and exits with no diagnostic. Pass `-n` to the root `install.sh`; the
`NO_FLAG` environment variable does *not* work, because the root script hardcodes `NO_FLAG="n"` at
line 1429 and exports it over anything the caller set.

#### 2.3.b The sixth database ✅ *(done — 2026-08-26)*

`servicetopology-db` was the last database no agent tool read. It was not empty: `TrafficFlow` held
66 rows (10 current) and `TrafficFlowMetrics` **132,602**, all of it measured per-flow telemetry
from the traffic agents. The dashboard read it (`dashboard-v2/tools/metrics.py`, `TRAFFIC_DOMAIN =
'service'`); the agents did not. `query_affected_flows` answered by listing `TrafficTest` CRs out of
Kubernetes and walking the transport graph — correct as far as it went, but it answers from
*intent*. An agent could say a flow had asked for 35Mbps; it could not say the flow was getting 4.9.

**The two tables do not share an id space, and that is the whole of the difficulty.** The
controller writes `TrafficFlow.id = "flow:<test-name>"`. The metrics collector writes
`TrafficFlowMetrics.flow_id = "<test-name>_<source-device>"`, plus a `_rev` variant for the reverse
leg of a bidirectional test — one series per direction per source agent, taken straight from the
Prometheus label the traffic agent exports. So one flow row corresponds to between one and 2N metric
series, and nothing joins them.

Three consequences, all now fixed:

| | Before | After |
|---|---|---|
| `FlowHasMetrics_Edge` | `ON f.id = m.flow_id` — **0 rows**, for every flow, since the schema was written. `serviceGraph`'s only real edge was dead | Joins on `TrafficFlow.name` plus the collector's suffix: **142,802 edge rows** over all 10 flows and 21 series |
| `query_affected_flows` | Sourced from Kubernetes; synthesised `flow_id` as `<test>_<source>` only when a test had several sources, so single-source flows carried an id matching no series | Sourced from `TrafficFlow`, id always `<test>_<source>`, each flow carrying a `measured` block |
| Measured telemetry | Reachable by no agent | New `query_flow_metrics` tool: per-direction throughput, loss, latency, jitter |

Splitting the metric id on its first underscore is exact rather than a heuristic: a test name is a
Kubernetes object name and RFC 1123 forbids `_` in one, so the first underscore is always the
boundary the collector inserted. The bridge lives in one place, `utils/flows.py`, in both MCP twins.

**A trap worth recording.** A traffic agent exports the full metric set for every series it owns,
including its send-only ones — and a send-only series reports `packet_loss_pct = 100`, because it
received nothing back. It is measuring nothing, not measuring total loss. Summarising a flow by the
worst reading across its directions therefore marked every healthy bidirectional flow as 100% lost.
`summarize_measurement` now reads loss, latency and jitter only from directions with
`bytes_received_total > 0`, and reports how many those were.

**And the tools were unreachable a second way.** `query_affected_flows` appears in no agent's ADK
`tool_filter`, so no agent could call it however well it worked — while `impactagent.md` documented
it as tool 4 and the trust agent's prompt delegates "what traffic flows through link X?" to exactly
that agent. The impact agent's filter was also missing `identify_bottlenecks` and
`recommend_capacity_upgrades`, both of which its own runtime prompt instructs it to use. `impact`
and `detective` now carry the flow tools, and `impactagent.md` says which of its documented tools
are actually wired.

Verified end to end through the deployed `ge` twin, not just in-process: `query_affected_flows("pe1")`
returns 3 flows with real paths and live readings (35Mbps requested → 4.87 measured, 0% loss), and
`query_flow_metrics("d3-hub-bidir-tcp")` returns both legs with the forward one sending 3.44Mbps and
the reverse receiving 11.91.

Then verified a level up, against the deployed agents themselves over `:streamQuery` — a tool that
works is not the same as an agent that reaches for it. Asked *"list the live user traffic flows
crossing router pe1 and, for each, the measured throughput versus the requested bandwidth"*, both
`impact` and `detective` call `query_affected_flows(router_names="pe1")`, follow it unprompted with
`query_flow_metrics` over the three flows it returned, and answer per direction — `d1-red-d2-red-bidir`
reported as 35Mbps requested / 14.56 measured forward, 45 / 18.37 reverse. The call shape is
`classMethod: "streaming_agent_run_with_events"` with the message JSON-encoded into
`input.request_json`; the plain `stream_query` class method returns an empty 200 on these
deployments.

Eleven scenario docs called `query_affected_flows("p1,p2", "running")` — a second argument the tool
has never had. Corrected while here.

The `CREATE OR REPLACE VIEW` is **appended** to `gcp/spanner.tpl.yaml`, not edited in place — see
1.2 on Config Connector's append-only `ddl`. Spanner accepted the replacement without disturbing
`serviceGraph`, which references the view.

The three schema-specific bullets at the top of this gate close as follows. `configdrift.py` reads
through the shared `utils.database.get_database`, with no client of its own. `KgLogEntryNode` is not
one table any more — transport, core and radio each hold a copy and Spanner cannot `UNION` across
databases, so `logs.py` fans out per domain and merges in Python, skipping a domain that fails
rather than failing the query. `hosts.py` selects `phase` from `ComputeHost` and surfaces it as
`status`; the callers that read the retired `interface_id`/`vlan` were rewritten to walk
`network_name` instead (see 7.1, which is where the last consumer of that path was fixed).

---

## Phase 3 — Agents

**The ADK 1.x → 2.x migration is the critical path of this entire plan.** REMOTE is on
`google-adk >=2.5,<3` (resolved 2.7.0) and `a2a-sdk >=1.0,<2`; we are on `>=1.27,<2` (1.34.1) and
`a2a-sdk >=0.3.22`. `agents-cli` goes 0.3.0 → 1.3.1.

Port **one agent at a time**, in this order:

| # | Agent | Why here |
|---|---|---|
| 1 | `trust` or `impact` | 10-11 files, read-only, no mutation path. Cheapest way to discover what ADK 2.x actually breaks before committing to six more |
| 2 | `detective` | Read-mostly; exercises the MCP tool plane end to end |
| 3 | `chaos` | On the older 1.15 pin — more migration surface. Mutates cluster state via `NetworkFailure` CRs |
| 4 | `tester` | Also 1.15. Depends on the Go traffic-agent, which is near-identical on both sides |
| 5 | `designer` | Most recent work. Carries Google Chat notifications (315 lines), live-cluster schema pre-validation, GitOps commit path. Highest regression risk |
| 6 | `supervisor` | **Must be last** — it holds `AGENT_RUNTIME_MAP`, a map of peer Reasoning Engine IDs. It cannot be wired until its peers have IDs |

Per agent: bump ADK, resolve breaking changes, re-point tool URLs at the Phase 2 servers, deploy
via `deploy_agents.py`, record the RE ID, smoke-test through the Gemini Enterprise surface.

**Do not skip the `deployment_metadata.json` discipline.** `fac7c52`'s commit message notes it
exists specifically to preserve existing Reasoning Engine IDs and avoid duplicate resource
creation. Fresh IDs on every deploy will silently orphan engines.

#### 3.0 Packaging pre-work ✅ *(done — no GCP needed, and it unblocks two agents)*

Three packaging problems, all of which would have surfaced as confusing deploy failures partway
through the per-agent port.

**`chaos` and `tester` could not have deployed at all.** Both `uv.lock` files resolved every
package against the Google Airlock registry
(`us-python.pkg.dev/artifact-foundry-prod/ah-3p-staging-python`) — 1786 references in `tester`
alone. CLAUDE.md is explicit that this fails during Cloud Run / Vertex AI builds because Airlock
requires authentication the build has no credentials for. Both re-locked against public PyPI with
**zero `version =` changes**, so the entire diff is registry URLs; nothing was upgraded or
downgraded.

**Nothing stopped it recurring.** Seven of eight agent directories, plus
`domains/transport/services/topologyserver`, had no `uv.toml`, so a bare `uv lock` on a Google
workstation re-poisons the lock from system config. `deploy_agents.py` passes
`--index https://pypi.org/simple` to `uv sync`, which papers over a poisoned lock at *sync* time
but does nothing at *lock* time — which is exactly how these two drifted. All eight now carry the
same `uv.toml` as `services/sharedmcp`.

**`requests` and `google-auth` were undeclared direct imports.** `deploy_agents.py:251-260`
vendors `lib/src/agent_library` into `app/`, so `lib/pyproject.toml` is never resolved at deploy
time and its dependency list has no effect on any agent — the library's own imports have to be
satisfied by the *agent's* `pyproject.toml`. After 6.2 both `agent_library.gitea_utils` and
`designer/app/app_utils/git.py` import `requests` and `google.auth` directly. They resolve today
only transitively through `py-gitea` and `google-cloud-*`; now declared in `chaos` and `designer`.
Note that `designer/app/app_utils/.requirements.txt` is a dotfile nothing reads — a stale artifact,
not the effective spec.

#### 3.1 Gemini Enterprise registration ✅ *(done)*

Deploying to Agent Runtime only creates the Reasoning Engine; the agent is invisible until it is
also registered against a Gemini Enterprise app. Set `GEMINI_APP_ID` in
`runtime_variables.json` to the full resource path — `projects/{project_number}/locations/global/
collections/default_collection/engines/{engine_id}` — because `deploy_agents.py` splits it on `/`
and indexes fixed positions; a bare app name silently produces the wrong project. For `an-gdemo`
the engine id is `an-gdemo`, so the path is
`projects/809342844318/locations/global/collections/default_collection/engines/an-gdemo`.

`python3 deploy_agents.py -r` registers without redeploying, reading each
`deployment_metadata.json` for its `remote_agent_runtime_id`. All seven register and carry the
custom icon; the app then lists eight agents (the seven plus the built-in Deep Research).

**The agent icon was a dangling reference to a deleted bucket.** Every `publish_config.json`, and
`designer/app/app_utils/notification.py`'s Chat card header, pointed at
`https://storage.googleapis.com/gnn-telco-tmp/telco_icon.jpg`. That bucket returns `NoSuchBucket`
— it was deleted, and the GCS namespace is global, so it is gone for everyone. The image was never
in the repo (`git log -S telco_icon` only ever shows code), so re-hosting alone would leave the
next clean project in the same position. Instead: the asset lives at
`agents-in-agent-runtime/assets/telco_icon.jpg`, the URL is parameterised on `${GCP_PROJECT}`, and
`deploy_agents.py --register` creates `{project}-agent-assets` and uploads it. Bucket creation
passes `--no-public-access-prevention` and binds `allUsers:roles/storage.objectViewer` **only on
buckets it just created**, so it never widens access on one somebody else locked down. Every
failure in that path warns and continues — a missing icon must not fail a deploy. Expect a few
seconds of 403 on the new object while the IAM binding propagates.

`domains/transport/agents/transport-orchestrator` is deliberately not covered by this: it reads
`GEMINI_APP_ID` from the environment rather than `runtime_variables.json` and keeps its own
`network_node` Material Symbols icon.

#### 3.2 Verification gate — **passed, read and write ✅** *(2026-08-26)*

The gate: all seven deploy; supervisor delegates to each peer and gets a response; chaos can inject
and remove a fault; designer can commit a CR that Config Sync applies.

The ADK bump itself was never the hard part — all seven agents carry
`google-adk[gcp,mcp,otel-gcp]>=2.5,<3` and `a2a-sdk[http-server]>=1.0,<2`, and all seven deploy.
What had never been done is proving each one *behaves*, which is where the real defects were.

Every agent was driven over `:streamQuery` with a read-only prompt and its tool calls read off the
event stream. **Seven of seven answer from live data with no tool errors:**

| Agent | Tools it actually called | Result |
|---|---|---|
| `supervisor` | `query_fault_events`, `getRunningTests`, `delegate_to_agent` → detective | No active faults; 8 tests running, 2 failed. Delegation returns the peer's telemetry, not a summary of the question |
| `designer` | `getVyosDescriptors`, `getDeployedCRs` | Lists the four VyOS CRDs and the deployed transport CRs |
| `chaos` | `getFaultDescriptors`, `getDeployedFaults` | Ten fault types; none currently injected |
| `detective` | `query_affected_flows`, `query_flow_metrics` | 3 flows across `pe1` with measured-vs-requested per direction |
| `impact` | `query_affected_flows`, `query_flow_metrics` | Same, reached independently |
| `tester` | `getRunningTests`, `getDevices` | The 8 running tests with ports, protocol and configured bandwidth |
| `trust` | `query_ospf_paths`, `query_router_metadata`, `query_link_utilization`, `simulate_traffic_addition`, `recommend_capacity_upgrades` | Eleven calls, both OSPF paths for a proposed 300Mbps `pe1`→`pe2` flow, verdict issued |

**Two things this found that no amount of deploying would have.** First, the `tool_filter` gaps in
2.3.b — `impact` could not call two tools its own prompt instructs it to use. Second, the session
contract below, which had made dashboard chat unusable.

**The call shape, since it is not obvious and the wrong one fails silently.** `classMethod:
"streaming_agent_run_with_events"`, with the message JSON-*encoded into a string* under
`input.request_json`. The documented `stream_query` class method returns an empty `200` on these
deployments — no error, no body. `agent_engines.get(...)` is no help either: it has no
`create_session` and `operation_schemas()` returns `[]`.

**Agent Runtime allocates the session id; the client does not.** Pass any string as `session_id`
and the call succeeds, returns a *different* numeric id, and has opened a brand-new session. Only
by echoing that numeric id back does the next turn continue the conversation. This matters because
the supervisor is plan-first: turn 1 proposes a plan and asks to proceed, and it is turn 2 that
calls tools. With a fixed client-side id, turn 2 lands in a fresh session and the agent re-proposes
the plan forever — reproduced by hand, and the cause of 4.4's chat symptom.

**Send a `session_id` on turn 1 even though you do not have one yet.** Omitting the field is worse
than sending a dummy string: the response carries a UUID that the service will not accept on the
next turn, so turn 2 silently opens a fresh numeric session and the conversation restarts. Sending
any string makes it allocate the numeric id up front, and that is the id to echo back. Both
multi-turn drives below held one session across every turn once this was fixed.

##### The mutating clauses ✅ *(2026-08-26)*

**Chaos, both directions, on the live network.** Turn 1 proposed the spec, turn 2 called
`deploySpec`: `OSPF_COST_INFLATION` on `p1`/`eth1`, `ospf_cost: 65535`, `writeMode: git`. The
NetworkFailure reached `Active`, the controller committed `transport/underlay.yaml` to the `network`
repo, Config Sync applied it, the underlay update handler re-ran Ansible against `p1`, and
`show ip ospf interface eth1` on the router reported `Cost: 65535` with the adjacency still Full —
which is the whole point of this fault. Driving chaos to `deleteFault` reversed every layer: CR
gone, `interface_costs` emptied in git and in the CR, router back to `Cost: 1`, and the route to
`10.0.0.4/32` back over `eth1` at metric 1.

**Designer, through the full review chain.** `deployDescriptor` committed a `TrafficTest` to the
`update` branch; the webhook opened PR #1 and called trust. Trust returned **DO NOT PROCEED** on the
first two attempts — correctly, having simulated the addition and found `p1-pe2` already at 102.58%
— and the webhook left the PR open, which is the HITL behaviour working rather than a failure. A
1Mbps/120s version on the red mesh got **PROCEED**, the webhook auto-merged, Config Sync created the
CR, and the controller ran the test to `Completed`. `deleteDescriptor` then took the same path in
reverse (PR #2, merged, CR removed), leaving the lab as it was found.

**Three defects the mutating half found that the read path could not.**

| Defect | Cause | Fix |
|---|---|---|
| Every git-mode fault injection failed on a seeded network | `_patch_via_git` computed `vyosunderlay-l3vpn-underlay.yaml` from the naming convention; the seeded repo keeps the CR at `transport/underlay.yaml` | `find_crd_git_path()` — try the convention, then scan the repo tree and match on kind + `metadata.name`. The convention describes files this code *creates*; it says nothing about a repo seeded from `examples/` |
| The controller could not read or update any nested path | `get_git_file`/`git_file_exists` listed the repo *root* and matched on `c.name` — the same basename-only bug 2.2 fixed in the shared library, still live in the controller's kept copy. `git_file_exists` returning `None` also sent `commit_git_file` down the *create* branch on a file that exists | Both now hit the contents API by path. `py-gitea` 0.3.2's `get_git_content()` takes a commit, not a directory, so listing was never going to work |
| Trust reviewed with no tools and invented one | ADK gives an `MCPToolset` 5s to complete the MCP session handshake; `mcp-networktools` scales to zero, so a cold start blew past it. A toolset that times out does not fail loudly — it comes up empty, and the agent then calls `get_tools ` against a list of nothing | `timeout=30.0` on all five `SseConnectionParams`, and `--min-instances=1` on the RE-facing twin so the handshake never races a cold start. The GE twin stays at zero: it is called interactively, not by an agent that gives up after one handshake |

The first two also existed in the designer and in the MCP twins' `deleteDescriptor`, which would
have created a *second* file for a resource that already had one and left Config Sync with two
sources for one object. `find_crd_git_path()` now lives in `agent_library` alongside a recursive
`list_git_yaml_paths()`, and `get_git_file()` takes a `branch` so the designer can resolve against
`update` rather than `main`.

One gap left open on purpose: the designer's own `deleteDescriptor(name)` tool takes no `kind`, so
for that one call path the lookup still degrades to matching `<name>.yaml` at the repo root. Closing
it means changing the tool signature and the agent prompt, which is a larger change than the bug
warrants — every other write path resolves by reading the repo.

*Incidental, worth chasing separately:* the supervisor reports 2 of 10 traffic tests in a failed
phase after a controller restart.

---

## Phase 4 — Dashboard

### 4.1 Port `dashboard-v2/` ✅ *(install wiring done in 1.0.b; app-level work below)*

Lift and shift. It already speaks `reasoningEngines/{id}:streamQuery`, so there is no integration
work against Phase 3. It brings its own aiohttp backend — the supervisor HTTP/Spanner layer we
folded into it — which is why `services/supervisor` is redundant.

Carried across from `install-new.sh:1586-1620`: `--no-allow-unauthenticated`, `--iap`, the IAP
service-agent invoker binding, and the domain-level `roles/iap.httpsResourceAccessor` binding.

**Do not port `dashboard-v2/deploy_dashboard_v2.sh`.** It deployed `--allow-unauthenticated`
(`:119`), bypassing the IAP the installer sets up. Delete it rather than carry the divergence.

*Superseded by 4.3b, then closed.* Deleting it could not wait for Phase 4 to finish — 4.3b removes
the client-side login, so leaving a script that deploys this dashboard unauthenticated would have
converted a weak control into no control. It was first brought in line with the installer
(`--no-allow-unauthenticated --iap`) to close the hole, and has now been **deleted** ✅ as
originally planned. `install.sh --dashboard` is the single deployment path; it already runs
standalone, rendering its own `cloudbuild.yaml`. `dashboard-v2/README.md` pointed at the script in
two places and now points at the installer, with a note on why the script is gone.
`dashboard-v2/variables.json`, which the script read, was already absent from the tree.

> **Correction.** An earlier draft of this section — and `REPO_COMPARISON.md` §5.4 — claimed the
> credential embedding was unique to that side-door script, on the reasoning that `cloudbuild.j2`
> passes no `--dart-define`. That reasoning is wrong: `--dart-define` is applied at
> `flutter build web` time, not at image-build time, and `install-new.sh:1541-1542` passes
> `WEBAPPS_LOGIN`/`WEBAPPS_PWD` too. **Both paths embed the password.** The side-door script's
> real and only distinguishing sin is `--allow-unauthenticated`. The embedding is 4.3's problem.

### 4.2 Delete `dashboard/` and `services/supervisor/` ✅ *(done, `985efb0`)*

Removed once nothing pointed at them. `services/supervisor/src/tools/` remains reachable at
`git show 6c10467:services/supervisor/src/tools/` for the Phase 2.2 harvest.

### 4.3 Retire the two interim shims ✅ *(both done)*

Both are deliberate, documented compromises taken in 1.0.b to get the tree deploying. Neither is
acceptable as an end state.

**a. Six-database routing in the dashboard backend.** ✅ *(done)*

The plan under-scoped this. It named only `metrics.py` and `topology.py`, which at least read
`GOOGLE_SPANNER_DATABASE` and so were reachable by the shim. `tools/logs.py:21` and
`tools/traces.py:25` hardcoded `SPANNER_DATABASE = 'networktopology-db'` with **no env var at
all** — the shim never touched them, so both were wholly broken against the six-DB schema, not
merely mis-routed. `tools/networkdescriptors.py` inherited topology's connection and was
mis-routed with it.

What landed:

| File | Routing | Schema changes beyond the database name |
|---|---|---|
| `utils/database.py` *(new)* | six-domain router | mirrors `mcp_server_for_ge/re/src/utils/database.py`; adds `reset_database()` for metrics' not-found reconnect path |
| `utils/hosts.py` *(new)* | infra | recovers `Device JOIN PhysicalInterface` from `LinuxNetwork.connected_routers`; derives transport graph ids from the `router:<name>[:interface:<if>]` convention rather than querying transport |
| `tools/traces.py` | ops | none — `AgentTrace` moved database, not shape |
| `tools/networkdescriptors.py` | ops | none |
| `tools/logs.py` | fan out transport/core/radio | `node_name` → `source`; `message` is now a column; per-domain query merged and re-sorted in Python; `delete_logs()` clears all three |
| `tools/metrics.py` | transport + service | `NetworkMetrics` → `RouterSystemMetrics` (`kind='SYSTEM'`) and `RouterRoutingMetrics` (`kind='ROUTING'`); `node_name` → `router_name`; the system half loses `vrf`/`afi`/`peer_ip` and the routing half loses `interface`, so both self-joins were regrouped; `TrafficFlowMetrics` → service |
| `tools/topology.py` | transport + infra + service | `GRAPH_NAME` from `graph_name('transport')`, not the vanished `networkGraph`; `Device` → infra via `utils/hosts.py`; `NetworkMetrics` → `RouterSystemMetrics` in `fetch_snapshots`; `clear_topology()` split across three databases |

Two behaviour changes worth knowing, both forced by the schema:

- **A host can now attach to several routers.** Attribution goes via the shared bridge rather
  than a point-to-point `interface_id`, so `_add_devices_to_topology` emits one edge per
  attachment and carries the full list on the node as `attachments`. The single `router_id` /
  `interface_id` fields are kept, set to the first attachment, so the existing UI still renders.
- **`RouterRoutingMetrics` has no `interface` column**, and its PK is
  `(router_name, metric_name, vrf, afi, peer_ip, timestamp)`. Per-interface OSPF values still
  arrive in `labels` and the parser reads them from there, but two interfaces on the same
  `(vrf, afi, peer_ip)` now collide on write. That is a schema-side issue in
  `gcp/spanner.tpl.yaml`, not something the reader can fix.
  **This prediction was correct and is now fixed — see 7.5.** The collision was real and
  measured: `frr_ospf_neighbors` stored one interface per router out of about five.
  `RouterRoutingSeries` replaces the table and `tools/metrics.py` reads it instead.

The `GOOGLE_SPANNER_DATABASE` shim is deleted from `install.sh`; the variable no longer appears
anywhere in the tree.

**b. Client-side login.** ✅ *(done)*

`environment_config.dart:35-36` read `WEBAPPS_LOGIN`/`WEBAPPS_PWD` as compile-time constants and
`login_screen.dart:61` compared the typed password against them **in the browser**. Anyone who
could fetch the bundle could read the password; anyone who could open a debugger could skip the
check. It was tolerable only because IAP is the actual access control in front of it.

What landed:

| File | Change |
|---|---|
| `lib/screens/login_screen.dart` | deleted |
| `lib/main.dart` | `home:` goes straight to `NetworkDashboard()` |
| `lib/utils/environment_config.dart` | `username`/`password` removed; a comment records why there is deliberately no credential here |
| `install.sh` | both `--dart-define`s dropped from `DeployDashboardV2()` |
| `dashboard-v2/deploy_dashboard_v2.sh` | both `--dart-define`s dropped, plus the two `--update-env-vars WEBAPPS_*` and the `variables.json` reads that fed them |

The plan named only `DeployDashboardV2()`. The side-door `deploy_dashboard_v2.sh` passed the same
two `--dart-define`s **and** shipped `WEBAPPS_LOGIN`/`WEBAPPS_PWD` to Cloud Run as env vars that no
backend code reads — a plain secret leak with no consumer.

That script also deployed `--allow-unauthenticated`, contradicting the installer. Left alone it
would have turned removing the login into a regression: this path would have served the dashboard
to the public internet with nothing at all in front of it. It now matches the installer
(`--no-allow-unauthenticated --iap`), with a note that `--iap` alone grants no access — the
`run.invoker` and `iap.httpsResourceAccessor` bindings still come from `./install.sh --dashboard`.

`WEBAPPS_LOGIN`/`WEBAPPS_PWD` stay in `config.sh`: Gitea still uses them, and `install.sh` still
prints them as the Gitea credentials. Only the dashboard stopped consuming them.

### 4.4 Verification gate — **passed ✅** *(2026-08-26)*

Dashboard reachable only through IAP. Topology renders with live GNN anomaly scores. Chat streams
from the supervisor Reasoning Engine. No credential in the served JS bundle — check the built
bundle directly, not just the build command:
`grep -r "$WEBAPPS_PWD" dashboard-v2/build/web/` must return nothing.

**Bundle check: passed.** Run offline with a sentinel password, both before and after 4.3b, since
"grep found nothing" is worthless without evidence the grep would have found something:

| Build | `grep -rl sentinel-pwd-… build/web/` |
|---|---|
| pre-4.3b tree, `--dart-define=WEBAPPS_PWD=sentinel-pwd-…` | `build/web/main.dart.js` — leaked |
| post-4.3b tree, sentinel in the environment only | nothing |

The residual defaults `YOUR_WEB_LOGIN`/`YOUR_WEB_PASSWORD` are gone from the bundle too, while a
still-used default (`YOUR_SUPERVISOR_ENGINE_ID`) is present — confirming the grep reaches
`main.dart.js` and the credential fields are absent from the source rather than tree-shaken.

`flutter analyze` is clean of errors; the remaining 297 issues are pre-existing infos and
warnings, unchanged in count by 4.3b.

**IAP-only reachability: passed ✅** *(2026-08-26)*. An anonymous `GET /topology/physical` against
the deployed service returns `302` to `accounts.google.com/v3/signin`, and a bare identity token
returns `401` — IAP is in front, not merely enabled. The service carries no `allUsers` binding (see
the 1.4 scan).

**Live topology: passed ✅** *(2026-08-26, `0fb23474`)*. The topology view renders routers, links and
devices, and the `d*` devices now draw their edge to the terminating CE router. That last part took
two fixes, not one: 7.1 put the attribution in Spanner, and the dashboard's own copy of
`utils/hosts.py` was independently discarding it through the `JsonObject` bug — see 7.1.

**Chat: it was not unverified, it was broken — two independent bugs** *(2026-08-26)*.

**One, the wrong region, hardcoded.** `server.py` posted every chat turn to
`europe-west3-aiplatform.googleapis.com` while the supervisor engine is
`projects/809342844318/locations/europe-west1/reasoningEngines/8563797001584508928`. Vertex's
regional endpoints do not serve resources from other regions, so the proxy got a flat `404` on
every turn:

```
europe-west3 -> 404      # same resource path, same token
europe-west1 -> 200
```

The endpoint now follows the engine: `_engine_location()` reads `/locations/<region>/` out of
`REASONING_ENGINE_ID` itself, falling back to `GOOGLE_REGION`. Deriving it from the resource is the
point — the two can never drift apart again, whereas a second env var beside the engine id can.

**Two, the session id was never adopted.** `agent_engine_provider.dart` minted a UUID v4 at
construction and sent it as `session_id` on every turn, never replacing it with the numeric id the
service returns — see 3.2 for the contract. Every message therefore opened a fresh session, and
since the supervisor proposes a plan and waits for confirmation, the user's "yes" arrived in a
session that had never seen the plan. The provider now adopts the returned `session_id`;
`resetConversation()` still starts a new one deliberately.

The first bug hid the second: with every call 404-ing, nothing ever got far enough to notice that
the conversation had no memory.

**Chat: passed ✅** *(2026-08-26)*. Both fixes are deployed (`network-dashboard-v2-00006`), and the
new revision logs its target on startup — `Reasoning Engine projects/809342844318/locations/
europe-west1/reasoningEngines/8563797001584508928 -> europe-west1-aiplatform.googleapis.com`.
Upstream returns `200` for that engine with the exact payload the proxy sends, and the two-turn
session fix was reproduced by hand against the supervisor: with the returned id echoed back, turn 2
calls `query_fault_events` and `getRunningTests` and answers; without it, the agent re-proposes its
plan. The browser half could only be driven by a signed-in user — IAP here uses Google-managed
OAuth with no brand, so there is no audience to mint a CLI token against — and was confirmed
working from the deployed dashboard, closing the last link in the chain.

**Gate 4.4 is closed.**

---

## Phase 5 — Cherry-picks — **on hold**

Optional, and best done after the environment is stable end to end. Put on hold deliberately: the
three open items are all dashboard features, none of them blocks the migration, and every gate in
Phases 1–4 now closes without them.

| Item | Source | Status |
|---|---|---|
| `examples/intents/` | REMOTE | ✅ present — five descriptors + README. Its cross-reference links are broken and three of the four APIs it documents do not exist; unchanged as a caveat |
| `services/gitea/seed.sh` | REMOTE | ✅ present, and extended in 6.3 to also build `networkdesign` |
| `docs/gnn/{examples,research}` | LOCAL | ✅ present |
| Trace Gantt panel | `dashboard/lib/widgets/trace/` | **Open — larger than a cherry-pick.** See below |
| 4-tab GNN analytics + embedding history | REMOTE `dashboard/` | Open. `/embeddings/{node_id}` already exists in `server.py:637`, so the backend half is there |
| Chat question tree | REMOTE `dashboard/lib/tree_menu_dropdown` | Open. Self-contained UI, no backend dependency |

**On the Trace Gantt panel.** The estimate needs revising: this is not a file copy. Of the four
files in `dashboard/lib/widgets/trace/` at `985efb07^`, only `collapsible_json_viewer.dart` was
carried into `dashboard-v2`; `gantt_chart_painter.dart`, `trace_details_dialog.dart` and
`trace_widget.dart` were not. The blocker is the data layer, not the widgets:
`dashboard-v2/tools/traces.py` exists but is built around the Socket.IO supervisor gateway
(`socket_endpoint`, `add_client`, `_emit_to_clients`) — the component dashboard-v2 was written to
eliminate — and `server.py` registers no `/traces` route at all, so nothing serves
`fetch_recent_traces()`. Porting the panel means rewriting `traces.py` onto the aiohttp/Spanner
path, adding the route, then rebuilding the widgets against a different model. That needs a live
Spanner with trace rows to validate against, which is why it stays here rather than being done
blind.

---

## Phase 6 — Re-add what REMOTE dropped ✅

Small, self-contained, easy to forget:

- ✅ `operator/src/gitea_lifecycle/` + the `Gitea` CRD. REMOTE replaced CRD-driven lifecycle with
  shell scripts and dropped the `install`/`webhook` Ansible roles and `failure-analysis.md`
  (594 lines). The handler is deliberately **not** re-added: Gitea now runs on Cloud Run
  (`services/gitea/install.sh`) and the shell path works, so restoring the CRD would mean running
  two lifecycles against one service. The document is recovered on its own — see 6.3.
- ✅ `utils/oslogin.py`, restored to `controller/src/utils/oslogin.py` and re-wired into
  `controller/src/main.py`'s `configure()` startup hook, in the same position it occupied before
  (right after `log_configuration`, wrapped in the same non-fatal try/except). This is more than
  a recovery-from-history task, because the code it supports is all still here: ten
  `lifecycle_tasks.py` files pass `GOOGLE_VM_USER` as `ansible_user` and read
  `google-compute.pub`, and `utils/compute.py:353-380` writes `{vm_user}:{key}` into the VM's
  `ssh-keys` metadata. Where an org enforces OS Login that metadata is ignored outright, so
  without this module every Ansible task against every VM fails to authenticate. The dependency
  it needs, `google-api-python-client`, is already pinned in `controller/requirements.txt`.
  Note the interaction: the module overrides `GOOGLE_VM_USER` with the SA's POSIX name whenever
  it is reachable from GCP metadata, including where OS Login is *not* enforced. That is
  harmless — `ssh-keys` metadata accepts any username — and it is the behaviour the operator
  shipped before `985efb07`, so it is restored as-is rather than newly gated.
- ✅ `logservices/gitea-webhook`'s PR workflow — create, comment, auto-merge on `**PROCEED**`.
  REMOTE's `post_commit_comment()` was a no-op stub returning `True`. Ported as a three-way
  merge, not a revert — see 6.1 below.
- ✅ `DeployTrafficAgent` and `DeployMetricsCollector` install targets — both added to
  `install.sh` and called from `StartPlatform`. `DeployMetricsCollector` also needed its
  `cloudbuild.yaml` render step added to the render block; its own installer reads that file.
  `DeployTrafficAgent` has to run **before** `kubectl apply -f gcp/networkvm.yaml`: the VM's
  3gpp role runs `make device`, and the device image does
  `COPY --from=…/traffic-agent:latest`, so the build fails outright if the image is absent.
  Nothing was building it — the render block wrote `traffic-agent/cloudbuild.yaml` and no code
  ever submitted it. Deleted the stale `traffic-agent/cloudbuild.j2`, the only leftover
  duplicate of an already-converted template.
- ✅ `GEMINI_APP_ID` — restored to `config.sh.template`, documented as optional (absent it, the
  agents deploy to Agent Runtime and skip Gemini Enterprise registration).

**Conflict markers: already resolved.** The two files the plan flagged are no longer broken —
`dashboard-v2/cloudbuild.j2` became `dashboard-v2/cloudbuild.tpl.yaml` with the markers gone, and
`gitea_lifecycle/…/network-role.yaml` left the tree with the handler. `grep -rn '^<<<<<<<'` across
the tree returns nothing.

### 6.1 gitea-webhook port

The webhook in the tree was REMOTE's variant, doubly non-functional: it called a Cloud Run
`trust-agent` service that does not exist here, and its feedback method was a stub returning `True`
with unreachable code after it. LOCAL's variant (recovered from `985efb07^:logservices/gitea-webhook/`)
is strictly better on the agent client and the PR workflow and strictly worse on Gitea discovery
and route path, so all three `src/*.py` were taken from LOCAL and then reconciled:

| Change | Why |
|---|---|
| `TRUST_AGENT_URL` is a `reasoningEngines/…` path, not a service URL | The agents are Agent Runtime engines. `install.sh` now reads it from `agents-in-agent-runtime/trust/deployment_metadata.json`, the way `ReadEngineId` does, instead of `gcloud run services describe trust-agent` — which always returned empty and silently deployed a webhook that could not call anything |
| Target is the `trust` agent, not `impact` | `trust/app/agent.py:34` is the agent that emits the PROCEED / DO NOT PROCEED verdict `main.py` auto-merges on |
| `BASE_BRANCH`, default `main` | LOCAL's PR code used `base="master"`. `services/gitea/seed.sh:70,110,119` creates every repo with `default_branch: main` and cuts `update` from `main`, so `master` would have 404'd on every PR create. Four literals in `main.py` plus the `gitea_client.create_pull_request` default |
| Both `/webhook/gitea/trust` and `/webhook/gitea/impact` route to one handler | The installer registers `/trust`; `/impact` is the documented path and may already exist on a live Gitea |
| `get_gitea_url()` asks the Cloud Run Admin API, not `kubectl` and not `gcloud` | The `Gitea` CRD is gone, and the image is `python:3.13-slim` with no gcloud binary, so the `subprocess` fallback could never have worked. `install.sh` also passes `GITEA_URL` when Gitea is already up, so discovery is only the first-deploy path |
| `requirements.txt`: drop `a2a-sdk` and `kubernetes`, add `google-auth==2.53.0` | Nothing imports `a2a` or `kubernetes` any more; `google-auth` was only reaching the image transitively through `kubernetes`. Version matches `mcp_server_for_ge/{ge,re}` |
| `requirements.txt`: pin `requests==2.32.5` back | Fallout from the row above, found on first deploy: `a2a_client.py` imports `google.auth.transport.requests`, which needs `requests` — that is the `google-auth[requests]` extra, not a dependency of `google-auth` itself, and it had been arriving transitively through the `kubernetes` that was just removed. The container exited 1 on startup with `ModuleNotFoundError` |

Left alone deliberately: `httpx.AsyncClient(verify=False)` at all eight Gitea call sites. TLS
verification is on the out-of-scope list below — fix it everywhere at once, not in one file.

**Deployed and verified end to end** on `an-gdemo`. A push to the `network` repo's `update` branch
produced, in one delivery: diff fetch → trust `:query` (200) → PR created → verdict comment posted →
auto-merge on `**PROCEED**`. The verdict was a substantive review of the actual diff, not a
template. Re-verified after re-running the installer from scratch, which re-minted the API token and
re-registered the hook.

Re-verified a third time on 2026-08-26, after 7.2 moved Gitea to GKE behind a reserved address and
a persistent disk — this time driven by the designer agent rather than a hand-made push, so the
whole chain ran: designer commit → `update` → PR → trust verdict → auto-merge → Config Sync applied
the CR. The hook and the token survived the redeploy, as 7.2 intended. See *The mutating clauses*
under 3.2 for what that run turned up.

Three wiring gaps had to be closed first — the code was complete, but nothing deployed it:

| Gap | Fix |
|---|---|
| `DeployGiteaWebhook()` was defined in `install.sh` and never called | Called from `DeployAgents`, positioned after the domain services and before the dashboard. Also exposed as `install.sh --webhook` for re-running just this step |
| The hook registration in `services/gitea/install.sh` was commented out, and broken under the comments — it used `$REGION` and `$ADMIN_USER` (neither exists; the script uses `$GOOGLE_REGION` and `$WEBAPPS_LOGIN`) and `openssl rand -hex 32` for the secret, which could never match the `sha256("${GOOGLE_PROJECT}-webhook-secret")` the service computes. Every delivery would have failed the signature check | Deleted rather than uncommented, and ownership moved to `services/gitea-webhook/install.sh`, which is the first point where the webhook URL and the trust engine id both exist. The dead `WEBHOOK_URL` branch in `seed.sh` went with it; `seed.sh` still cuts the `update` branch |
| Cloud Run's IAM layer rejected every delivery with a 403 | Gitea's webhook sender sends a plain HTTP POST and cannot mint a Google OIDC token, so `--no-allow-unauthenticated` made the path permanently dark. Switched to `--allow-unauthenticated` with the HMAC signature as the gate — see the *gitea-webhook: why it is public* section in `docs/SECURITY.md` for the accepted residual risk |

One incidental bug fixed while making the installer idempotent: the existing-token lookup used
gcloud's `env.filter(name="GITEA_API_TOKEN")` projection, which silently yields nothing, so every
run minted a new token and burned an extra Cloud Run revision. It reads the env list as JSON now.
Hook registration deletes any hook already pointing at `/webhook/gitea/trust` before creating one,
since Gitea has no upsert and a duplicate would run the trust agent twice per push.

### 6.2 The same two bugs everywhere else that talks to Gitea ✅

Fixing 6.1 turned up the same pair of defects in every other Gitea client in the tree. Both are
consequences of the same two facts — Gitea is a Cloud Run service now, and its repos only have a
`main` branch — and both were silently fatal rather than noisy.

**`MASTER_BRANCH = 'master'`.** Four more copies used it for every commit, delete and branch-cut.
This broke the MCP GitOps **write** path (`deployDescriptors`, `deleteDescriptor`) and the
designer agent's write path as surely as `base="master"` broke PR creation; the difference is only
that nobody had exercised them yet. All now read `os.environ.get('GITEA_BASE_BRANCH', 'main')`,
the same knob the webhook uses.

**`get_gitea_url()` reading a CRD that no longer exists.** Three copies did
`resources.get(api_version='google.dev/v1', kind='Gitea')` and pulled an external IP out of its
status. There is no `Gitea` CRD anywhere in `controller/config/`, so this could only ever return
`None` — meaning *every* Gitea tool in the MCP plane and in the designer agent failed, including
the ones 2.2.e had just ported. All three now read `GITEA_URL`, falling back to the Cloud Run
Admin API.

| File | `master` | dead CRD |
|---|---|---|
| `mcp_server_for_ge/re/src/utils/{git_helpers,gitea_extension}.py` | ✅ | ✅ |
| `mcp_server_for_ge/ge/src/utils/{git_helpers,gitea_extension}.py` | ✅ | ✅ |
| `lib/src/agent_library/gitea_utils.py` | ✅ (+ two docstrings) | ✅ |
| `agents-in-agent-runtime/designer/app/app_utils/git.py` | ✅ | ✅ |
| `dashboard-v2/utils/git_utils.py` | n/a — no branch constant | n/a |

Discovery only covers the deployed-Gitea-first case; the URL is now passed explicitly wherever it
is known: `mcp_server_for_ge/{re,ge}/deploy.sh` add `GITEA_URL` plus the `WEBAPPS_*` credentials
the Gitea tools authenticate with (they were reaching `git_helpers.py`'s `'admin'`/`'password'`
fallback), and `deploy_agents.py` passes `GITEA_URL` to every agent.

**One more live bug found in the same pass.** `mcp_server_for_ge/re/deploy.sh` passed both
`--set-env-vars` and `--update-env-vars` to one `gcloud run deploy`. They are in gcloud's "at most
one of these" group, so the command was an argparse error — `re/` could not deploy at all, which
is Phase 2.3's first gate. Merged into a single `--set-env-vars`.

Consolidating these helpers onto `agent_library` was the outstanding cleanup here; it is done in
6.4, where the real count turned out to be seven rather than five. The `re/`/`ge/` twins must still
change together — consolidation shrank the duplicated surface but did not remove it.

### 6.3 The `networkdesign` repo was documented but never created ✅

Recovering `failure-analysis.md` meant working out where it should land, and that turned up a
third defect of the same kind. `services/gitea/Readme.md:44` lists a `networkdesign` repo,
`services/gitea/install.sh:128,133` prints its clone URL, and every design-document reader in the
tree targets it — `DESIGN_REPO = 'networkdesign'` in the designer agent, the chaos agent, and both
`mcp_server_for_ge` `git_helpers.py`. `seed.sh` never created it. It seeds `network`, `root-repo`
and one repo per domain, and nothing else, so on a fresh install `getDesignDoc()` and
`getTransportDesignDoc()` returned "Design documentation not found" and `getFailureAnalysis()`
returned its upload-the-file-first error — with no way for the operator to comply, since the file
was not in the tree either.

`seed.sh` now creates `networkdesign`, assembled at seed time from the canonical copies rather
than duplicated into `services/gitea/repos/`:

| File in the repo | Source in the tree |
|---|---|
| `designdoc.md` | `domains/transport/knowledge/designdoc.md` |
| `failure-analysis.md` | `docs/gnn/failure-analysis.md` *(restored — see below)* |
| `rca.md` | `docs/gnn/rca.md` — `failure-analysis.md` links to it |

Layout is flat because the readers fetch by bare filename, not by path. A missing source warns
and is skipped rather than aborting the seed.

**Which `failure-analysis.md`.** Two divergent lineages existed at `985efb07^`: 642 lines under
`docs/gnn/`, and 594 lines under `gitea_lifecycle/…/networkdesign/files/`. The 594-line one is
correct and is what was restored. The longer version documents faults 10 (BGP Update Storm) and 11
(Cross-VPN Route Leak) as "recommended additions", and
`controller/src/networkfailure/lifecycle.py` implements neither — the chaos agent's whole job is
recommending which fault to inject, so advertising two that cannot be injected is worse than
having no document. Restored to `docs/gnn/failure-analysis.md`, next to the `rca.md` it
cross-references and at the exact path the chaos agent's error message already tells operators to
use.

### 6.4 Seven copies of the Gitea client, four of them now gone ✅ *(`b037a503`, `39b4ccf2`)*

6.1, 6.2 and 6.3 were each one defect that had to be fixed in four or five files. The inventory
behind that was also wrong: counting properly turned up **seven** copies, not five —
`controller/src/utils/git.py` and `services/sharedmcp/src/utils/gitea_utils.py` had never been
counted, and `mcp_server_for_ge/{re,ge}` carried *two apiece* (`git_helpers.py` and
`gitea_extension.py`, the same monkeypatches twice, in the same directory).

| Copy | Outcome |
|---|---|
| `mcp_server_for_ge/re/src/utils/git_helpers.py` | 159 lines → ~50-line facade over `agent_library` |
| `mcp_server_for_ge/ge/src/utils/git_helpers.py` | ditto |
| `mcp_server_for_ge/{re,ge}/src/utils/gitea_extension.py` | deleted |
| `agents-in-agent-runtime/designer/app/app_utils/git.py` | 229 lines → 97 |
| `dashboard-v2/utils/git_utils.py` | already consolidated; docstring corrected |
| `controller/src/utils/git.py` | **kept** — pins `py-gitea==0.3.2` against `0.2.10` everywhere else, and `agent_library` monkeypatches SDK internals. Its two live bugs fixed in place instead |
| `services/sharedmcp/src/utils/gitea_utils.py` | **kept** — builds with `uv sync --frozen`; consolidating needs a lock regeneration and a path-dependency restructure, and this copy has no live defect |

What stays local to each consumer is only what is genuinely local: repository names for the MCP
tools, and for the designer, the fact that it never writes to `main` — every write goes to
`TARGET_BRANCH` (`update`) for review, and the filename resolvers that read back whatever naming
convention is already on that branch.

**Vendoring.** `lib/` is outside the MCP services' `gcloud run deploy --source` context, so their
`deploy.sh` copies `lib/src/agent_library` into `src/` first, exactly as `deploy_agents.py` already
does for the agents. The copy is gitignored — which is precisely why each service needs a
`.gcloudignore`: with none present, gcloud synthesizes one from `.gitignore` and drops the vendored
library from the Cloud Build upload, and the container dies at import with `ModuleNotFoundError`.
The cost is zero new dependencies: `agent_library/__init__.py` imports only `credentials` and
`gitea_utils`, never `trace` or `a2a_helpers`, and the MCP `requirements.txt` already pins all six
of the resulting transitives.

**A latent bug the consolidation exposed.** `agent_library.commit_git_file` called `create_file`
unconditionally on any non-default branch, on the stated assumption that such branches are always
empty. They are not: a second designer run against an unmerged `update` branch finds its own
earlier commit there, so every repeat write returned 422, retried four times and returned `None`.
Existence checks in both `commit_git_file` and `delete_git_file` are now scoped to the branch being
written to. `list_git_files()` was added so the designer's resolvers could survive the removal of
its local `get_branch_git_contents()`.

**And two more live controller defects,** same family as 6.2, found by reading the Dockerfile
rather than assuming. `controller`'s `get_gitea_url()` shelled out to `gcloud run services
describe`; the image is `python:3.13.2` with no gcloud in it, so the subprocess raised
`FileNotFoundError` and the entire GitOps fault-injection path in
`networkfailure/operator_injection.py` was dead in the deployed controller — while working fine
locally, where gcloud is on `PATH`. It now uses the Cloud Run Admin API over HTTP. Its
`commit_git_file` also still defaulted to `branch='master'`, with no caller overriding it; 6.2 had
missed this copy.

---

## Phase 7 — Operational hardening, found by running the thing

Gaps found while exercising the deployed `an-gdemo` environment after the 2.3 gate passed. None of
them blocks the migration and none is a porting question — each is a decision about how the
platform should be run, which is why they are collected here rather than retrofitted into the
phase that shipped the component. Each was reproduced against the live project; the evidence is
recorded inline so none of it has to be re-measured.

### 7.1 Devices are isolated nodes — there is no `d*` → CE-router edge ✅ *(done, 2026-08-26)*

**Observed.** In `infratopology-db`, `d4-red`, `dh-blue` and every other `ComputeHost` connects to
nothing. They are not attached to the CE router that actually terminates their LAN, so the graph
says the devices float free of the network they sit on.

**Why.** `infraGraph`'s `EDGE TABLES` block (`gcp/spanner.tpl.yaml:685-701`) declares exactly three
edges — `HostHasMetrics_Edge`, `ComputeHostHasEmbedding_Edge`, `LinuxNetworkHasEmbedding_Edge`.
Every one of them points at a metrics or embedding table. There is no topology edge in the infra
graph at all. That splits into two different problems with two different fixes:

| Hop | Scope | Status |
|---|---|---|
| `ComputeHost` → `LinuxNetwork` | Same database | Pure schema gap. `ComputeHost.network_name` already holds the join key and is already populated — nothing declares it as an edge |
| `LinuxNetwork` → `PhysicalRouter` | **Cross-database** | `LinuxNetwork.connected_routers` is a JSON array of `{router_name, interface, ip_address}`, but `PhysicalRouter` lives in `transporttopology-db`. Spanner property graphs cannot span databases, so decision 0.1 forecloses a real edge here |

This is the first concrete instance of the risk already on the register as *"a cross-domain query
is discovered mid-migration that the six-DB split cannot serve"*. It is not a surprise; it is that
risk arriving.

**What it costs today.** Two things, both already paid. The GNN trains on a graph in which every
device is an isolated node, so nothing a device does can propagate to the router serving it. And
`query_affected_flows` (2.2.a-bis) could not ask Spanner which router a flow's endpoints hang off —
it builds the entire device→network→router join in Python against Kubernetes CRs instead, because
that was the only place the relationship existed in one piece.

**DECIDED — declare the intra-DB edge, denormalize the cross-DB one.**

1. **Add `ComputeHostOnNetwork_Edge` to `infraGraph`.** Spanner lets an existing node table double
   as an edge table, so this is a declaration against data that is already there — `SOURCE KEY (id)
   REFERENCES ComputeHost`, `DESTINATION KEY (network_name) REFERENCES LinuxNetwork`. No new
   columns, no backfill, no writer change.
2. **Add `attached_router` and `attached_router_interface` to `ComputeHost`,** populated by the
   controller from the same `LinuxNetwork.connected_routers` data it already writes. This makes the
   device→router relationship a logical reference of exactly the kind the schema already uses and
   already documents — `TrafficFlow.src_device_id` is a `device:{name}` reference into another
   database (`gcp/spanner.tpl.yaml:705-707`). One convention, applied twice, instead of a special
   case.
3. **Do not merge `infratopology-db` into `transporttopology-db`** to get a real graph edge. That
   reopens 0.1 for one traversal, and the same argument would then apply to core, radio and service.

**BUILT — and it needed one more piece than the decision listed.**

| Change | Where |
|---|---|
| `attached_router`, `attached_router_interface` on `ComputeHost` | `gcp/spanner.tpl.yaml` |
| `ComputeHostOnNetwork_Edge` view + the edge in `infraGraph` | `gcp/spanner.tpl.yaml` |
| Populate both columns on every Device sync, and back-fill on every LinuxNetwork sync | `controller/src/graph/infra_tasks.py` |
| `_device_attachment_map()` now one Spanner read instead of two Kubernetes list calls | `mcp_server_for_ge/{re,ge}/src/tools/topology.py` |

Three things the decision got wrong or did not anticipate, all worth recording:

1. **The edge could not be a bare declaration.** The decision said "`SOURCE KEY (id) REFERENCES
   ComputeHost`, `DESTINATION KEY (network_name) REFERENCES LinuxNetwork` — no new columns, no
   backfill". That is not expressible: `LinuxNetwork` is keyed on `(id, valid_start_ts)` and
   `ComputeHost.network_name` holds a *name*, not an id, and carries no timestamp. The edge needs a
   view to supply the destination's `valid_start_ts`, exactly like the three edges already there.
   Its `WHERE` is an interval overlap because both sides are SCD Type 2.
2. **The DDL had to be appended, not edited.** Config Connector's `ddl` list is append-only — the
   provider applies statements added to the end and refuses to re-apply an edited one. Folding the
   new columns into `CREATE TABLE ComputeHost` would have been a silent no-op on every database
   that already exists. So the file carries `ALTER TABLE` statements and a *second*
   `CREATE OR REPLACE PROPERTY GRAPH infraGraph`. A fresh install runs the whole list and lands in
   the same state.
3. **`attached_router` is deliberately NULL when ambiguous.** It is written only when the host's
   bridge has exactly one connected router. That covers every `lan-*` device bridge, which is the
   case the column exists for. `mgmt` has eighteen routers on it and a core link has two; there is
   no single terminating router there and inventing one would be worse than nothing.

**The bug this uncovered, which is the more valuable half of 7.1.** The Spanner client returns a
JSON column as `JsonObject`, which subclasses `dict` **even when the value is an array**. Both
`_as_list()` in `mcp_server_for_ge/{re,ge}/src/utils/hosts.py` and the first cut of the controller
helper tested `isinstance(value, list)` — false for every row — and answered `[]` without raising.
That means `hosts_on_router()` and `routers_for_hosts()` have been returning *nothing* for every
query, silently, for as long as they have existed. `_as_list` now round-trips through the
documented `serialize()`; the controller sidesteps the question entirely by doing the extraction in
SQL with `JSON_QUERY_ARRAY`/`JSON_VALUE`, which returns plain `STRING`s.

This is worth generalising: the failure was invisible because "no rows" and "wrong type" are the
same answer here. Any other `isinstance(x, list)` test on a Spanner JSON column in this tree is the
same bug.

**And there was a third copy, found the way the first one should have been — by looking at the
dashboard** *(2026-08-26, `0fb23474`)*. `dashboard-v2/utils/hosts.py` is a deliberate second copy of
the MCP helper (it cannot import it; see `utils/database.py`) and carried the identical guard. Its
`fetch_hosts()` is what `/topology/physical` uses to attach devices, so every host was returned with
`attachments: []`, hence no `target_router_id`, hence no `device_to_interface` connection, hence the
`d*` nodes drew as isolated — which is precisely the symptom that opened 7.1. Fixing the Spanner
columns was necessary but never sufficient: the *renderer* was dropping the relation independently.

Measured against the live infra database before and after: the old helper recovered **0 of 74**
router entries across 33 bridges, the fixed one recovers all 74, giving 8 device→CE edges. The
Flutter side needed no change — `appstate.dart` already handles `device_to_interface` and matches
`target_router_id` against the node set, and the transport ids it derives by convention
(`router:ce2-red`) are exactly the `PhysicalRouter.id` values in the database.

A sweep of the rest of the tree found no fourth copy: the other `connected_routers` readers are all
in the controller and all take the value off a Kubernetes CR spec, where it is a real Python list.
`mcp_server_for_ge/*/src/tools/{configdrift,simulations}.py` only mention it in comments and route
through the fixed helper.

**Verified live on `an-gdemo`.** All eight devices traverse the new edge and carry their CE router:

```
device   ip           network     ce_router  ce_iface
d1-blue  10.100.1.10  lan-spoke1  ce1-spoke  eth2
d4-red   10.101.4.10  lan-red4    ce4-red    eth2
dh-blue  10.100.2.10  lan-hub     ce1-hub    eth2      (8 rows)
```

`d4-red` and `dh-blue` — the two named in the original report — are no longer isolated.

**Knock-on: `servicetopology-db` is populated and unread.** Measured on the live project while
investigating this — `TrafficFlow` holds 30 current rows (e.g. `flow:d1-hub-bidir-tcp`,
`host:d1-blue` → `host:dh-blue`, `running`) and `TrafficFlowMetrics` holds 23,200. No MCP tool
reads either. That gap was previously filed as "a database no tool reaches"; it is worse than that,
because the data is real and current. Once `ComputeHost` carries `attached_router`, flow→router
attribution is answerable in Spanner and `query_affected_flows` can drop its Kubernetes join.
**Closed by 2.3.b** ✅ — it did, and the flows now carry their measurements.

### 7.2 Gitea has no durable storage — **DONE: it runs on GKE with a retained PVC** ✅ *(2026-08-26)*

2.3.a records the incident: after one idle night on `an-gdemo`, `network`, `root-repo` and
`networkdesign` were gone, both Config Sync reconcilers were crash-looping on a 404, and the trust
webhook and its API token went with the database. The cause is one line of `gcloud run services
describe` output — no `volumes`, no `volumeMounts`. The SQLite database and every repository sit on
the container's ephemeral filesystem.

| Option | Durable? | Verdict |
|---|---|---|
| GCS volume mount (gcsfuse) on Cloud Run | Not safely | **Rejected.** gcsfuse offers no POSIX file locking and no atomic rename. SQLite needs both, and so does git for `index.lock` and ref updates. It would appear to work and corrupt under concurrency — the worst possible failure mode for the GitOps source of truth |
| Filestore (NFS) volume mount on Cloud Run | Yes | Workable. NFS does lock, and Cloud Run supports `--add-volume=type=nfs`. But the smallest Filestore instance is 1 TiB for a repository set measured in megabytes, plus Direct VPC egress into `mgmt`. Wrong order of magnitude for a lab |
| **GKE Deployment + PersistentVolumeClaim** | Yes | **BUILT.** The `networkautomation` cluster already exists, a 20 Gi balanced PD is a few dollars a month, and it gives Gitea an ordinary filesystem with exactly the semantics SQLite and git both assume |

**A Deployment, not a StatefulSet.** The decision above said StatefulSet; what shipped is a
`Deployment` with `replicas: 1` and `strategy: Recreate`, plus a standalone PVC. A StatefulSet buys
stable per-replica identity and per-replica volumes, and Gitea has exactly one replica forever —
an RWO disk permits one writer, and two Gitea processes on one SQLite file is corruption. The
`Recreate` strategy is the load-bearing part: a RollingUpdate would start the new pod before the
old one released the disk. Nothing about the StatefulSet was needed, so it was not used.

**What shipped.**

| Piece | Detail |
|---|---|
| `services/gitea/deployment.tpl.yaml` | New. `StorageClass gitea-retain` (`pd-balanced`, `reclaimPolicy: Retain`, `WaitForFirstConsumer`) + 20Gi `PersistentVolumeClaim gitea-data-pvc` + the Deployment + a `Service type=LoadBalancer` |
| Exposure | An L4 `LoadBalancer` on the **reserved regional** address `gitea-ip` — `34.156.121.236` on `an-gdemo`. `:443 → :3000` |
| TLS | Self-signed, generated by `install.sh`, SAN covering the IP plus the in-cluster DNS names, stored as secret `gitea-tls` and reused across redeploys |
| `services/gitea/gitea-url.sh` | New. One owner for the URL lookup, replacing **six** copies of `gcloud run services describe gitea` |
| Discovery fallbacks | `lib/.../gitea_utils.py`, `controller/src/utils/git.py`, `services/gitea-webhook/src/main.py` now query the Compute API for the reserved address instead of the Cloud Run Admin API. They cannot shell out — their runtime images have no `gcloud` |
| `uninstall.sh` | Keeps the PVC, the PV and the address by default; `--purge` is required to destroy repositories |

**No `nip.io`, no managed certificate.** The original plan implied an Ingress with a
Google-managed certificate. Managed certificates validate *domains*, not IP addresses, so that path
needed a hostname the lab does not have, and the `nip.io` workaround was rejected on the user's
call: it puts a third-party wildcard DNS service on the critical path of every GitOps write, for
nothing but a certificate that no client in this tree actually verifies. Every consumer already
skips verification — `verify=False` at 24 sites in the libraries and 7 in `gitea_client.py`, `-k`
in the shell scripts, and `noSSLVerify: true` in both Config Sync reconcilers. So: bare public IP,
self-signed cert, L4 LoadBalancer. Note that an L4 Service needs a **regional** static address; a
global one silently does not attach, which cost a round trip here.

**Two defects found doing it, worth remembering.**

1. **`/api/v1/version` is not a health endpoint under `REQUIRE_SIGNIN_VIEW = true`** — it returns
   403 to an unauthenticated prober, so the pod is healthy, listening, and never Ready. Probes must
   use `/api/healthz`, which is exempt. Upstream `cloud-gtm/autonomous-network` has an unwired draft
   `deployment.tpl.yaml` carrying exactly this bug.
2. **The stored `GITEA_API_TOKEN` died with the old database but nothing noticed.** The webhook
   installer reused it unconditionally and failed at hook registration. It now probes
   `/api/v1/user` and re-mints on anything but 200.

Also fixed in passing: the root installer's Gitea-CRD lookup had always failed, silently baking
`https://localhost:3000` into the dashboard's JS bundle.

**Verified live, not assumed.** Seven repositories were mirror-cloned to
`gs://an-gdemo-gitea-backup/` first, the `network` repo's real history (11 commits, `main`
`c499d033`, `update` `a1f6b8e7`) was restored by force-push onto the fresh disk, and then the pod
was deliberately deleted: after rollout all seven repos, both refs and the `networkagent` admin
user were intact. RootSync and RepoSync are on the new URL and reporting `Sync Completed`. The
Cloud Run `gitea` service is deleted, which also closes 7.3's residual — there is no longer a
Cloud Run service to cap.

**Not carried over:** the interim `git bundle`-to-GCS mitigation from 2.3.a. It bounded a loss
window that no longer exists. A real backup schedule is still worth having, but it is now a
convenience rather than the only thing standing between the lab and total loss.

**The consumer side had to move too, and that was the part that stayed broken longest.** Deleting
the Cloud Run `gitea` service does not update anything that hardcoded its URL, and the agent fleet
did: all seven Reasoning Engine agents were still carrying
`GITEA_URL=https://gitea-3wv6vjxvyq-ew.a.run.app` — a host that no longer resolves — so the
designer→trust GitOps loop was dead even though Gitea itself was healthy. Nothing surfaced this;
the agents simply failed at tool-call time. Re-running `deploy_agents.py` with `GITEA_URL` exported
from `gitea-url.sh` fixed all seven, verified by reading `spec.deploymentSpec.env` back off the
`reasoningEngines` REST listing. `transport-orchestrator` is still unset and stays that way — it
falls back to the Compute API lookup, which is the documented path.

Two things survived the migration untouched, which is the point of the Retain PVC: the `network`
repo's push hook (`branch_filter: update`, pointing at the `gitea-webhook` Cloud Run service) and
the `GITEA_API_TOKEN` the webhook authenticates with — the token still answers 200 on
`/api/v1/repos/networkagent/network/branches`. The webhook service's `TRUST_AGENT_URL` also still
matches the trust engine id after the fleet redeploy, since the engine ids are stable across
`deploy_agents.py` runs. So the loop needed no re-wiring beyond the agents themselves.

*Note on `deploy_agents.py`:* there is no top-level virtualenv in `agents-in-agent-runtime/`, only
per-agent `.venv` directories. Running it with the system `python3` dies on
`ModuleNotFoundError: No module named 'google'`. Use one of the agent venvs, e.g.
`supervisor/.venv/bin/python deploy_agents.py`.

### 7.3 Cloud Run scaling is unbounded almost everywhere — **DECIDED and applied** ✅ *(2026-08-26)*

Five of the nine deployed Cloud Run services have no `maxScale` at all, which means the platform
default of 100. The number that matters here is not the bill; it is how much concurrency each
service can impose on a shared backend that has no such cap — Spanner's per-transaction mutation
limits, and Gitea's hard single-writer constraint.

| Service | Deployed by | `maxScale` as found | `min` | **Decided max** | Why that number |
|---|---|---|---|---|---|
| ~~`gitea`~~ | ~~`services/gitea/install.sh:42`~~ | unset → 100 | 0 | **n/a** | **Moot.** 7.2 moved Gitea off Cloud Run entirely; the service is deleted. The single-writer constraint it was protecting is now enforced structurally by `replicas: 1` + `strategy: Recreate` against an RWO PVC |
| `capture-log` (Cloud Function gen2) | `services/logcollector/install.sh:85` | unset → 100 | 0 | **10** | Pub/Sub-triggered, and every invocation writes Spanner. 100 concurrent writers against `transporttopology-db` is a mutation-limit incident waiting for a log burst |
| `network-dashboard-v2` | `install.sh:1093` | 100 | 0 | **5** | A handful of operators. Each instance holds Spanner sessions and an open Vertex AI stream |
| `sharedmcp` | `services/sharedmcp/install.sh:75` | 100 | **1** | **10** | Keep `min 1` — cold start sits directly on agent latency |
| `gitea-webhook` | `services/gitea-webhook/install.sh:145` | 100 | 0 | **3** | One delivery per push; 3 absorbs a multi-commit push |
| `mcp-networktools` (`re`) | `mcp_server_for_ge/re/deploy.sh:100` | 10 | 0 | **10** — no change | Already bounded |
| `mcp-networktools-ge` | `mcp_server_for_ge/ge/deploy.sh:138` | 10 | 0 | **10** — no change | Already bounded. Public-facing behind a shared secret, so this cap is also the blast radius of a leaked token |
| `transport-intentserver` | `domains/transport/services/intentserver/install.sh:145` | 10 | 0 | **10** — no change | Already bounded |
| `transport-topologyserver` | `domains/transport/services/topologyserver/install.sh:91` | 10 | 0 | **10** — no change | Already bounded |
| `metricscollector` (worker pool) | `services/metricscollector/install.sh:78` | n/a | n/a | **1** — no change | Worker pools scale by `--instances`, not `maxScale`, and it is already pinned at 1. It must stay 1: it is a singleton poller with a retention thread, and a second copy would double-write every metric |

Five services change, five are already right. `domains/service/services/mcp/install.sh:125` also
sets `--max-instances 10` but is not deployed on `an-gdemo`; listed so the next audit does not
re-discover it.

The caps go into the installers, not only into a one-off `gcloud run services update` — an
out-of-band change is erased by the next deploy, and this whole class of finding exists because the
defaults were never written down anywhere. All five are now in their installers, and four of them
were also applied to the running services so the deployed environment matches:

```
capture-log 10   gitea-webhook 3   network-dashboard-v2 5   sharedmcp 10 (min 1)
```

**`gitea` was the deliberate exception, and is now closed.** Its cap was in
`services/gitea/install.sh` but deliberately *not* applied live, because `gcloud run services
update` creates a new revision — which recycles the instance, which is precisely the event that
destroys every repository (7.2). Applying a cap of 1 to a service receiving almost no traffic bought
nothing worth a guaranteed wipe. 7.2 resolved it the other way: Gitea left Cloud Run, the service is
deleted, and single-writer is now a structural property of `replicas: 1` on an RWO PVC rather than a
scaling flag that has to be remembered.

### 7.4 BigQuery `serviceperformance` is empty — its only writer has never started ✅ *(done, 2026-08-26)*

**Observed.** The dataset and all four tables exist and are Config-Connector-managed from
`gcp/bigquery.tpl.yaml` — `nodeembedding`, `routerroutingmetrics`, `routersystemmetrics`,
`trafficflowmetrics`. All four measured at **0 rows**, with ten traffic tests running and Spanner's
equivalent tables filling normally.

**Why.** The only component in the tree that writes BigQuery is `services/ingestion/src/main.py`, a
Beam pipeline that polls Cloud Monitoring and fans out to Spanner and BigQuery in parallel. It runs
as the `ingestion` `DataflowFlexTemplateJob` (`gcp/dataflow.tpl.yaml:159`). That job has never
started once. Every launch dies identically:

```
Failed to start the launcher VM, launcher-2026082523412888..., because of status code:
INVALID_ARGUMENT, reason: Invalid value for field
'resource.networkInterfaces[0].network': 'global/networks/default'.
The referenced network resource cannot be found.
```

`an-gdemo` has exactly one VPC — `mgmt`, custom mode. There is no `default` network. The CR sets
neither `networkRef` nor `subnetworkRef`, so Dataflow falls back to `default` and the launcher VM
cannot be created. Config Connector retries indefinitely: the CR has been `UpdateFailed` for 25
hours and the project has accumulated **100 failed `ingestion` jobs**, one roughly every two
minutes. Two smaller things fall out of the same read: the header comment at
`gcp/dataflow.tpl.yaml:32` still advertises a "PubSub_to_BigQuery template" that the file no longer
uses — nothing publishes to Pub/Sub on this path — and `nodeembedding` has no writer in either
design.

**The real finding is underneath that.** `services/ingestion` and `services/metricscollector` are
two implementations of the same job. Both poll Cloud Monitoring; both write `RouterSystemMetrics`,
`RouterRoutingMetrics` and `TrafficFlowMetrics`. Only `metricscollector` is running, which is
exactly why Spanner is populated and BigQuery is not. Repairing the Dataflow networking without
retiring the worker pool would give those three tables two concurrent writers on the same primary
keys — turning an empty-dataset annoyance into a data-integrity problem.

**DECIDED: keep `metricscollector`, add a BigQuery sink to it, retire the Dataflow job.**

- It is the implementation that works, and it costs one always-on Cloud Run worker instance against
  a streaming Dataflow job's 24/7 worker plus Streaming Engine.
- The change is small and local: `metricscollector` already holds the exact records in hand at the
  point it writes Spanner, and the BigQuery schemas in `gcp/bigquery.tpl.yaml` already mirror the
  Spanner columns, so the sink is an `insert_rows_json` alongside the existing write.
- No IAM round trip — the shared `networkagent` SA already carries `roles/editor`. That is its own
  problem (see the SA-trimming item under *Deliberately out of scope*), but it does mean nothing is
  blocked on a permissions change.
- Deleting `gcp/dataflow.tpl.yaml` also removes the temp bucket, `dataflow-worker-sa` and its six
  role bindings, a seventh binding granted to the default compute SA, and the two-minute retry loop.

**Done, and measured.** `services/metricscollector/src/main.py` now streams every row it writes to
Spanner into the matching BigQuery table. `_write_bigquery()` reuses the `columns` tuple already
passed to `_write_batches()`, so the two sinks cannot drift; the only conversion is
`DatetimeWithNanoseconds` → ISO string, which Spanner takes natively and the JSON streaming API
cannot serialize. The Spanner table name lowercased *is* the BigQuery table name, and the schemas
match column for column, so there is no mapping table to maintain. Both sinks are independent by
design — every BigQuery failure is logged and swallowed, because BigQuery is analytics and Spanner
is what the tools and the dashboard read. `BQ_DATASET=""` turns the sink off.

Verified against the live project rather than assumed. A probe insert first confirmed the two
non-obvious column types round-trip — `JSON_VALUE(labels, '$.device')` parses back out of the JSON
column, and the TIMESTAMP lands correctly. After redeploying the worker pool, all three tables went
from **0 rows** to filling continuously: 53,011 rows in `routersystemmetrics` within ten minutes,
covering every router (`pe1`-`pe4`, `p1`-`p4`, `rr1`, `rr2`, all six CEs) with current timestamps
and no BigQuery errors in the worker-pool logs.

**The Dataflow path is retired, not parked.** The retry loop stopped when the CR was deleted (last
failed launch 06:59:49, nothing after), and the rest followed: the seven Config Connector resources
(`StorageBucket`, `IAMServiceAccount`, five `IAMPolicyMember`s for `dataflow-worker-sa` plus one on
the default compute SA) are deleted from the cluster, and `gcp/dataflow.tpl.yaml` and
`services/ingestion/` are out of the tree along with their `install.sh` wiring — `DeployIngestion()`
and its call site, two `envsubst` renders, the whole `--kill` teardown block, and the help text. Had
they stayed, `./install.sh --platform` would have recreated the CR and restarted the loop.

One thing worth recording from the teardown, because it will bite again: every one of those
`IAMPolicyMember`s was also `UpdateFailed`, so the Dataflow worker SA never actually held the roles
the template grants it. Even with the networking corrected, the job would have failed at the next
step. And `kubectl delete -f gcp/dataflow.yaml` without `-n automation` exits 0 having deleted
nothing — the manifests carry no namespace, so it silently targets the context default. The
`--kill` path in `install.sh` had exactly that bug.

*Housekeeping:* the probe row (`router_name = '__probe__'` in `routersystemmetrics`) could not be
deleted immediately — BigQuery refuses DML against rows still in the streaming buffer. It clears
after roughly 90 minutes. Deleted 2026-08-26; the table now holds only the eighteen real routers,
between 28k and 60k rows each.

**If the Dataflow path is ever wanted back** — it is the more scalable design, and this is recorded
so the reasoning does not have to be rebuilt — the minimum change is `networkRef: mgmt` plus
`subnetworkRef: regions/${GOOGLE_REGION}/subnetworks/mgmt-subnet` (Private Google Access is already
on for that subnet), `ipConfiguration: WORKER_IP_PRIVATE`, and a `maxWorkers` cap so a streaming
job cannot autoscale unattended. `metricscollector` must go to zero instances in the same change,
never alongside it.

---

### 7.5 `RouterRoutingMetrics` silently dropped most of its series ✅ *(done, 2026-08-26)*

The table was keyed on `(router_name, metric_name, vrf, afi, peer_ip, timestamp DESC)`. The FRR
exporter emits four more labels that vary independently of that key, so every series differing
only in one of them overwrote the others. `insert_or_update` is last-writer-wins, so there was no
error anywhere — just missing data.

Measured on the live database before the fix (387,090 rows in the table at the time):

| Metric | Should be | Actually stored | Lost dimension |
|---|---|---|---|
| `frr_ospf_neighbors`, `frr_ospf_neighbor_adjacencies` | one row per adjacency | 10 rows for 10 routers — `p1` kept only `eth5` despite ~5 adjacencies | `iface` (and `area`) |
| `frr_bgp_peer_*` (7 metrics) | `unicast` and `vpn` separately | one of the two | `safi` |
| `frr_collector_up` | one row per FRR sub-collector | only `collector=route` | `collector` |

This is why the dashboard's OSPF panel has been showing a single interface per router. The reader
was not at fault: `tools/metrics.py` already parsed `iface` out of the `labels` JSON, but the rows
it needed had been overwritten before they reached Spanner.

**Fixed as a new table, not a repair, because neither half of the obvious repair exists.** Spanner
cannot `ALTER` a primary key, and the Config Connector `ddl` list is append-only — editing a
statement in place is a silent no-op against a live database, and removing or reordering one makes
the bridge destroy and recreate the whole database. Dropping and recreating in place is legal as
appended DDL, but `RouterRoutingMetrics` is a `NODE TABLE` of `transportGraph`, so it would also
mean appending two hand-copied duplicates of the ~90-line property graph. Not worth the blast
radius for a table whose own retention policy is one day.

| Change | File |
|---|---|
| `CREATE TABLE RouterRoutingSeries` + index, appended; PK adds `safi`, `iface`, `area`, `collector` | `gcp/spanner.tpl.yaml` |
| Matching `routerroutingseries` sink (BigQuery never lost these — it has no primary key) | `gcp/bigquery.tpl.yaml` |
| Writes the ten key columns; retention `DELETE` re-targeted | `services/metricscollector/src/main.py` |
| `fetch_routing_metrics` self-join regrouped on all eight non-timestamp key columns | `dashboard-v2/tools/metrics.py` |
| `MAX`/`MIN` timestamp probes and the metrics `UNION ALL` re-targeted | `domains/transport/gnn/src/gnn/data/spanner.py` |
| Table reference, sample query, schema overview | `docs/spanner/*.md` |

`RouterRoutingMetrics` stays declared — it cannot be removed from an append-only list — but nothing
writes it, so its own `ROW DELETION POLICY (OLDER_THAN(timestamp, INTERVAL 1 DAY))` empties it
within a day. It is still in `clear_network_metrics()` so a stale install leaves nothing behind.

**The durable part of this fix is the detector, not the table.** `_warn_on_key_collisions()` in the
collector counts duplicate primary keys per poll and logs one example with its key columns. The
next label the exporter grows will show up as a log line instead of as a thinner graph. It is wired
on all three metric tables (`key_columns=4`, `4`, `10`). Verified against both shapes: the old
9-column tuple warns on three OSPF interfaces, the new 13-column tuple is silent.

**The rename has a deploy-ordering trap, and it caught us** *(2026-08-26)*. Renaming a table splits
the fix across two services: the collector starts writing `RouterRoutingSeries` (and, from 7.8a,
`TrafficFlowSeries`), while a dashboard still on the previous image keeps reading
`RouterRoutingMetrics` and `TrafficFlowMetrics`. Both old tables still exist and still answer, so
there is no error anywhere — they are simply frozen at the moment the collector was redeployed, and
every panel filters to a 120-second window ending now. The OSPF card went to `Neighbors 0`,
`Adjacencies 0` with no per-interface rows; the frozen tables were two and a half hours stale.

Redeploying the dashboard is the whole fix — the tree was already correct. Verified by running the
deployed `fetch_routing_metrics` against Spanner: `p1` returns 5 neighbours and 5 adjacencies across
`eth1`–`eth5`, and `pe1` returns six BGP peers including both the `safi=unicast` and the `safi=vpn`
session to each route reflector, which is exactly the pair the old narrow key used to collapse into
one row.

So: **redeploy every reader in the same pass as the collector.** `./install.sh --all` orders this
correctly; deploying `services/metricscollector` on its own does not, and fails silently when it
does not.

`SPANNER_BATCH_SIZE` drops from 3,000 to 2,000. The wider key costs ~24 mutations per row against
the 80,000-per-commit limit, and 3,000 would have left only 10% headroom.

*Found in passing:* `domains/transport/gnn/src/gnn/data/spanner.py` did not parse at all at `HEAD`
— the `TrafficFlowMetrics` loop body was left at its old indentation when the
`if self.service_database:` / `try:` / `with:` wrappers went in around it, so the whole GNN
snapshot loader was dead on import. Re-indented while re-targeting the table.

**Applied to the running lab, 2026-08-26.** The new table does not exist until
`kubectl apply -f gcp/spanner.yaml` (and the BigQuery equivalent) runs, and the collector must be
redeployed after that, not before — it will fail its routing writes against a table that is not
there yet. Order used: apply Spanner DDL → apply BigQuery → `cd services/metricscollector && ./install.sh`.

Before applying, the rendered `gcp/spanner.yaml` was diffed against the live `ddl` of **all six**
databases, not just transport — Config Connector treats the `ddl` list as append-only, so a removal
or a reorder anywhere in the file destroys and recreates that database. Result: transport +2,
service +1 (the `FlowHasMetrics_Edge` view from 7.1), the other four +0, zero prefix mismatches.
Both changed databases reconciled `Ready=True` / `UpToDate` immediately.

Verified against live data on revision `metricscollector-00003-g98`:

| Check | Before | After |
|---|---|---|
| OSPF neighbor rows stored for `p1` | 1 | 5 — `eth1`…`eth5` |
| `safi` values present | `unicast` only | `unicast` (3,212 rows) **and** `vpn` (1,452) |
| `collector` values present | `route` only | `bgp`, `bgp6`, `ospf`, `route`, `bfd` |

*Also fixed while applying:* `gcp/bigquery.tpl.yaml` pinned no `namespace:` on any of its six
resources, unlike `gcp/spanner.tpl.yaml`. `kubectl apply` therefore lands them in whichever
namespace the current context points at. Applying from a shell that had not selected `automation`
created a **second** set of Config Connector objects managing the same five BigQuery tables — two
controllers racing on one resource, where deleting either set takes the real table with it unless
it is annotated `cnrm.cloud.google.com/deletion-policy: abandon` first. That is how the duplicates
were unwound. All six resources now pin `namespace: ${GOOGLE_NAMESPACE}`, and both BigQuery render
sites in `install.sh` (in `--platform` and in the BigQuery deploy step) pass it to `envsubst`.

---

### 7.8 The collision detector immediately found two more of the same bug — both fixed ✅ *(done, 2026-08-26)*

The point of `_warn_on_key_collisions` (7.5) was to make the *next* missing key dimension show up
as a log line instead of as missing data. It did so on the first poll after deploy. Both findings
are the same class of defect as 7.5 — a dimension the exporter emits that is not in the primary
key — and both are confirmed against live data.

**a. `TrafficFlowMetrics` was missing `role`. This was the serious one.** ✅ *(fixed and applied to
the running lab, 2026-08-26.)* The key was `(flow_id, timestamp, node_name, metric_name)`. The
traffic agent reports every flow twice, once from each end, and `role` was extracted in `main.py`
and written into the `labels` JSON but never into the key — so the two ends collided and **exactly
half of all traffic metrics were discarded on write**: `160 primary keys have more than one row in
this poll, so 160 of 320 rows will be overwritten`, on every poll. Confirmed in BigQuery, which
keeps both because it has no key:

```
flow d2-hub-bidir-udp_d2-blue_rev, traffic_agent_throughput_bps, ts 15:42:59
  role=source       40,321,514 bps
  role=destination  44,094,183 bps
```

Those two numbers are precisely the pair you need to compute delivery ratio and loss for a flow,
and Spanner only ever stored one of them, non-deterministically.

**It was not a uniform 50% sample — the survivor alternated between the real reading and a
fiction.** Measured per role over ten minutes of live traffic:

| metric | `role=source` | `role=destination` | which end is right |
|---|---|---|---|
| `traffic_agent_throughput_sent_bps` | 11.6 Mbps | always 0 | source |
| `traffic_agent_throughput_recv_bps` | always 0 | 11.4 Mbps | destination |
| `traffic_agent_packet_loss_pct` | **50.0%** | 0.94% | destination |

A sender observes no loss, delay or jitter, so its `packet_loss_pct` is a fabricated ~50%, and that
metric feeds both the GNN anomaly features and the dashboard. Two downstream consequences fell out
of this: a false comment in `gnn/data/spanner.py` claiming `(flow_id, node_name, metric_name)`
already separated the ends — it does not, the collector derives `node_name` *from* `flow_id`, so
both ends carry the same one — and the 30-second bucketing in `dashboard-v2/tools/metrics.py`,
added to hide a "sawtooth, 0 on source, 100 on dest" that was this write-side collision all along.

Fixing it cost more than 7.5 did, because the table is a NODE TABLE of `serviceGraph` **and** the
target of `FlowHasMetrics_Edge`. Unlike the transport graph, though, the service DDL already uses
`CREATE OR REPLACE PROPERTY GRAPH`, so the graph could be re-pointed by appending rather than
duplicated. The edge keeps the label `FlowHasMetrics` — the relationship is unchanged, only its
destination table.

| Where | Change |
|---|---|
| `gcp/spanner.tpl.yaml` | New `TrafficFlowSeries` with `role` as key part 5, `TrafficFlowSeriesByTimestamp`, `FlowHasSeries_Edge`, and a re-pointed `CREATE OR REPLACE PROPERTY GRAPH serviceGraph` — all appended |
| `gcp/bigquery.tpl.yaml` | New `trafficflowseries` sink (the collector derives the BigQuery name from the Spanner one lowercased) |
| `services/metricscollector/src/main.py` | Writes `role` as a key column, `key_columns=5`; retention DML re-targeted |
| `gnn/data/spanner.py` | `GROUP BY` carries `role`; new `_FLOW_METRIC_ROLE` map takes each KPI from the end that measures it |
| `dashboard-v2/tools/metrics.py` | Reads `role` as a column instead of digging it out of `labels` |
| `mcp_server_for_ge/{re,ge}` | `_ROLE_FOR_METRIC` filter in `flows.py`; `role` → `roles` in `topology.py`. Series key stays `(flow_id, node_name)` so `directions` and the throughput sum do not double |
| docs | `table_reference.md`, `sample_queries.md`, `docs/network/metrics.md`, `docs/gnn/*`, `FEATURE_CALCULATION.md`, `domains/service/specs/TOPOLOGY.md` |

`TrafficFlowMetrics` and `trafficflowmetrics` are left in place for the history they hold; the
Spanner rows age out on the existing one-day deletion policy.

Rolled out in the order proven for 7.5 — Spanner DDL → BigQuery → collector. The DDL diff was
verified strictly append-only across all six databases first (`servicetopology-db` +4, everything
else byte-identical, zero prefix mismatches); `servicetopology-db` reconciled `Ready=True`/
`UpToDate`, `trafficflowseries` likewise, and the collector went to revision `metricscollector-00004-rb4`.
Verified on live data six polls later:

- 960 `source` rows and 960 `destination` rows — **both ends present for every
  `(flow_id, timestamp, node_name, metric_name)`**, where before one of each pair was dropped.
- `packet_loss_pct`: `destination` avg 0.395%, `source` avg 50.0% (max 100%) — the two are now
  stored separately instead of one silently standing in for the other.
- `throughput_sent_bps` non-zero only on `source`, `throughput_recv_bps` only on `destination`.
- The `TrafficFlowSeries` collision warning is gone from the logs: `Inserting 320 TrafficFlowSeries
  points in 1 Spanner batch(es)` with no `primary keys have more than one row` line. The only
  remaining warning was 7.8b's 54 rows on `RouterRoutingSeries`, now also closed.

**b. `RouterRoutingSeries` collided on three exporter self-metrics.** ✅ *(fixed and applied to the
running lab, 2026-08-26.)* 54 rows per poll, which is exactly 3 metrics × 18 routers:
`process_open_fds`, `process_network_receive_bytes_total`,
`process_network_transmit_bytes_total`. Each router is scraped on two ports — `:9100`
(node_exporter) and `:9101` (frr_exporter) — and both processes emit the standard client_golang
`process_*` series. The only thing distinguishing them is the Cloud Monitoring **resource** label
`instance`, and the collector reads `series.metric.labels` only, never `series.resource.labels`.

This was a misclassification rather than a key defect. The classifier was
`raw_metric_name.startswith("node_")` → system, *everything else* → routing, so exporter health
metrics landed in the routing table by default. They are not routing metrics, and nothing queried
them there — no GNN feature, no MCP tool, no dashboard panel — so they were **dropped from
`SELECTED_METRICS`** rather than given a key column. Exporter liveness is already reported by
`frr_collector_up`, which is kept.

The catch-all `else` was the actual defect, so it went too: the classifier is now `node_*` →
system, `frr_*` → routing, and **anything else is skipped with a warning** (once per metric name,
not once per 20-second poll). A metric with no table is now a log line instead of a row filed under
whichever branch happened to be last.

Verified live on revision `metricscollector-00005-9hr`: routing rows per poll fell **808 → 700**,
which is the full 108 those three metrics contributed (3 × 18 routers × 2 ports), not merely the 54
duplicates — the collided rows were being *written*, one of each pair discarded, so removing the
metric removes both. Across five consecutive polls **no table logs a collision warning**:
`RouterSystemMetrics` 1710, `RouterRoutingSeries` 700, `TrafficFlowSeries` 320, all clean. That
closes 7.5, 7.8a and 7.8b together — every metric the collector writes now has a primary key wide
enough to hold what the exporter emits.

---

### 7.6 The service installers die silently when there is no TTY ✅ *(done, 2026-08-26)*

Four installers had the same "should I rebuild this image?" block copied out, and the same defect
in each copy: a bare `read -p` under `set -euo pipefail`. With no TTY — CI, a backgrounded run,
`install.sh` from a pipeline — `read` returns 1 at EOF, `set -e` aborts, and the script exits with
**no output at all and no failing command to point at**. Reproduced against the old code: `exit=1`,
zero bytes on stdout and stderr. It reads as a hang followed by a silent death.

Fixed as one shared helper, `services/lib/build.sh`, rather than four corrected copies —
the five-divergent-copies-of-the-Gitea-URL-lookup lesson is already in this document and did not
need learning twice. Each installer sources it and its 10–12-line block collapses to three lines:
`services/gitea`, `services/gitea-webhook`, `services/metricscollector`, `services/sharedmcp`.

Decision table, unchanged except the new row:

| Condition | Result |
|---|---|
| image absent | build |
| `YES_FLAG=y` | build |
| `NO_FLAG=y` | skip, and say so |
| no TTY | **skip, and say so** (was: silent exit) |
| otherwise | ask |

Verified by driving all four states with `gcloud` stubbed and stdin on `/dev/null`; each prints its
reason and reaches the end of the script.

The earlier note in this file that `install.sh` "hardcodes `NO_FLAG=n`" was wrong — line 1390 sets
it as a default, which is correct. So was the suspicion that `if [[ … ]] && $(gcloud … >/dev/null
2>&1)` was an always-true guard: command substitution used as a command does propagate exit status
(`$(exit 5)` is non-zero). The `read` was the whole defect.

---

### 7.7 The vendored `agent_library` under each agent was not gitignored ✅ *(done, 2026-08-26)*

`deploy_agents.py` `copytree`s `lib/src/agent_library` into `<agent>/app/` and `rmtree`s it in a
`finally`. While a deploy is in flight it is an untracked directory in the working tree, and a
`git add -A` would commit it — the same trap the `mcp_server_for_ge` copies are already gitignored
against.

It was left alone before because of the `.gcloudignore` hazard CLAUDE.md warns about: for the MCP
servers, ignoring the vendored copy in `.gitignore` made gcloud synthesize a `.gcloudignore` from
it and drop the library from the upload, and the container then failed at import time. The
question was whether `agents-cli` does the same thing.

It does apply gitignore-style exclusion — but not to this file. `agents-cli` packages the upload
itself: `google/agents/cli/deploy/agent_runtime.py` calls `_packaged_files(Path.cwd())`, and
`_ignore_lines(root)` reads `root/.gcloudignore` if present, else `root/.gitignore`. `deploy_agents.py`
runs the CLI with `cwd=<agent>`, so `root` is the agent directory — which already has its own
`.gitignore`. **The repo-root `.gitignore` is never consulted**, so the entry is safe there and
only there. No per-agent `.gcloudignore` is needed.

Verified both directions by reimplementing the packager against the real `pathspec` and a stub
vendored copy:

- as-is → `app/agent_library/__init__.py` is packaged, `Dockerfile` present, `.venv` excluded
- with the pattern added to `<agent>/.gitignore` instead → `NONE`, i.e. exactly the breakage
  CLAUDE.md describes

`git check-ignore` confirms the new entry matches, and `git add -An agents-in-agent-runtime/` now
stages zero `agent_library` paths. The `.gitignore` comment says not to copy the lines into a
per-agent file.

---

## Risk register

| Risk | Phase | Mitigation |
|---|---|---|
| ADK 2.x breaks agent behaviour in ways tests do not catch | 3 | One agent at a time, cheapest first. Keep the current environment running until Phase 3's gate passes |
| Spanner query rewrite is larger than estimated | 2 | Still the dominant Phase 2 cost, but bounded: `services/supervisor/src/tools/` is the same code already re-targeted at the six-DB schema. Adapt it rather than authoring the rewrite |
| A cross-domain query is discovered mid-migration that the six-DB split cannot serve | 2 | No single-query join exists across databases. Fan out in the tool and join in Python, or denormalize the needed column. Surface these in 2.2.c before writing code, not after. **First concrete instance, 7.1:** device → CE-router attribution crosses infra and transport, so it is denormalized rather than joined |
| The `Device` → `PhysicalInterface` edge is gone — host-to-interface attribution breaks | 2 | `ComputeHost` in `infratopology-db` drops `interface_id` and `vlan`. Any tool that walked host→interface needs a different join key or an added column. Audit `devices.py` and `configdrift.py` for this specific traversal |
| `KgLogEntryNode` / `NodeEmbedding` fan-out returns partial results under a failing shard | 2 | Three and five databases respectively. Decide explicitly whether a failed shard is an error or a partial success, and log which shards answered |
| ~~`ge/` serves live topology and fault data on a public endpoint~~ **Closed, `ad73bd4b`** | 0.2 | ~~Accepted for now by explicit decision. Read-only, so exposure is disclosure not mutation.~~ **It was never read-only** — `tests.py` already exposed `runTest`/`deleteTest`, and 2.2.e widened it to `deployDescriptors`, `deleteDescriptor` and `create_device`/`delete_device`, i.e. anonymous write access to the GitOps repo the controller reconciles from. Closed on both axes: a shared bearer secret now gates every call, and the six write tools are not registered on `ge/` at all. **Residual:** one static, non-expiring credential in front of the read surface; rotation is a redeploy. See 0.2 |
| Reasoning Engine IDs churn, orphaning deployed engines | 3 | Preserve `deployment_metadata.json` per agent |
| Supervisor wired before peers have IDs | 3 | Ordering — supervisor is last, non-negotiable |
| REMOTE's core/radio handlers vanish under legacy startup | 1 | `main.py:78-93 legacy_import_modules()` does not import them; only `startup_coordinator.py:222-241` does. Pin `STARTUP_MODE` or fix the import |
| `-n` flag collision between installers | all | `-n` means "answer no" in REMOTE, "deploy agents" in LOCAL. Rename one before muscle memory causes damage |
| REMOTE `logcollector` silently drops entries on empty embeddings | 1 | Known defect; fix or keep our collector |
| ~~Dangling `trust-agent` reference~~ **closed** | 6 | REMOTE's `gitea-webhook` called a Cloud Run `trust-agent` that exists in neither tree. Re-pointed at the `trust` Reasoning Engine, read from `deployment_metadata.json` — see 6.1 |
| ~~`chaos` and `tester` locked to the Google Airlock registry~~ **closed** | 3 | Neither could have built on Cloud Run or Vertex AI — Airlock needs credentials the build does not have. Re-locked against public PyPI with no version changes, and `uv.toml` added to all eight components that lacked one so a bare `uv lock` cannot re-poison them — see 3.0 |
| Vendored `agent_library` drifts from its own `pyproject.toml` | 3 | `deploy_agents.py` copies `lib/src/agent_library` into `app/` rather than installing it, so `lib/pyproject.toml`'s dependency list is inert. Any new import in the library must be declared in **every** consuming agent's `pyproject.toml`, or it resolves only by luck of a transitive. Bit us once already in 3.0 |
| ~~**Gitea loses every repository whenever Cloud Run recycles the instance**~~ **Closed** | 6 → 7 | The service ran with no volume mount, so the SQLite database and all repos sat on the container's ephemeral filesystem. Observed on `an-gdemo`: after one idle night `network`, `root-repo` and `networkdesign` were all gone, both Config Sync reconcilers were crash-looping on a 404, and the trust webhook and API token were lost with the database. Nothing warned you — the deployed network keeps running off what the controller already reconciled, so the GitOps source of truth could be missing for hours before anyone noticed. **Fixed in 7.2:** Gitea is a GKE Deployment with `/data` on a 20Gi `reclaimPolicy: Retain` PVC, exposed on the reserved address `gitea-ip` over self-signed TLS; the Cloud Run service is deleted. gcsfuse was rejected outright (no POSIX locking — it would corrupt rather than fail) and Filestore on cost. Durability was verified by deleting the pod, not assumed. The `git bundle`-to-GCS stopgap is dropped as no longer load-bearing — see 2.3.a and 7.2 |
| ~~Devices are isolated nodes in `infraGraph` — no edge to the CE router serving them~~ **Closed** | 7 | Two hops, two fixes, both shipped. `ComputeHost` → `LinuxNetwork` is now a real edge (`ComputeHostOnNetwork`, via a view — the node table is keyed on `(id, valid_start_ts)`, so a bare `DESTINATION KEY (network_name)` was not expressible). `LinuxNetwork` → `PhysicalRouter` is genuinely cross-database and cannot be a graph edge under decision 0.1, so it is denormalized onto `ComputeHost.attached_router`, reusing the `host:{name}` logical-reference convention the schema already documents. `query_affected_flows` has dropped its Kubernetes join. **The find that mattered:** the Spanner client returns JSON arrays as `JsonObject`, a `dict` subclass, so `isinstance(x, list)` guards in `utils/hosts.py` had been discarding every `connected_routers` and `extra_interfaces` row silently — `hosts_on_router()` and `routers_for_hosts()` returned nothing and never raised — see 7.1 |
| ~~Five of nine Cloud Run services scale to the 100-instance default~~ **Closed** | 7 | The exposure was concurrency against shared backends that have no cap of their own — Spanner mutation limits, and Gitea, which is a strict single writer. Caps are now in the installers and applied live: `gitea-webhook` 3, `network-dashboard-v2` 5, `capture-log` 10, `sharedmcp` 10. The one residual — `gitea` capped at 1 in its installer but never applied, because the revision it would create is the event that wipes the repos — is gone too: 7.2 deleted the Cloud Run service, and single-writer is now structural (`replicas: 1` on an RWO PVC) rather than a scaling flag — see 7.3 |
| ~~Two implementations of the metrics poller, one of them permanently failing~~ **Closed** | 7 | `services/ingestion` (Dataflow) and `services/metricscollector` (Cloud Run worker pool) both polled Cloud Monitoring and wrote the same three Spanner tables; only the second one ran, which is why BigQuery had never received a row. The worker pool now carries the BigQuery sink and all three tables are filling, and the Dataflow path is gone from both the cluster and the tree. Worth remembering how this hid: 100 failed launcher VMs, a permanently `UpdateFailed` CR retrying every two minutes, and seven IAM bindings that had never applied — with nothing anywhere surfacing any of it — see 7.4 |
| ~~Five divergent copies of the Gitea helper~~ **Mostly closed, `b037a503`** | 6 | ~~Each of the last three Gitea defects had to be fixed in four or five places (6.1, 6.2, 6.3).~~ There were **seven**, and the miscount was itself part of the risk — two uncounted copies (`controller/`, `services/sharedmcp`) meant 6.2's sweep missed the controller entirely, leaving a `branch='master'` default live until 6.4. Four are now facades over `agent_library`. **Residual:** the two kept copies, for stated reasons (py-gitea 0.3.2 vs 0.2.10; `uv sync --frozen`), and the `re/`/`ge/` twins, which still differ only in `main.py` and must change together. See 6.4 |

**Rollback posture.** Phases 1-2 are additive on a branch of a fork — revert the branch. Phase 3 is
the point of no return only in the sense that the current environment must keep running in parallel
until its gate passes; do not decommission anything until then. Phase 4 is reversible until 4.2
deletes the old dashboard.

---

## Deliberately out of scope

- The `{core,radio,transport,infrastructure}` reshuffle of existing VyOS handlers — pure churn.
  `networkfailure/lifecycle_tasks.py` differs by exactly 4 lines, all license header.
- The `.j2` → `.tpl.yaml` conversion — ~14 files of churn, marginal benefit.
- Container `USER` directives, image digest pinning, TLS verification, CI/SAST. All are real gaps,
  all are equally absent from both trees, none blocks this migration. Track separately.
- Trimming the 17-role shared SA beyond removing `projectIamAdmin`.
