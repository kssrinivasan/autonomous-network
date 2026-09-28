# Transport Intent API Server

FastAPI-based REST service that implements TMF921 Intent Management for Layer 3 VPN provisioning in the transport domain.

## Architecture

### ACTN Alignment

The API server acts as the **MDSC (Multi-Domain Service Coordinator)** in the IETF ACTN (RFC 8453) architecture:

```
Northbound System  →  Transport API (MDSC)  →  Transport Orchestrator Agent  →  K8s Operator (PNC)  →  VyOS Routers
     (CNC)                  This Service              A2A JSON-RPC                   Controller              Network
```

**Roles:**
- **CNC (Customer Network Controller)**: Northbound system submitting TMF921 intents (what connectivity is needed)
- **MDSC (Multi-Domain Service Coordinator)**: This API server - translates intents to infrastructure configurations (how to implement it)
- **PNC (Physical Network Controller)**: Kubernetes operator - provisions configurations on network elements

### Component Design

```
┌─────────────────────┐
│  Northbound System  │
│       (CNC)         │
└──────────┬──────────┘
           │ TMF921 Intent (JSON)
           ↓
┌─────────────────────────────────────────────┐
│         FastAPI Server (Port 8080)          │
│  ┌────────────────────────────────────┐    │
│  │ REST Endpoints                      │    │
│  │ - POST /intent                      │    │
│  │ - GET /intents                      │    │
│  │ - GET /intent/{id}                  │    │
│  │ - PATCH /intent/{id}                │    │
│  │ - DELETE /intent/{id}               │    │
│  └────────────────────────────────────┘    │
│           ↓                  ↓               │
│  ┌──────────────────┐  ┌──────────────┐    │
│  │ Pydantic Models  │  │ A2A Client   │    │
│  │ (TMF921 Schema)  │  │ (a2a-sdk)    │    │
│  └──────────────────┘  └──────┬───────┘    │
└────────────────────────────────┼────────────┘
                                 │ A2A JSON-RPC
                                 ↓
           ┌──────────────────────────────────────┐
           │ Transport Orchestrator Agent (ADK)   │
           │  - validate_intent_schema()           │
           │  - check_underlay_readiness()         │
           │  - check_vpn_conflicts()              │
           │  - create_vyosl3vpn_cr()              │
           │  - get_vpn_cr_status()                │
           │  - delete_vyosl3vpn_cr()              │
           └──────────────┬───────────────┬────────┘
                          ↓               ↓
                 ┌─────────────┐   ┌──────────────┐
                 │  Kubernetes │   │   Spanner    │
                 │  (VyOSL3VPN │   │ (IntentRecords│
                 │     CRs)    │   │    table)    │
                 └─────────────┘   └──────────────┘
```

### State Management

**Hybrid Architecture:**
- **Spanner** (`agentops-db.IntentRecords`): Stores intent metadata (id, serviceId, CR name, TMF921 envelope) for audit trail and fast conflict detection
- **Kubernetes** (VyOSL3VPN CRs): Authoritative source for provisioning state (CR `status.phase`)
- **API Server**: Combines both - fetches intent from Spanner + enriches with live CR status from K8s

**Why hybrid?**
- Spanner enables fast serviceId conflict detection (indexed query)
- K8s CRs are source of truth for actual provisioning state
- Spanner preserves full intent history even after CR deletion
- Audit trail persists independently of infrastructure lifecycle

### Request Flow

**POST /intent (8-step orchestration pipeline):**

1. **Validate TMF921 schema** - Pydantic models ensure envelope structure
2. **Generate intent ID** - Assign UUID
3. **Check serviceId conflicts** - Query Spanner for duplicates
4. **Call orchestrator via A2A** - Send full intent to transport orchestrator agent
5. **Orchestrator validates underlayRef** - Check VyOSUnderlay CR exists and is Ready
6. **Orchestrator creates VyOSL3VPN CR** - Apply to Kubernetes
7. **Store intent record** - Save to Spanner with CR name mapping
8. **Return 202 Acknowledged** - Intent accepted, provisioning in progress

**GET /intent/{id}:**

1. Fetch intent from Spanner by UUID
2. Call orchestrator to get live VyOSL3VPN CR status
3. Map CR phase → TMF921 state
4. Return enriched intent with live state

**DELETE /intent/{id}:**

1. Fetch intent from Spanner
2. Call orchestrator to delete VyOSL3VPN CR
3. K8s operator removes VRF configs from routers
4. Update Spanner state to `Inactive`
5. Return intent with final state

## REST API

**Base URL:** `https://<cloud-run-url>`

### Intent Abstraction

**High-Level Intent (what you provide):**
```json
{
  "serviceName": "Corporate WAN",
  "topology": "any-to-any",
  "endpoints": [
    {
      "peRouter": "pe1",
      "peInterface": "eth2",
      "customerRouter": "ce-core1",
      "customerPrefix": "10.100.0.0/24",
      "customerVlan": 100
    }
  ],
  "sla": {
    "bandwidth": "100 Mbps",
    "latency": "< 10ms",
    "availability": "99.99%",
    "trafficClass": "business-critical"
  }
}
```

**Low-Level Configuration (orchestrator derives):**
- VRF name (auto-generated from serviceName)
- Route Distinguisher (RD) per PE router
- Route Target (RT) values based on topology
- BGP neighbor configurations
- BGP AS numbers from CE router registry
- QoS policies (trafficClass → VyOS queue configuration)

**Key Principle:** You specify **what** connectivity is needed. The orchestrator derives **how** to implement it.

### Endpoints

| Method | Path | Description | Status Codes |
|--------|------|-------------|--------------|
| POST | `/intent` | Submit new L3 VPN intent | 202, 400, 409, 424, 500 |
| GET | `/intents` | List all active intents | 200, 500 |
| GET | `/intent/{id}` | Get intent status | 200, 404, 500 |
| PATCH | `/intent/{id}` | Update intent (not implemented) | 501 |
| DELETE | `/intent/{id}` | Revoke intent | 200, 404, 500 |
| GET | `/health` | Health check | 200 |
| GET | `/docs` | Interactive Swagger UI | 200 |
| GET | `/redoc` | Alternative ReDoc UI | 200 |
| GET | `/openapi.json` | OpenAPI 3.1 schema | 200 |

### API Discovery

**For northbound orchestrators to discover this API:**

1. **OpenAPI Schema (machine-readable):**
   ```bash
   curl https://transport-intentserver-<hash>.a.run.app/openapi.json
   ```
   
   Returns full OpenAPI 3.1 specification with all endpoints, schemas, and examples.

2. **Interactive Documentation (human-readable):**
   - **Swagger UI:** `https://<api-url>/docs`
   - **ReDoc:** `https://<api-url>/redoc`

3. **Service Discovery Pattern:**
   ```bash
   # Get service health and capabilities
   curl https://transport-intentserver-<hash>.a.run.app/health
   
   # Fetch OpenAPI schema for programmatic client generation
   curl https://transport-intentserver-<hash>.a.run.app/openapi.json -o transport-api-schema.json
   
   # Generate client SDK (example with openapi-generator)
   openapi-generator-cli generate \
     -i transport-api-schema.json \
     -g python \
     -o ./transport-api-client
   ```

### TMF921 Intent States

```
POST → Acknowledged → InProgress → Completed
                               └─→ Rejected
DELETE → Inactive
```

**State Mapping (VyOSL3VPN CR → TMF921):**
- `Waiting` → `InProgress`
- `Processing` → `InProgress`
- `Ready` → `Completed`
- `Error` → `Rejected`
- `Deleted` → `Inactive`

## Deployment

### Prerequisites

1. **Environment configured:**
   ```bash
   source config.sh  # From repository root
   ```

2. **Transport orchestrator deployed:**
   ```bash
   cd domains/transport/agents/transport-orchestrator
   ./install.sh
   ```

3. **Spanner ops database deployed:**
   
   The IntentRecords table is automatically created in the `agentops-db` database when you deploy the Spanner infrastructure:
   
   ```bash
   cd gcp
   ./install.sh --spanner
   ```
   
   The table schema is defined in `gcp/spanner.tpl.yaml`.

### Deploy to Cloud Run

```bash
cd domains/transport/services/intentserver
./install.sh
```

The install script will:
1. Render `cloudbuild.yaml` from template using `envsubst`
2. Build container image via Cloud Build
3. Discover transport orchestrator A2A URL
4. Deploy to Cloud Run with environment variables
5. Output API URL and test commands

### Uninstall

```bash
./uninstall.sh
```

## Usage Examples

### Create Intent

```bash
API_URL="https://transport-intentserver-<hash>-uc.a.run.app"

curl -X POST $API_URL/intent \
  -H 'Content-Type: application/json' \
  -d @example-intent.json
```

**Response (202 Acknowledged):**
```json
{
  "@type": "Intent",
  "id": "a3f2c1d4-5e6f-7g8h-9i0j-1k2l3m4n5o6p",
  "name": "red-mesh-vpn",
  "state": "Acknowledged",
  "message": "Intent received and queued for orchestration",
  "intentExpression": { ... }
}
```

### List Intents

```bash
curl $API_URL/intents
```

**Response:**
```json
{
  "intents": [
    {
      "@type": "Intent",
      "id": "a3f2c1d4-...",
      "name": "red-mesh-vpn",
      "state": "Completed",
      "crStatus": {
        "phase": "Ready",
        "message": "All PE and CE routers configured successfully"
      },
      "intentExpression": { ... }
    }
  ]
}
```

### Get Intent Status

```bash
curl $API_URL/intent/a3f2c1d4-...
```

**Response:**
```json
{
  "@type": "Intent",
  "id": "a3f2c1d4-...",
  "name": "red-mesh-vpn",
  "state": "Completed",
  "message": "VPN service is Ready",
  "crStatus": {
    "phase": "Ready",
    "message": "All PE and CE routers configured successfully"
  },
  "intentExpression": { ... }
}
```

### Delete Intent

```bash
curl -X DELETE $API_URL/intent/a3f2c1d4-...
```

**Response:**
```json
{
  "@type": "Intent",
  "id": "a3f2c1d4-...",
  "state": "Inactive",
  "message": "Intent revoked, VPN service deleted"
}
```

## Error Responses

**400 Bad Request - Validation Error:**
```json
{
  "detail": "Validation error",
  "errors": [
    {
      "loc": ["body", "intentExpression", "expectation"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

**409 Conflict - Duplicate serviceId:**
```json
{
  "detail": "Conflict: VPN service with serviceId 'RED_MESH' already exists"
}
```

**424 Failed Dependency - Underlay Not Ready:**
```json
{
  "detail": "VyOSUnderlay CR 'default-multi-underlay' is not Ready (phase: Processing)"
}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP server port | `8080` |
| `ORCHESTRATOR_URL` | Transport orchestrator A2A endpoint | (required) |
| `SPANNER_INSTANCE` | Spanner instance ID | `networktopology-instance` |
| `GOOGLE_SPANNER_OPS_DATABASE` | Spanner ops database name | `agentops-db` |
| `K8S_NAMESPACE` | Kubernetes namespace for CRs | `default` |

## Development

### Local Testing

1. **Install uv (if not already installed):**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies:**
   ```bash
   # Using uv (recommended)
   uv pip install -e .
   ```

3. **Set environment variables:**
   ```bash
   export ORCHESTRATOR_URL="http://localhost:8081/a2a/app"
   export SPANNER_INSTANCE="networktopology-instance"
   export GOOGLE_SPANNER_OPS_DATABASE="agentops-db"
   export K8S_NAMESPACE="default"
   ```

4. **Run server:**
   ```bash
   cd src
   python main.py
   ```

5. **Test endpoints:**
   ```bash
   curl http://localhost:8080/health
   curl http://localhost:8080/docs  # Swagger UI
   curl -X POST http://localhost:8080/intent -H 'Content-Type: application/json' -d @../example-intent.json
   ```

### Development with uv

```bash
# Create virtual environment and install dev dependencies
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
uv pip install -e ".[dev]"

# Generate lock file for reproducible builds (requires GCP auth)
uv lock

# Run tests (when implemented)
pytest

# Lint and format
ruff check .
ruff format .
```

**Note on `uv.lock`:** The lock file ensures reproducible builds but requires authentication to Google's artifact registry for `a2a-sdk`. Once generated and committed, the Dockerfile can use `uv sync --frozen` for guaranteed dependency versions.

### Code Structure

```
intentserver/
├── src/
│   ├── main.py                         # FastAPI app with 5 endpoints
│   ├── models/
│   │   ├── __init__.py
│   │   └── tmf921.py                   # Pydantic models for TMF921
│   └── clients/
│       ├── __init__.py
│       ├── orchestrator_client.py      # A2A client
│       └── spanner_client.py           # Spanner intent store
├── pyproject.toml                      # Python project & dependencies (uv)
├── Dockerfile                          # Container image
├── cloudbuild.tpl.yaml                       # Cloud Build template
├── install.sh                          # Deployment script
├── uninstall.sh                        # Cleanup script
├── example-intent.json                 # Sample 4-site mesh VPN intent
├── example-intent-simple.json          # Simple 2-site point-to-point intent
└── Readme.md                           # This file
```

## References

- [TMF921 Intent Management API Specification](https://www.tmforum.org/resources/specification/tmf921-intent-management-api-user-guide-v1-0-0/)
- [IETF ACTN Framework (RFC 8453)](https://datatracker.ietf.org/doc/html/rfc8453)
- [INTENT.md Specification](../../specs/INTENT.md)
- [VyOSL3VPN CRD](../../../../controller/config/transport/vyosl3vpn.yaml)
- [Transport Orchestrator Agent](../../agents/transport-orchestrator/)
