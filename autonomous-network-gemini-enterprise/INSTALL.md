# Network Agent Installation Guide

This guide describes how to set up the Network Agent GCP environment.

## Quick Start

**First-time setup:**

1. **Configure your environment** (only needed once):
   ```bash
   # The install script will auto-create config.sh from template on first run
   ./install.sh --all
   
   # This will exit with instructions to edit config.sh
   # Edit the file with your GCP credentials:
   vi config.sh
   ```

2. **Run the installation:**
   ```bash
   # install.sh automatically sources config.sh, so just re-run it:
   ./install.sh --all
   ```

**Note:** You only need to manually `source ./config.sh` if you want the variables available in your current shell (e.g., for running `gcloud` commands). The install script sources it automatically.

The first time installation takes roughly 30-40 minutes.

At the end of the installation you'll see a recap of the key URLs to access the demo, most notably the dashboard and the gitea server (git repo used for network gitops).

**How config.sh works:**
- `config.sh.template` is tracked in git with placeholder values
- On first run, `install.sh` automatically copies it to `config.sh`
- You edit `config.sh` with your actual credentials (never committed to git)
- All scripts source `config.sh` to get environment variables

For your information, the full installation process goes through the following steps:
- Check that your GCP environment is set properly (project, org policies, permissions, APIs,...)
- Create the demo environment configuration if needed
- Start the runtime services
- Deploy all network agents and dashboard

## Prerequisites

The following packages are required before proceeding with the installation:

* [Google Cloud Command Line interface](https://cloud.google.com/sdk/docs/install)
* kubectl (on Debian: `sudo apt-get install kubectl`)
* Python3 pip installer (on Debian: `sudo apt-get install python3-pip`)
* jinja templating engine (`pip install jinja-cli`)
* ansible (`pip install ansible`)
* [flutter sdk](https://flutter.dev/)

**Note:** It is recommended to create your own [Python virtual environment](https://docs.python.org/3/library/venv.html) first prior to installing jinja or any other python packages. We recommend using Python 3.13.

### Update Organization Policies

The install script will do the GCP org policies check for you, but you may want to ensure the organization policy values below are set as follows:

* Set **constraints/compute.requireShieldedVm** to **Off**
* Set **constraints/iam.disableServiceAccountKeyCreation** to **Off**
* Set **constraints/compute.vmCanIpForward** to **Allow All**
* Set **constraints/iam.allowedPolicyMemberDomains** to **Allow All**

## Environment Setup

### Setup gcloud

[Install](https://cloud.google.com/sdk/docs/install) and initialize gcloud:

```bash
gcloud init --no-launch-browser
```

### Setup GCP Environment Variables

The project uses `config.sh` as the master configuration file. On first run, `install.sh` will automatically create `config.sh` from the template.

**Manual setup (optional):**

If you prefer to set up config.sh before running install.sh:

```bash
# Copy the template
cp config.sh.template config.sh

# Edit with your values
vi config.sh
```

**Required variables in config.sh:**

Edit `config.sh` and set these variables to match your GCP environment:

```bash
export GOOGLE_PROJECT="your-project-id"       # Your GCP project ID (must be created first)
export GOOGLE_USER="user@domain.com"          # Your GCP email (must be project owner)
export GOOGLE_VM_USER="user_domain_com"       # GCE VM username (run 'whoami' on a test VM to find this)
export GOOGLE_REGION="us-central1"            # GCP region (e.g., us-central1, europe-west1)
export GOOGLE_ZONE="us-central1-a"            # GCP zone (e.g., us-central1-a, europe-west1-b)
export WEBAPPS_LOGIN="admin"                  # Login for web UIs (dashboard, gitea)
export WEBAPPS_PWD="your-secure-password"     # Password for web UIs
```

**After editing config.sh:**

You can verify your configuration is correct by sourcing it and checking a variable:

```bash
source ./config.sh
echo $GOOGLE_PROJECT
```

However, this is **optional** - install.sh will automatically source config.sh when you run it.

**Important:** Never commit `config.sh` to git - it contains credentials. The file is already in `.gitignore`.

## Installation Options

The **install.sh** script provides simplified installation options:

```bash
Network Agent environment manager

Syntax: install.sh [COMMAND] [FLAGS]

DEPLOYMENT COMMANDS:
  --platform    Create and start platform infrastructure
                (auto-creates environment, deploys GKE, Spanner, controller, logs, metrics)

  --agents      Deploy Gitea, the MCP tools, the domain agent, the webhook and the dashboard
                (requires platform; the Vertex AI agent fleet is a separate step, see below)

  --gnn         Deploy GNN (Graph Neural Network)
                (requires platform)

  --all         Complete installation
                (platform + agents)

MANAGEMENT COMMANDS:
  -k, --kill    Stop and delete runtime (GKE, VMs, databases)
  -d, --delete  Delete environment files (keys, manifests, networkagent.json)

INFORMATION COMMANDS:
  -g, --env     Display active GCP environment info
  -i, --info    Display deployed service URLs and credentials
  -h, --help    Display this help message

FLAGS:
  -y, --yes     Answer 'yes' to all prompts (automation mode)
  -n, --no      Answer 'no' to all prompts (skip rebuilds)

EXAMPLES:
  ./install.sh --all                  # Install everything from scratch
  ./install.sh --all -y               # Install everything, no prompts
  ./install.sh --platform             # Create platform only
  ./install.sh --agents               # Deploy agents only (platform must exist)
  ./install.sh --gnn                  # Deploy GNN (platform must exist)
  ./install.sh --kill; ./install.sh --platform   # Rebuild platform
  ./install.sh --dashboard            # Redeploy dashboard only (no component-local script)
  cd controller && ./install.sh       # Redeploy controller only

COMPONENT DEPLOYMENT:
  To deploy individual components, run their install.sh scripts:
    cd controller && ./install.sh
    cd services/logcollector && ./install.sh
    cd services/metricscollector && ./install.sh
    cd services/gitea && ./install.sh
    cd services/sharedmcp && ./install.sh
    ./install.sh --dashboard            # dashboard-v2 is driven from the repo root
    cd domains/transport/agents && ./install.sh

```

## Installation Workflows

### Simple Installation (Recommended)

For most users, the comprehensive installation is the easiest approach:

```bash
# First-time: install.sh will create config.sh from template and exit
./install.sh --all

# Edit config.sh with your GCP credentials
vi config.sh

# Re-run the installation (config.sh is sourced automatically)
./install.sh --all -y
```

### Step-by-Step Installation

If you prefer more control over the installation process:

1. **Deploy the platform infrastructure:**
   ```bash
   ./install.sh --platform
   ```
   This automatically creates the environment (if needed) and deploys GKE, Spanner, controller, logs, and metrics.

2. **Deploy all Network Agents and Dashboard:**
   ```bash
   ./install.sh --agents
   ```

3. **Deploy the agent fleet to Vertex AI Agent Runtime:**
   ```bash
   cd agents-in-agent-runtime && python3 deploy_agents.py
   ```
   This is a separate step from `--agents`, which deploys the Cloud Run side. The supervisor
   must be deployed last — `deploy_agents.py` handles the ordering. See
   [agents-in-agent-runtime/AD_DEPLOY.md](agents-in-agent-runtime/AD_DEPLOY.md).

4. **Wire the Gitea → trust review loop** (once the fleet is up, since it reads the trust
   agent's engine id):
   ```bash
   YES_FLAG=y bash services/gitea-webhook/install.sh
   ```

5. **Optionally deploy GNN:**
   ```bash
   ./install.sh --gnn
   ```

### Selective Component Deployment

You can deploy individual components by running their install scripts directly:

```bash
# Deploy only the dashboard (dashboard-v2 has no component-local install.sh)
./install.sh --dashboard

# Deploy Gitea
cd services/gitea && ./install.sh

# Deploy shared MCP tools
cd services/sharedmcp && ./install.sh

# Deploy the transport domain orchestrator agent.
# Transport is the only domain with a real agent — the core, radio and service
# orchestrators were the unmodified ADK starter sample and have been deleted.
cd domains/transport/agents && ./install.sh

# Deploy controller
cd controller && ./install.sh
```

### Automated Installation

For CI/CD or automated deployments, use the confirmation flags:

```bash
# Answer 'yes' to all prompts automatically (always rebuild)
./install.sh --all -y

# Answer 'no' to all prompts (skip rebuilds if images exist)
./install.sh --all -n
```

## Environment Management

### Viewing Environment Information

```bash
# Display current GCP environment details
./install.sh --env

# Display demo information and URLs
./install.sh --info
```

### Rebuilding Components

```bash
# Rebuild entire platform (GKE, Spanner, controller, logs, metrics)
./install.sh --kill
./install.sh --platform

# Rebuild all agents and dashboard
./install.sh --agents

# Rebuild GNN
./install.sh --gnn

# Rebuild individual components
cd controller && ./install.sh
cd services/logcollector && ./install.sh
cd services/metricscollector && ./install.sh
cd services/gitea && ./install.sh
./install.sh --dashboard
```

### Clean Up

```bash
# Stop and delete runtime resources (keeps environment config)
./install.sh -k

# Delete environment configuration (keys, manifests)
./install.sh -d

# Complete cleanup (runtime + environment)
./install.sh -k; ./install.sh -d
```

## Config Connector Resource Lifecycle

GCP resources (Spanner, BigQuery, Dataflow, log sinks) are managed as Kubernetes CRDs via Config Connector. This section covers how to inspect, force-reconcile, and safely bounce those resources.

### Check resource status

```bash
source ./config.sh

# List all CC-managed resources and their Ready condition
kubectl get spannerinstance,spannerdatabase,bigquerydataset -n ${GOOGLE_NAMESPACE} -o wide

# Inspect a stuck resource
kubectl describe spannerinstance ${GOOGLE_SPANNER_INSTANCE} -n ${GOOGLE_NAMESPACE}
```

The `Ready: True` condition means CC last reconciled successfully. Any other status will include a reason in the events.

### Force a reconcile

Config Connector only re-applies when it detects a change. To kick a stuck resource:

```bash
source ./config.sh

kubectl annotate spannerinstance ${GOOGLE_SPANNER_INSTANCE} \
  cnrm.cloud.google.com/force-reconcile="$(date +%s)" \
  --overwrite -n ${GOOGLE_NAMESPACE}
```

Remove the annotation afterwards to avoid drift:

```bash
kubectl annotate spannerinstance ${GOOGLE_SPANNER_INSTANCE} \
  cnrm.cloud.google.com/force-reconcile- \
  --overwrite -n ${GOOGLE_NAMESPACE}
```

### Safely delete and recreate a resource ("bounce")

By default, deleting a Config Connector CR will delete the underlying GCP resource. To bounce without data loss, annotate with `abandon` first:

```bash
source ./config.sh

# Step 1: set abandon so deletion does NOT touch GCP
kubectl annotate spannerinstance ${GOOGLE_SPANNER_INSTANCE} \
  cnrm.cloud.google.com/deletion-policy=abandon --overwrite -n ${GOOGLE_NAMESPACE}

# Step 2: delete the K8s CR (GCP resource is preserved)
kubectl delete spannerinstance ${GOOGLE_SPANNER_INSTANCE} -n ${GOOGLE_NAMESPACE}

# Step 3: reapply — CC re-adopts the existing GCP resource
kubectl apply -f gcp/spanner.yaml
```

### Suspend and resume reconciliation

Useful during manual maintenance windows:

```bash
source ./config.sh

# Suspend
kubectl annotate spannerinstance ${GOOGLE_SPANNER_INSTANCE} \
  cnrm.cloud.google.com/reconcile-interval-in-seconds="999999" \
  --overwrite -n ${GOOGLE_NAMESPACE}

# Resume (remove the annotation)
kubectl annotate spannerinstance ${GOOGLE_SPANNER_INSTANCE} \
  cnrm.cloud.google.com/reconcile-interval-in-seconds- \
  --overwrite -n ${GOOGLE_NAMESPACE}
```

### Spanner DDL schema changes

Config Connector **appends** DDL statements — it does not modify existing ones. If you change a `CREATE TABLE` statement in `gcp/spanner.tpl.yaml`, CC will ignore it. To evolve the schema:

- Add a new `ALTER TABLE ... ADD COLUMN` (or similar) entry to the `ddl:` list in the template, OR
- Apply the DDL change directly via `gcloud spanner databases ddl update` and leave the YAML as the source of truth for new deployments

## Troubleshooting

### Common Issues

1. **Permission Errors**: Ensure your GCP user has Owner role on the project and all organization policies are correctly set.

2. **Environment Variables**: The script will validate all required environment variables and provide clear error messages if any are missing.

3. **Network Connectivity**: If building the VNF image fails with SSH errors, ensure you can connect to GCP from your network (run `gcert` if on Google corporate network).

4. **Resource Quotas**: Ensure your GCP project has sufficient quotas for compute instances, GKE clusters, and other resources.

5. **Dependency Resolution / Build Failures**: If you encounter build failures related to package dependencies (e.g., 401 Unauthorized errors from `us-python.pkg.dev`), see the Python Dependency Management section below.

### Python Dependency Management

This project uses [uv](https://docs.astral.sh/uv/) for Python dependency management. Each Python component has:
- `pyproject.toml` - Defines dependencies and project metadata
- `uv.lock` - Locked dependency versions for reproducible builds
- `uv.toml` - Optional uv configuration overrides

**Issue: Google Workstation Airlock Registry**

If you're developing on a Google workstation, you may have a system-level uv configuration at `/etc/uv/uv.toml` that forces all packages to use Google's internal Airlock registry (`us-python.pkg.dev/artifact-foundry-prod`). This registry requires authentication and is not accessible during Cloud Run or Vertex AI builds, causing deployment failures.

**Solution: Override with Public PyPI**

Each component includes a local `uv.toml` file that overrides the system configuration to use public PyPI:

```toml
# Override system-level uv configuration to use public PyPI
[[index]]
name = "pypi"
url = "https://pypi.org/simple"
default = true
```

**Regenerating Lock Files**

If you modify dependencies in `pyproject.toml`, regenerate the lock file:

```bash
# Navigate to the component directory (e.g., services/sharedmcp)
cd services/sharedmcp

# Remove old lock file and regenerate with public PyPI
rm uv.lock
uv lock --default-index https://pypi.org/simple

# Or, if uv.toml exists in the directory, just run:
uv lock
```

**Components with uv configuration:**

All Python components have `uv.toml` files to ensure public PyPI access:
- `services/sharedmcp/`
- `domains/transport/agents/transport-orchestrator/`
- `domains/transport/services/intentserver/`
- `domains/transport/services/topologyserver/`
- `agents-in-agent-runtime/*/` (one per agent in the fleet)
- `mcp_server_for_ge/re/`, `mcp_server_for_ge/ge/`
- `services/logcollector/`, `services/metricscollector/`, `services/gitea-webhook/`
- `controller/`

**Verifying Lock Files**

To verify a lock file uses public PyPI:

```bash
head -50 uv.lock | grep registry
# Should show: source = { registry = "https://pypi.org/simple" }
# NOT: source = { registry = "https://us-python.pkg.dev/..." }
```

### Getting Help

- Use `./install.sh -g` to check your current environment configuration
- Use `./install.sh -i` to see all deployed service URLs
- Check the script output for specific error messages and suggested fixes

## What Gets Deployed

The complete installation creates:

- **GKE Cluster**: Kubernetes cluster with Config Connector and operators
- **Network Infrastructure**: VPCs, subnets, firewall rules, and NAT gateways  
- **Database**: Spanner instances for multi-domain network topology storage (transport, core, radio, infrastructure, service, ops)
- **Controller**: Multi-domain network lifecycle controller on GKE (VyOS, free5gc, UERANSIM, infrastructure)
- **Domain Orchestrator Agent**: ADK-based transport orchestrator (the only domain with an agent)
- **MCP Tools**: `sharedmcp` for the domain orchestrator, plus the `re`/`ge` twins in `mcp_server_for_ge/` for the agent fleet and Gemini Enterprise
- **Agent Fleet**: supervisor, designer, detective, impact, chaos, tester and trust on Vertex AI Agent Runtime (deployed by `agents-in-agent-runtime/deploy_agents.py`, not by `install.sh`)
- **Metrics Collector**: Network metrics collection service
- **Log Collector**: Log capture and processing service
- **Dashboard**: Flutter web console (`dashboard-v2`) on Cloud Run behind IAP
- **Git Repository**: Gitea server for network configuration management (GitOps)
- **Gitea Webhook**: Webhook handler that drives the trust review loop on `update`-branch pushes
- **GNN Services**: Graph Neural Network inference pipeline (Vertex AI)

Most services are on Cloud Run. The exceptions: the controller and **Gitea** run on GKE — Gitea
because its SQLite database and repositories need a persistent disk — the agent fleet runs on
Vertex AI Agent Runtime, and the network simulator is a Compute Engine VM.
