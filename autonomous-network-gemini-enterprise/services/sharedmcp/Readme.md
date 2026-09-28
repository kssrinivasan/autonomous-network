# Shared MCP Server

An MCP (Model Context Protocol) tool server deployed on Cloud Run that exposes network operations to domain orchestrator agents. All ADK agents in the lab connect to this server via `SHARED_MCP_URL` to read topology, query devices, and deploy network descriptors.

## Tools

### Transport (`src/tools/transport.py`)

| Tool | Description |
|------|-------------|
| `getTransportCRDs` | Returns the VyOS CRD schemas (VyOSRouter, VyOSUnderlay, VyOSL3VPN) |
| `getTransportDesignDoc` | Returns the transport domain design document |
| `getDeployedTransportCRs` | Lists currently deployed transport Custom Resources from the cluster |

### Core (`src/tools/core.py`)

| Tool | Description |
|------|-------------|
| `CRDs` | Returns the 5G core CRD schemas (FiveGCore, NetworkFunction) |

### Radio (`src/tools/radio.py`)

| Tool | Description |
|------|-------------|
| `getRadioCRDs` | Returns the RAN CRD schemas (CellSite) |

### Devices (`src/tools/devices.py`)

| Tool | Description |
|------|-------------|
| `getDeviceDefinition` | Returns the Device CRD schema |
| `getDevices` | Lists all devices in the cluster |
| `getDeviceByName` | Returns a specific device by name |
| `searchDevices` | Searches devices by field criteria |

### Gitea (`src/tools/gitea.py`)

| Tool | Description |
|------|-------------|
| `deployDescriptors` | Pushes one or more Kubernetes CR YAML descriptors to the Gitea `network` repository (used by orchestrator agents to deploy network changes) |

## Project Structure

```
sharedmcp/
├── src/
│   ├── main.py             # uvicorn entrypoint; loads tools and starts MCP server
│   ├── networkagent.json   # Service account credentials (local dev only)
│   └── tools/
│       ├── transport.py    # Transport domain tools
│       ├── core.py         # Core domain tools
│       ├── radio.py        # Radio domain tools
│       ├── devices.py      # Device tools
│       └── gitea.py        # Gitea deployment tools
│   └── utils/
│       ├── auth.py         # OIDC inbound auth middleware
│       ├── creds.py        # GCP credential helpers
│       ├── gitea_utils.py  # Gitea API helpers
│       ├── globals.py      # Shared MCP server instance
│       └── k8s.py          # Kubernetes client helpers
├── .env.example            # Environment variable reference
├── Dockerfile
├── cloudbuild.tpl.yaml     # Cloud Build template
├── install.sh
└── uninstall.sh
```

## Local Development

```bash
# Install dependencies
uv sync

# Copy and configure env vars
cp .env.example .env
# Edit .env — set GOOGLE_PROJECT, GITEA_URL, WEBAPPS_LOGIN, WEBAPPS_PWD

# Run the server
uv run python src/main.py
```

The server listens on `PORT` (default `8080`). Leave `MCP_AUDIENCE` unset locally to disable OIDC auth.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_PROJECT` | Yes | GCP project ID |
| `GOOGLE_PROJECT_NUMBER` | Yes | GCP project number (used for OIDC allowlist) |
| `GITEA_URL` | Yes | Gitea base URL, `https://<ip>` — it runs on GKE behind the reserved address `gitea-ip`, with a self-signed certificate |
| `WEBAPPS_LOGIN` | Yes | Gitea admin username |
| `WEBAPPS_PWD` | Yes | Gitea admin password |
| `MCP_AUDIENCE` | No | Cloud Run URL of this service — enables OIDC token validation when set |
| `PORT` | No | HTTP port (default: `8080`) |

See [`.env.example`](.env.example) for full details.

## Deployment

```bash
./install.sh
```

Builds the image via Cloud Build and deploys to Cloud Run. After deploy, set `SHARED_MCP_URL` in each agent's environment to point at the resulting service URL.

```bash
./uninstall.sh
```
