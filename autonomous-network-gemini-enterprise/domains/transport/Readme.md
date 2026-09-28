# Transport Domain

The transport domain manages the lifecycle and autonomous operation of the underlay network — VyOS routers, underlay connectivity, and L3VPN services.

## Directory Structure

| Directory | Description |
|-----------|-------------|
| `agents/` | Transport orchestrator agent (Google ADK) — monitors, troubleshoots, and autonomously manages transport resources |
| `specs/` | Domain schema documents: inventory, topology, and intent specifications |
| `gnn/` | Graph Neural Network for topology-aware anomaly detection and failure prediction (trained and served on Vertex AI) |
| `knowledge/` | Agent-facing reference material — `designdoc.md`, the transport design reference the designer and trust agents read |
| `services/` | Domain services deployed to Cloud Run: `intentserver/`, `topologyserver/` |

## Components

### Orchestrator Agent (`agents/transport-orchestrator/`)

An AI agent built with Google ADK that handles autonomous transport lifecycle management. It responds to network events, performs root cause analysis, and drives remediation for VyOS routers, underlays, and L3VPN services.

### GNN (`gnn/`)

A graph neural network that operates over the transport topology graph. It learns structural and behavioral patterns to detect anomalies and predict faults. Training and serving run on Vertex AI using the provided Dockerfiles.

### Specs (`specs/`)

Schema and contract documents that define the transport domain's data model:

- `INVENTORY.md` — physical and logical resource inventory schema
- `TOPOLOGY.md` — how transport resources connect and relate
- `INTENT.md` — desired state / intent model for the domain

### Knowledge (`knowledge/`)

- `designdoc.md` — the transport design reference: addressing plan, VRF and route-target scheme,
  underlay and overlay conventions. Agents read it before proposing a change.

### Services (`services/`)

- `intentserver/` — serves intent data to the orchestrator and other consumers
- `topologyserver/` — serves live topology data derived from the Spanner graph
