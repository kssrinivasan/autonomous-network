# transport-orchestrator

An autonomous transport domain orchestrator agent built with [Google ADK](https://adk.dev/). It accepts TMF921 `L3ConnectivityIntent` requests via the A2A protocol and translates them into VyOS Kubernetes Custom Resources deployed via Gitea.

## What It Does

The agent runs a sequential four-stage pipeline to provision or modify L3VPN services:

| Stage | Subagent | Responsibility |
|-------|----------|----------------|
| 1 | `validate_intent` | Validates the TMF921 intent schema and checks infrastructure constraints (endpoint count, topology type, required fields) |
| 2 | `planner` | Decomposes the validated intent into a structured network plan using a 9-phase decomposition algorithm over VyOS CRD types (VyOSInfrastructure, VyOSUnderlay, VyOSL3VPN) |
| 3 | `designer` | Transforms the network plan into complete VyOSL3VPN Kubernetes Custom Resource YAML, merging VRF and BGP configs per router |
| 4 | `deploy_crs` | Deploys the approved YAML descriptors to Gitea and returns a structured `CreateIntentResponse` |

## Project Structure

```
transport-orchestrator/
├── app/
│   ├── agent.py               # Root SequentialAgent wiring all four subagents
│   ├── fast_api_app.py        # FastAPI / A2A server entrypoint
│   ├── config.py              # App configuration (project, region, MCP URL, etc.)
│   ├── mcp_auth.py            # MCP authentication helpers
│   ├── subagents/             # Four pipeline subagents (validate_intent, planner, designer, deploy_crs)
│   └── utils/                 # Shared utilities (spanner_client, intent_ops_client, etc.)
├── tests/                     # Unit, integration, eval, and load tests
├── agents-cli-manifest.yaml   # agents-cli deployment manifest
├── deployment_metadata.json   # Agent Runtime deployment metadata
├── Dockerfile                 # Container image
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Python dependencies (managed with uv)
```

## Requirements

- **uv** — Python package manager: [install](https://docs.astral.sh/uv/getting-started/installation/)
- **agents-cli** — `uv tool install google-agents-cli`
- **Google Cloud SDK** — for GCP services

## Quick Start

```bash
# Install agents-cli skills (first time only)
uvx google-agents-cli setup

# Install dependencies
agents-cli install

# Launch interactive playground (auto-reloads on save)
agents-cli playground
```

## Running Locally

```bash
export GOOGLE_CLOUD_PROJECT=<your-project-id>
export GOOGLE_CLOUD_LOCATION=<region>        # e.g. europe-west1
export SHARED_MCP_URL=<shared-mcp-cloud-run-url>

uv run uvicorn app.fast_api_app:app --reload --host 0.0.0.0 --port 8080
```

```bash
# Health check
curl http://localhost:8080/health

# Send an intent
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role": "user",
      "content": "{\"intent\": {\"@type\": \"Intent\", \"name\": \"vpn-svc-1\", \"intentExpression\": {...}}, \"namespace\": \"default\"}"
    }]
  }'
```

## Commands

| Command | Description |
|---------|-------------|
| `agents-cli install` | Install dependencies |
| `agents-cli playground` | Launch local development UI |
| `agents-cli lint` | Run code quality checks |
| `agents-cli eval` | Evaluate agent behavior |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli deploy` | Deploy to Agent Runtime |
| `agents-cli publish gemini-enterprise` | Register with Gemini Enterprise |

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

Or use the top-level install script:

```bash
cd domains/transport/agents && ./install.sh
```

## Observability

Telemetry exports to Cloud Trace, BigQuery, and Cloud Logging. Set `DEBUG=true` to enable verbose per-turn logging via `DevLoggerPlugin`.
