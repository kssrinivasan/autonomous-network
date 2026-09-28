# CLAUDE.md — AI Assistant Context for Claude

This file provides Claude with context about the **Autonomous Network Lab** repository to enable effective code assistance.

## Project Overview

**Autonomous Network Lab** is a sandbox demonstrating autonomous network lifecycle management. It uses Graph Neural Networks (GNNs) and AI agents to monitor, analyze, troubleshoot, and manage complex telecommunications networks. It includes a virtual network simulator with real-time topology understanding, automated fault detection, and intelligent incident resolution.

## Repository Structure

```
autonomous-network/
├── domains/                # Domain specifications and orchestrator agents
│   ├── transport/              # Transport domain (VyOS routers, underlay, L3VPN)
│   │   ├── agents/
│   │   │   └── transport-orchestrator/  # Transport domain orchestrator agent (ADK)
│   │   ├── specs/              # INVENTORY.md, TOPOLOGY.md, INTENT.md — schemas
│   │   ├── knowledge/          # designdoc.md — agent-facing reference material
│   │   ├── gnn/                # GNN model, features, local monitor
│   │   └── services/           # intentserver, topologyserver (Cloud Run)
│   ├── core/                   # Core domain (5G core network functions)
│   ├── radio/                  # Radio domain (RAN, gNodeB, UEs)
│   └── service/                # Service domain (end-to-end services)
│       # core/radio/service follow the same specs/ + knowledge/ layout;
│       # not every domain has all four subdirectories.
├── agents-in-agent-runtime/  # Agent fleet on Vertex AI Agent Runtime (Reasoning Engine)
│   ├── deploy_agents.py        # Orchestrates deployment + per-agent tool wiring
│   ├── supervisor/             # Incident management, dashboard backend (deploy LAST)
│   ├── designer/               # Writes CRs to the GitOps repo, on a review branch
│   ├── chaos/ detective/ impact/ tester/ trust/
│   └── AD_DEPLOY.md            # Deployment notes
├── mcp_server_for_ge/      # MCP tool servers — two deployments of one codebase
│   ├── re/                     # For Reasoning Engine agents: SSE, IAM + OIDC, writes enabled
│   ├── ge/                     # For Gemini Enterprise: streamable-HTTP, shared secret, read-only
│   └── README.md               # Read this before touching either — they must change together
├── lib/                    # agent_library — shared credentials, k8s, Gitea client, tracing
├── services/               # Supporting services (Python, Cloud Run/Functions)
│   ├── logcollector/           # Log collection service
│   ├── metricscollector/       # Metrics polling: Cloud Monitoring -> Spanner + BigQuery
│   ├── gitea/                  # Gitea (GKE Deployment + Retain PVC) with the design repos
│   ├── gitea-webhook/          # Gitea webhook handler
│   └── sharedmcp/              # MCP tool server (Python, Cloud Run)
│       └── src/
│           ├── main.py             # Tool server entrypoint
│           ├── tools/              # Individual MCP tools
│           └── utils/              # Shared utilities
├── controller/               # K8s controller for multi-domain network lifecycle management (Python)
│   ├── config/
│   │   ├── transport/          # VyOS CRDs: VyOSRouter, VyOSUnderlay, VyOSL3VPN
│   │   ├── core/               # 5G core CRDs: FiveGCore, NetworkFunction
│   │   ├── radio/              # RAN CRDs: CellSite
│   │   ├── infrastructure/     # Infra CRDs: Device, LinuxNetwork, TrafficTest, VyOSInfrastructure
│   │   ├── networkfailure.yaml # NetworkFailure CRD
│   │   └── vyosvm.yaml         # VyOSVM CRD
│   └── src/
│       ├── main.py             # Controller entrypoint (registers all domain handlers)
│       ├── transport/          # Transport domain: VyOS router/underlay/VPN lifecycle
│       │   ├── vyosrouter/         # VyOSRouter lifecycle
│       │   ├── vyosunderlay/       # VyOSUnderlay lifecycle
│       │   └── vyosvpn/            # VyOSL3VPN lifecycle
│       ├── core/               # Core domain: free5gc NF lifecycle
│       │   ├── core/               # FiveGCore handler (orchestrates NF child CRs)
│       │   └── xnf/                # NetworkFunction handler (AMF/SMF/UPF/NRF/etc.)
│       ├── radio/              # Radio domain: UERANSIM gNodeB + UE lifecycle (CellSite)
│       ├── infrastructure/     # Infrastructure domain: Linux networks, traffic tests, VyOS infra
│       │   ├── device/             # Device lifecycle
│       │   ├── linuxnetwork/       # LinuxNetwork lifecycle
│       │   ├── traffictest/        # TrafficTest lifecycle + port allocator
│       │   └── vyosinfrastructure/ # VyOSInfrastructure lifecycle
│       ├── vyosvm/             # VyOS VM provisioning lifecycle
│       ├── networkfailure/     # NetworkFailure injection lifecycle
│       ├── graph/              # Graph/topology management
│       └── utils/              # Shared utilities
├── traffic-agent/          # Network traffic generator & receiver (Go)
│   ├── cmd/agent/              # Binary entrypoint (daemon / run / serve modes)
│   ├── internal/
│   │   ├── api/                # HTTP/JSON REST control API (daemon mode)
│   │   ├── flowmanager/        # Flow lifecycle registry (start/stop/status)
│   │   ├── session/            # Concurrent TCP/UDP send-session pool
│   │   ├── server/             # TCP & UDP traffic receivers
│   │   ├── patterns/           # Traffic patterns (constant, burst, multisine, poisson, schedule)
│   │   ├── metrics/            # Thread-safe metrics collection & Snapshot
│   │   ├── bandwidth/          # Bandwidth string parser (e.g. "10Mbps")
│   │   ├── ratelimit/          # Token-bucket rate limiter + pattern controller
│   │   └── config/             # OneShotConfig & FlowRequest JSON schemas
│   ├── proto/agent.proto       # gRPC API definition (Phase 2)
│   └── tests/                  # Local integration tests
│       ├── test_local.sh           # Bash integration test runner (serve/run + daemon REST)
│       └── configs/                # One-shot JSON config fixtures
├── dashboard-v2/           # Web dashboard (Flutter/Dart) — the tracked dashboard
├── examples/               # Example CRs: multidomain networks, failure tests, intents
├── gcp/                    # GCP infrastructure manifests
│   ├── spanner.tpl.yaml        # Spanner database setup (source of truth — edit this)
│   ├── spanner.yaml            # Rendered output (generated via envsubst from tpl.yaml)
│   ├── configconnector.tpl.yaml
│   ├── networkvm.yaml          # Network simulator VM
│   └── free5gc/                # 5G core network config
├── docs/                   # Documentation and architecture diagrams
│   ├── automation/             # Domain-specific automation docs (transport, core, radio, infrastructure, supporting)
│   └── ...                     # Other documentation
├── install.sh              # Main installation/management script
├── config.sh               # Environment variable setup
├── MIGRATION_PLAN.md       # Phased plan + risk register for the migration/an-base work
└── INSTALL.md              # Full installation guide
```

**Untracked directories you may see on disk.** `operator/`, `networkagents/`, `logservices/`,
`dashboard/` and `dashboard-next/` contain no tracked files — they are local leftovers from earlier
layouts or build output. `operator/` became `controller/`, `logservices/` became `services/`,
`networkagents/` became `agents-in-agent-runtime/`, and `dashboard/` became `dashboard-v2/`. The
same applies inside `agents-in-agent-runtime/`: `chaosagent/`, `designeragent/`, `impactagent/`,
`logsagent/`, `supervisoragent/` and `testeragent/` are pre-rename scaffolds with nothing tracked
in them — the live agents are the clean-noun directories (`chaos/`, `designer/`, `detective/`,
`impact/`, `supervisor/`, `tester/`, `trust/`). Do not edit any of these and do not treat them as
sources of truth.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language (backend) | Python 3.13 |
| Language (traffic agent) | Go 1.22+ |
| Language (frontend) | Flutter / Dart |
| Cloud Platform | Google Cloud Platform (GCP) |
| Container Orchestration | GKE (Google Kubernetes Engine) |
| Database | Google Cloud Spanner |
| ML Platform | Vertex AI |
| GNN Framework | JAX / PyTorch / TensorFlow (see `docs/gnn/examples/`) |
| Network Simulator | VyOS (transport), free5gc (5G core), UERANSIM (RAN) |
| Agent Protocol | A2A (Agent-to-Agent) |
| Tool Protocol | MCP (Model Context Protocol) |
| Infrastructure | Config Connector, Config Sync, Ansible |
| CI/CD | Cloud Build (`.tpl.yaml` templates → `cloudbuild.yaml` via `envsubst`) |
| Serving | Google Cloud Run |

## Key Commands

### Environment Setup
```bash
# Edit and source environment variables before anything else
vi config.sh
source ./config.sh
```

### Installation
```bash
# Full install (recommended)
./install.sh --all -y

# Step-by-step
./install.sh --platform  # Create platform infrastructure (auto-creates environment, deploys GKE, Spanner, controller, logs, metrics)
./install.sh --agents    # Deploy Gitea, agents, dashboard
./install.sh --gnn       # Deploy GNN (optional)
```

### Selective Deployment
```bash
# Deploy individual components by running their install scripts
cd controller && ./install.sh
cd services/logcollector && ./install.sh
cd services/metricscollector && ./install.sh
cd services/gitea && ./install.sh

# The dashboard has no component-local install script — it is driven from the root
./install.sh --dashboard

# Deploy domain orchestrator agents
# Transport is the only domain with a real orchestrator agent. The core, radio and
# service orchestrators were the unmodified ADK starter sample and have been deleted —
# there is nothing under domains/{core,radio,service}/agents/ to deploy.
cd domains/transport/agents && ./install.sh

# Deploy the agent fleet to Vertex AI Agent Runtime
cd agents-in-agent-runtime && python3 deploy_agents.py   # supervisor must be last

# Wire the Gitea -> trust review loop. Must run after deploy_agents.py (it reads the
# trust engine id from trust/deployment_metadata.json) and after Gitea exists. Since
# Gitea moved to GKE with a persistent disk, the hook and the API token survive a
# redeploy, so this is normally a one-time step.
# Note: the root wrapper exports NO_FLAG="n", so with the image already built it dies
# on an interactive prompt. Run the component installer directly instead:
YES_FLAG=y bash services/gitea-webhook/install.sh

# Deploy the MCP tool servers (both twins, or one at a time)
cd mcp_server_for_ge && ./deploy.sh
```

### Environment Info
```bash
./install.sh --env     # Show active GCP environment
./install.sh --info    # Show deployed service URLs
```

### Cleanup
```bash
./install.sh --kill    # Stop and delete runtime (keeps config)
./install.sh --delete  # Delete environment config
./install.sh --kill; ./install.sh --delete   # Full cleanup
```

### traffic-agent (Go) — Local Development

```bash
cd traffic-agent

# Build for the current OS/arch (required before running tests)
make build-local

# Run the full integration test suite (build + test)
./tests/test_local.sh

# Skip rebuild if binary is already up to date
SKIP_BUILD=1 ./tests/test_local.sh

# Run Go unit tests
make test

# One-shot server mode (replaces iperf3 -s)
./traffic-agent serve --port 5201 --protocol TCP --duration 60

# One-shot client mode (replaces traffic_generator.py)
./traffic-agent run --config path/to/config.json

# Daemon mode (long-lived HTTP agent on :9090)
./traffic-agent daemon --control-port 9090

# Daemon REST API examples
curl http://localhost:9090/v1/health
curl -X POST http://localhost:9090/v1/flows \
  -H 'Content-Type: application/json' \
  -d '{"flow_id":"f1","role":"destination","port":5201,"protocol":"TCP","duration_sec":30}'
curl http://localhost:9090/v1/flows/f1
curl -X DELETE http://localhost:9090/v1/flows/f1
```

**traffic-agent modes:**

| Mode | Description | Equivalent |
|------|-------------|------------|
| `daemon` | Long-lived HTTP agent; operator POSTs flows via REST | Phase 2 container mode |
| `run` | One-shot source; reads JSON config, sends traffic, outputs JSON result | Replaces `traffic_generator.py` |
| `serve` | One-shot receiver; listens for traffic then exits | Replaces `iperf3 -s` |

**Key packages:**
- `internal/flowmanager` — thread-safe flow registry; each flow runs as a goroutine in `source` or `destination` role
- `internal/patterns` — pluggable traffic shape: `constant`, `burst`, `multisine`, `periodic`, `poisson`, `schedule`
- `internal/metrics` — atomic counters → `Snapshot` (snake_case JSON tags, consistent across REST and one-shot output)
- `internal/session` — manages pools of concurrent TCP/UDP connections with shared token-bucket rate limiter

## Development Conventions

- **Cloud Build**: Each deployable component has a `cloudbuild.tpl.yaml` template (with `${VAR}` placeholders) that is rendered into a `cloudbuild.yaml` at deploy time via `envsubst` using environment variables from `config.sh`. Edit `.tpl.yaml` files directly — `.yaml` files are generated outputs. There are no `.j2` Jinja2 templates; Ansible playbook templates (`.yaml.j2`, `.json.j2`, `*.j2` under `playbooks/templates/`) are the only remaining Jinja2 files and are used directly by Ansible.
- **Dockerfiles**: Each component has its own `Dockerfile` at the component root.
- **Python Dependencies**: This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Each Python component has:
  - `pyproject.toml` - Defines dependencies and project metadata
  - `uv.lock` - Locked dependency versions for reproducible builds (regenerate with `uv lock --default-index https://pypi.org/simple`)
  - `uv.toml` - Overrides system-level uv config to force public PyPI instead of Google Airlock registry (required on Google workstations)
  - **Important**: All `uv.lock` files must use public PyPI (`https://pypi.org/simple`), NOT the Google internal Airlock registry (`us-python.pkg.dev`), as the latter requires authentication and fails during Cloud Run/Vertex AI builds.
- **Domain Orchestrator Agents**: Only the transport domain has one — `domains/transport/agents/transport-orchestrator/`, built with Google ADK (Agent Development Kit), using the ADK project structure with `app/agent.py`, `deployment_metadata.json`, `agents-cli-manifest.yaml`, and `pyproject.toml`/`uv.lock`/`uv.toml`. The core, radio and service domains have `specs/` and `knowledge/` but no agent; adding one means adding the domain to the loop in `install.sh`'s `DeployAgents`.
- **Services**: Supporting services in `services/` (logcollector, metricscollector, gitea, gitea-webhook, sharedmcp) each have their own `install.sh`/`uninstall.sh` scripts for deployment.
- **The `re`/`ge` MCP twins are one codebase in two directories.** Every file under
  `mcp_server_for_ge/{re,ge}/src/tools/` and `src/utils/` is byte-identical; only `src/main.py`
  differs (SSE vs streamable-HTTP). Any change to a shared file must be copied across, and verified
  with `diff -rq re/src ge/src -x __pycache__ -x agent_library`. Their security postures differ only
  by environment variable — see [mcp_server_for_ge/README.md](mcp_server_for_ge/README.md).
- **Gitea access goes through `agent_library`.** `lib/src/agent_library/gitea_utils.py` is the
  shared client; do not add another copy. Two consumers vendor it at deploy time rather than
  pip-installing it (`deploy_agents.py` for the agents, `deploy.sh` for the MCP servers) because
  `lib/` is outside their build context. Those vendored copies are gitignored and must never be
  edited or committed, and each vendoring component needs a `.gcloudignore` or gcloud will
  synthesize one from `.gitignore` and drop the library from the upload. See
  [lib/README.md](lib/README.md).
- **Gitea runs on GKE, and its URL has exactly one owner.** It is a `Deployment` (`replicas: 1`,
  `strategy: Recreate`) with `/data` on a `reclaimPolicy: Retain` PVC, fronted by an L4
  `Service type=LoadBalancer` on the reserved regional address `gitea-ip`, serving HTTPS with a
  self-signed certificate — so every client passes `verify=False` / `-k` / `noSSLVerify: true`.
  Shell callers must get the URL from `services/gitea/gitea-url.sh`; Python callers read
  `GITEA_URL` and fall back to the Compute API (never `gcloud` — it is not in those images). Do not
  add a seventh copy of the lookup. See [services/gitea/Readme.md](services/gitea/Readme.md).
- **Config files**: Operator configuration YAML files live in `controller/config/`, organized by domain (`transport/`, `core/`, `radio/`, `infrastructure/`).
- **Operator domains**: The operator manages multiple network domains as separate Kopf handler domains — transport (VyOS router/underlay/VPN, organized by component under `vyosrouter/`, `vyosunderlay/`, `vyosvpn/`), core (free5gc 5G NFs via `FiveGCore` → `NetworkFunction` CRDs), radio (UERANSIM gNodeB + UEs via `CellSite`), infrastructure (Device, LinuxNetwork, TrafficTest, VyOSInfrastructure), plus `vyosvm` (VM provisioning), `networkfailure` (fault injection), `opsagent` (Ops Agent scrape config on the network VM) and `graph` (Spanner topology sync). Each domain has its own `src/<domain>/lifecycle.py` and optional `lifecycle_tasks.py` + `playbooks/`.

## Required GCP Environment Variables

```bash
# Core GCP configuration
export GOOGLE_PROJECT=<project-id>
export GOOGLE_USER=<user@domain.com>
export GOOGLE_VM_USER=<gce-vm-username>
export GOOGLE_REGION=<region>        # e.g. europe-west1
export GOOGLE_ZONE=<zone>            # e.g. europe-west1-c
export WEBAPPS_LOGIN=<login>
export WEBAPPS_PWD=<password>

# Spanner database names (optional overrides — install.sh sets defaults automatically)
export GOOGLE_SPANNER_TRANSPORT_DATABASE=transporttopology-db
export GOOGLE_SPANNER_INFRA_DATABASE=infratopology-db
export GOOGLE_SPANNER_CORE_DATABASE=coretopology-db
export GOOGLE_SPANNER_RADIO_DATABASE=radiotopology-db
export GOOGLE_SPANNER_SERVICE_DATABASE=servicetopology-db
export GOOGLE_SPANNER_OPS_DATABASE=agentops-db
```

## GCP Prerequisites

- Owner role on the GCP project
- Organization policies configured:
  - `compute.vmExternalIpAccess` → Allow All
  - `compute.requireShieldedVm` → Off
  - `iam.disableServiceAccountKeyCreation` → Off
  - `compute.vmCanIpForward` → Allow All
  - `iam.allowedPolicyMemberDomains` → Allow All

## Key Documentation

### Architecture & Design
- [Architecture Overview](docs/drawings/architecture.drawio.svg)
- [Network Simulator](docs/network/Readme.md)
- [GNN Models](docs/gnn/Readme.md)
- [Spanner Schema](docs/spanner/Readme.md)
- [Agents](docs/agents/Readme.md)
- [IAM & Service Account Strategy](docs/IAM_STRATEGY.md)
- [Security Model](docs/SECURITY.md)
- [MCP Tool Servers](mcp_server_for_ge/README.md) — the `re`/`ge` twin layout and its auth model
- [Shared Agent Library](lib/README.md) — the Gitea client and the vendoring contract
- [Migration Plan](MIGRATION_PLAN.md) — phases, decisions and risk register for `migration/an-base`

### Automation Documentation
- [Automation Overview](docs/automation/Readme.md)
- [Transport Domain Automation](docs/automation/transport.md)
- [Core Domain Automation](docs/automation/core.md)
- [Radio Domain Automation](docs/automation/radio.md)
- [Infrastructure Automation](docs/automation/infrastructure.md)
- [Supporting Services Automation](docs/automation/supporting.md)

### Domain Specifications

Schemas live under `domains/<domain>/specs/`, agent-facing reference material under
`domains/<domain>/knowledge/`. Not every domain has every document.

- **Transport**: [Overview](domains/transport/Readme.md) | [Inventory](domains/transport/specs/INVENTORY.md) | [Topology](domains/transport/specs/TOPOLOGY.md) | [Intent](domains/transport/specs/INTENT.md) | [Design doc](domains/transport/knowledge/designdoc.md)
- **Core**: [Overview](domains/core/Readme.md) | [Inventory](domains/core/specs/INVENTORY.md) | [Topology](domains/core/specs/TOPOLOGY.md) | [Intent](domains/core/specs/INTENT.md) | [Orchestrator](domains/core/knowledge/ORCHESTRATOR.md)
- **Radio**: [Overview](domains/radio/Readme.md) | [Inventory](domains/radio/specs/INVENTORY.md) | [Topology](domains/radio/specs/TOPOLOGY.md) | [Intent](domains/radio/specs/INTENT.md) | [Orchestrator](domains/radio/knowledge/ORCHESTRATOR.md)
- **Service**: [Overview](domains/service/Readme.md) | [Inventory](domains/service/specs/INVENTORY.md) | [Topology](domains/service/specs/TOPOLOGY.md) | [Intent](domains/service/specs/INTENT.md) | [Events](domains/service/specs/EVENTS.md) | [Orchestrator](domains/service/knowledge/ORCHESTRATOR.md)

### Scenarios
- [Transport RCA Scenario](docs/scenarios/transport.md)
