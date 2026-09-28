# Transport Topology API

**TMF639 Resource Inventory Management API for Transport Domain**

Northbound API for discovering available transport infrastructure resources to support L3VPN connectivity intent creation.

---

## Overview

The Transport Topology API implements [TMF639 Resource Inventory Management API v4.0](https://www.tmforum.org/resources/specification/tmf639-resource-inventory-management-api-rest-specification-r19-0-1/) for discovering:

- **PE Routers** by geographic location (for backhaul endpoint selection)
- **Customer-facing interfaces** on PE routers (for service attachment points)
- **Existing L3VPN services** (for name conflict checking and service discovery)

### Key Features

- **Resource Discovery**: Query PE routers, customer interfaces, and VPN services via TMF639-compliant REST API
- **Real-time Events**: Subscribe to topology changes via WebSocket or Webhook (TMF688)
- **Security Filtering**: Only PE routers and customer-facing interfaces are exposed (P routers and network interfaces are filtered)
- **Temporal Queries**: SCD Type 2 temporal tracking with historical snapshot support

### Architecture

```
Northbound System → Topology API → Spanner transporttopology-db
     (Service         (TMF639)       (PhysicalRouter, PhysicalInterface,
   Orchestrator)                      L3VPNService)
```

---

## Deployment

### Prerequisites

1. Environment configured via `config.sh` at repo root
2. Spanner instance with `transporttopology-db` database
3. GKE cluster with network controller writing topology data

### Install

```bash
cd domains/transport/services/topologyserver
./install.sh
```

**What it does:**
1. Renders `cloudbuild.yaml` from template using `envsubst`
2. Builds container image via Cloud Build
3. Deploys to Cloud Run with environment variables
4. Outputs API URL and example commands

### Uninstall

```bash
./uninstall.sh
```

### Authentication

The topology server deployed to Cloud Run requires authentication (`--no-allow-unauthenticated`).

**For Service-to-Service Calls (Python):**

```python
import google.auth.transport.requests
import google.auth
import requests

# Get ID token for authenticated requests
credentials, project = google.auth.default()
auth_req = google.auth.transport.requests.Request()
credentials.refresh(auth_req)

response = requests.get(
    f"{API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?role=PE",
    headers={"Authorization": f"Bearer {credentials.id_token}"}
)
```

**For Human Access (Testing):**

```bash
# Get auth token
TOKEN=$(gcloud auth print-identity-token)

# Make authenticated request
curl -H "Authorization: Bearer $TOKEN" \
  "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?role=PE"
```

**Granting Service Account Access:**

```bash
# Grant specific service account permission to invoke the API
gcloud run services add-iam-policy-binding transport-topologyserver \
    --region=$GOOGLE_REGION \
    --member="serviceAccount:SERVICE_ACCOUNT@${GOOGLE_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

---

## API Reference

### Base URL

```
/tmf-api/resourceInventoryManagement/v4/transport
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/resource` | List resources with filtering |
| GET | `/resource/{id}` | Get specific resource by ID |
| WS | `/events` | WebSocket event subscription |
| POST | `/hub` | Register webhook callback |
| DELETE | `/hub/{id}` | Unregister webhook callback |
| GET | `/health` | Service health check |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc documentation |

---

## Resource Types

### PhysicalRouter (PE Routers Only)

Provider Edge routers available for L3VPN service attachment.

**TMF639 Schema:**
```json
{
  "@type": "PhysicalRouter",
  "id": "router:pe1",
  "href": "/tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1",
  "name": "pe1",
  "resourceSpecification": {
    "id": "PhysicalRouter",
    "name": "Physical Router"
  },
  "resourceStatus": "Active",
  "resourceCharacteristic": [
    {"name": "role", "value": "PE"},
    {"name": "location_city", "value": "London"},
    {"name": "location_lat", "value": 51.5074},
    {"name": "location_lon", "value": -0.1278},
    {"name": "status", "value": "Ready"}
  ],
  "validFor": {
    "startDateTime": "2026-08-12T08:30:00Z",
    "endDateTime": null
  }
}
```

**Resource Status Values:**
- `Active` — PE is operational and accepting new VPN services
- `Suspended` — PE is unavailable (Error status)
- `Standby` — PE is in maintenance

### PhysicalInterface (Customer-Facing Only)

Customer-facing interfaces on PE routers available for L3VPN attachment.

**TMF639 Schema:**
```json
{
  "@type": "PhysicalInterface",
  "id": "router:pe1:interface:eth2",
  "href": "/tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1:interface:eth2",
  "name": "eth2",
  "resourceSpecification": {
    "id": "PhysicalInterface",
    "name": "Physical Interface"
  },
  "resourceStatus": "Available",
  "resourceCharacteristic": [
    {"name": "router_id", "value": "router:pe1"},
    {"name": "interface_type", "value": "customer"},
    {"name": "status", "value": "UP"}
  ],
  "relatedParty": [
    {
      "id": "router:pe1",
      "href": "/tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1",
      "role": "parent",
      "name": "pe1"
    }
  ],
  "validFor": {
    "startDateTime": "2026-08-12T08:30:00Z",
    "endDateTime": null
  }
}
```

**Resource Status Values:**
- `Available` — Interface is free for VPN service attachment
- `InUse` — Interface is allocated to an existing VPN service
- `Reserved` — Interface is reserved for planned service

### L3VPNService

Existing L3VPN services for conflict checking and service discovery.

**TMF639 Schema:**
```json
{
  "@type": "L3VPNService",
  "id": "vpn:BLUE_HUB",
  "href": "/tmf-api/resourceInventoryManagement/v4/transport/resource/vpn:BLUE_HUB",
  "name": "BLUE_HUB",
  "resourceSpecification": {
    "id": "L3VPNService",
    "name": "L3 VPN Service"
  },
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "topology", "value": "hub"},
    {"name": "endpoint_count", "value": 4}
  ],
  "validFor": {
    "startDateTime": "2026-08-12T08:30:00Z",
    "endDateTime": null
  }
}
```

**Resource Status Values:**
- `Ready` — VPN service is fully provisioned and operational
- `InProgress` — VPN service is being provisioned
- `Error` — VPN service provisioning failed
- `Inactive` — VPN service has been deleted

---

## Query Examples

### List PE Routers

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalRouter&role=PE"
```

**Note:** You can also omit `resourceSpecification` when using `role` or `location_city`:
```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?role=PE"
```

### List PE Routers in London

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalRouter&location_city=London"
```

### List Interfaces on pe1

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalInterface&router_id=router:pe1"
```

**Note:** You can also omit `resourceSpecification` when using `router_id`:
```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?router_id=router:pe1"
```

### List Available Interfaces

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalInterface&resourceStatus=Available"
```

### List L3VPN Services

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=L3VPNService"
```

### Get Specific Resource

```bash
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1"
```

---

## Event Subscriptions (TMF688)

### WebSocket Subscription

```javascript
const ws = new WebSocket('ws://topology-api/tmf-api/resourceInventoryManagement/v4/transport/events');

ws.onopen = () => {
  // Subscribe to PE router state changes
  ws.send(JSON.stringify({
    action: 'subscribe',
    subscriptionId: 'pe-monitor',
    filters: {
      resourceSpecification: ['PhysicalRouter'],
      eventTypes: ['ResourceStateChangeEvent'],
      role: 'PE'
    }
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.status === 'subscribed') {
    console.log('Subscribed:', data.subscriptionId);
  } else if (data.eventType) {
    console.log('Event received:', data.eventType, data.event.resource.name);
  }
};
```

### Webhook Registration

```bash
curl -X POST "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/hub" \
  -H 'Content-Type: application/json' \
  -d '{
    "callback": "https://service-orchestrator.example.com/webhook/transport",
    "query": "eventType=ResourceStateChangeEvent&resourceSpecification=PhysicalRouter&role=PE"
  }'
```

**Response:**
```json
{
  "id": "hub-uuid",
  "callback": "https://service-orchestrator.example.com/webhook/transport",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=PhysicalRouter&role=PE"
}
```

**Unregister Webhook:**
```bash
curl -X DELETE "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/hub/{hub-uuid}"
```

---

## Security

### Filtering Rules

The API enforces security filtering to prevent exposure of internal infrastructure:

1. **PhysicalRouter**: Only PE routers (`role='PE'`) are exposed. P, RR, and other internal routers are filtered.
2. **PhysicalInterface**: Only interfaces on PE routers are exposed. Interfaces on internal routers (P, RR) are filtered.
3. **Temporal Filtering**: All queries include `valid_end_ts IS NULL` to return only current resources (SCD Type 2).

### Testing Security

```bash
# Verify P routers are NOT exposed (should return empty list)
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalRouter&role=P"

# Verify only PE router interfaces are exposed
curl "${API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalInterface&limit=5"
```

---

## Integration with Transport Intent API

**Discovery Flow:**

1. **Query Topology API** → Discover available PE routers by geographic location
2. **Extract Resources** → Identify PE router IDs and customer interfaces
3. **Submit Intent** → Create L3VPN intent via [Transport Intent API](../intentserver/Readme.md) using discovered resources

**Example:**

```bash
# 1. Discover PE routers in London
ROUTERS=$(curl -s "${TOPOLOGY_API_URL}/tmf-api/resourceInventoryManagement/v4/transport/resource?resourceSpecification=PhysicalRouter&location_city=London")

# 2. Extract router IDs (jq example)
PE1_ID=$(echo "$ROUTERS" | jq -r '.resourceInventory[0].id')

# 3. Submit L3VPN intent using discovered PE1
curl -X POST "${INTENT_API_URL}/intent" \
  -H 'Content-Type: application/json' \
  -d @intent-with-discovered-router.json
```

---

## Development

### Local Development

**1. Set up environment:**

```bash
# Copy example env file and configure
cp .env.example .env

# Edit .env and set your GCP project and credentials
# Either set GOOGLE_APPLICATION_CREDENTIALS or run:
gcloud auth application-default login
```

**2. Run server:**

```bash
# uv will automatically create a venv and install dependencies
uv run python src/main.py
```

**Alternative** - manual setup:

```bash
# Create venv and install dependencies
uv venv
source .venv/bin/activate
uv pip install fastapi uvicorn pydantic google-cloud-spanner websockets httpx python-dotenv

# Run server
cd src
PYTHONPATH=$PWD python main.py
```

**Access:**
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
- Health: `http://localhost:8080/health`

### Testing

With the server running (`uv run python src/main.py`):

```bash
# Health check
curl http://localhost:8080/health

# List PE routers (auto-detects resourceSpecification from 'role' filter)
curl 'http://localhost:8080/tmf-api/resourceInventoryManagement/v4/transport/resource?role=PE'

# List interfaces on a specific router
curl 'http://localhost:8080/tmf-api/resourceInventoryManagement/v4/transport/resource?router_id=router:pe1'

# Get specific resource
curl 'http://localhost:8080/tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1'

# View Swagger UI for interactive testing
open http://localhost:8080/docs
```

---

## References

- [TMF639 Specification](https://www.tmforum.org/resources/specification/tmf639-resource-inventory-management-api-rest-specification-r19-0-1/)
- [TMF688 Event Management](https://www.tmforum.org/resources/specification/tmf688-event-management-api-rest-specification-r18-5-0/)
- [Transport INVENTORY.md](../../specs/INVENTORY.md)
- [Transport Intent API](../intentserver/Readme.md)

---

## License

Copyright 2024-2028 Google LLC

Licensed under the Apache License, Version 2.0
