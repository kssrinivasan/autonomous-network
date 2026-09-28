# Transport Domain Inventory API (TMF639)

**Northbound API** for discovering available transport infrastructure resources to support L3VPN connectivity intent creation.

---

## Overview

**Purpose:** Enable northbound systems (e.g., service orchestrator) to discover available PE routers, customer interfaces, and existing L3VPN services when planning backhaul connectivity.

**Intended Consumers:** Service domain orchestrator, network planning systems, external service requestors

**Spanner Database:** `transporttopology-db`

**TMF Alignment:** TMF639 Resource Inventory Management API v4.0

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/transport`

**What is Exposed:**
- **PE Routers** by geographic location (for backhaul endpoint selection)
- **Customer-facing interfaces** on PE routers (for service attachment points)
- **Existing L3VPN services** (for name conflict checking and service discovery)

**What is NOT Exposed:**
- Internal topology (P routers, links, subnets, routing protocols)
- VRF implementation details (RD/RT values, BGP sessions)
- Low-level interface properties (MAC addresses, media types, IP addressing)

**Next Steps:** After discovering available resources, use the [Transport Intent API](INTENT.md) to submit L3VPN connectivity requests.

---

## Resource Models

### PhysicalRouter (PE Routers Only)

**Description:** Provider Edge routers available for L3VPN service attachment, with geographic location for coverage planning.

**TMF639 Resource Schema:**
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

**Key Characteristics:**

| Name | Description | Usage |
|------|-------------|-------|
| `role` | Always "PE" for northbound queries | Filter PE routers from internal infrastructure |
| `location_city` | City name | Match to coverage zones |
| `location_lat`, `location_lon` | GPS coordinates | Proximity-based PE selection |
| `status` | Ready, Error, Maintenance | PE availability for new services |

**Resource Status Values:**
- `Active` — PE is operational and accepting new VPN services
- `Suspended` — PE is unavailable (use for capacity planning only)
- `Standby` — PE is ready but not currently in service

---

### PhysicalInterface (PE Router Interfaces)

**Description:** Interfaces on PE routers available for L3VPN attachment.

**TMF639 Resource Schema:**
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
    {"name": "status", "value": "UP"},
    {"name": "ip_address", "value": "10.0.1.2"},
    {"name": "speed", "value": "1G"}
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

**Key Characteristics:**

| Name | Description | Usage |
|------|-------------|-------|
| `router_id` | Parent PE router ID | Link interface to router |
| `status` | UP, DOWN, ADMIN_DOWN | Interface operational state |
| `ip_address` | IP address (optional) | Interface IP configuration |
| `speed` | Interface speed (optional) | Bandwidth capacity (e.g., "1G", "10G") |

**Resource Status Values:**
- `Available` — Interface status is UP
- `InUse` — Interface status is not UP (approximation)

**Note:** All interfaces on PE routers are exposed via this API. Security filtering ensures only PE router interfaces are accessible.

---

### L3VPNService

**Description:** Existing L3VPN services for conflict checking and service discovery.

**TMF639 Resource Schema:**
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

**Key Characteristics:**

| Name | Description | Usage |
|------|-------------|-------|
| `topology` | hub, any-to-any, point-to-point | VPN service type |
| `endpoint_count` | Number of PE endpoints | Service scale indicator |

**Resource Status Values:**
- `Ready` — VPN service is fully provisioned and operational
- `InProgress` — VPN service is being provisioned (intent InProgress)
- `Error` — VPN service provisioning failed
- `Inactive` — VPN service has been deleted

---

## REST API Reference

### GET /resource

List resources with optional filtering.

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/transport/resource`

**Query Parameters:**

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `resourceSpecification` | string | Filter by resource type | `PhysicalRouter` |
| `resourceStatus` | string | Filter by status | `Active` |
| `role` | string | Filter PE routers by role | `PE` |
| `location_city` | string | Filter routers by city | `London` |
| `router_id` | string | Filter interfaces by parent router | `router:pe1` |
| `limit` | integer | Page size (max 100) | `20` |
| `offset` | integer | Page offset | `0` |

**Response Schema:**
```json
{
  "resourceInventory": [
    {
      "@type": "PhysicalRouter",
      "id": "router:pe1",
      ...
    }
  ],
  "totalCount": 10,
  "limit": 20,
  "offset": 0
}
```

---

### GET /resource/{id}

Get a specific resource by ID.

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/transport/resource/{id}`

**Path Parameters:**
- `id` (required) - Resource ID (e.g., `router:pe1`)

**Response Schema:** Single TMF639 Resource object

**Example:**

```bash
GET /tmf-api/resourceInventoryManagement/v4/transport/resource/router:pe1

Response:
{
  "@type": "PhysicalRouter",
  "id": "router:pe1",
  "name": "pe1",
  "resourceStatus": "Active",
  "resourceCharacteristic": [
    {"name": "role", "value": "PE"},
    {"name": "location_city", "value": "London"},
    {"name": "status", "value": "Ready"}
  ]
}
```

---

## Event Notifications (TMF688)

Real-time notifications for infrastructure changes affecting service planning.

### Event Types

#### ResourceStateChangeEvent

**Trigger:** PE router availability changed (e.g., Active → Suspended)

**Use Case:** Northbound systems monitor PE capacity and trigger replanning if backhaul router fails.

**Schema:**
```json
{
  "eventId": "d5g0h3c4-6e7f-8g9h-0i1j-2k3l4m5n6o7p",
  "eventTime": "2026-08-12T12:45:15.789Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "transport",
  "event": {
    "resource": {
      "@type": "PhysicalRouter",
      "id": "router:pe1",
      "name": "pe1",
      "resourceStatus": "Suspended"
    },
    "stateChange": {
      "previousState": "Active",
      "newState": "Suspended",
      "reason": "Router status changed from Ready to Error"
    }
  }
}
```

#### L3VPNService Lifecycle Events

**Event Types:**
- `ResourceCreateEvent` — VPN service provisioning started (intent acknowledged)
- `ResourceStateChangeEvent` — VPN service state changed (InProgress → Ready or Error)
- `ResourceDeleteEvent` — VPN service deleted (intent revoked)

**Use Case:** Track L3VPN provisioning status after submitting transport intent.

**Schema:**
```json
{
  "eventId": "b3e8f1a2-4c5d-6e7f-8g9h-0i1j2k3l4m5n",
  "eventTime": "2026-08-12T10:15:30.123Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "transport",
  "event": {
    "resource": {
      "@type": "L3VPNService",
      "id": "vpn:CORPORATE_WAN",
      "name": "CORPORATE_WAN",
      "resourceStatus": "Ready"
    },
    "stateChange": {
      "previousState": "InProgress",
      "newState": "Ready"
    }
  }
}
```

---

### Subscription Mechanisms

#### WebSocket Subscription

**URL:** `ws://<api-host>/tmf-api/resourceInventoryManagement/v4/transport/events`

**Subscribe to PE Router State Changes:**
```json
{
  "action": "subscribe",
  "subscriptionId": "pe-monitor-london",
  "filters": {
    "resourceSpecification": ["PhysicalRouter"],
    "eventTypes": ["ResourceStateChangeEvent"],
    "role": "PE",
    "location_city": "London"
  }
}
```

**Subscribe to VPN Service Lifecycle:**
```json
{
  "action": "subscribe",
  "subscriptionId": "vpn-tracker",
  "filters": {
    "resourceSpecification": ["L3VPNService"],
    "eventTypes": ["ResourceCreateEvent", "ResourceStateChangeEvent", "ResourceDeleteEvent"]
  }
}
```

#### Webhook Callbacks

**URL:** `POST /tmf-api/resourceInventoryManagement/v4/transport/hub`

**Register Webhook for PE Availability:**
```json
{
  "callback": "https://service-orchestrator.example.com/webhook/transport",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=PhysicalRouter&role=PE"
}
```

**Event Delivery:** The API will POST events to your callback URL.

---

## Northbound Use Cases

### Use Case 1: Find PE Routers for Backhaul Planning

**Scenario:** Service orchestrator needs to provision backhaul for a London network slice.

**Step 1: Query PE routers in London**
```bash
GET /tmf-api/resourceInventoryManagement/v4/transport/resource?
    resourceSpecification=PhysicalRouter&
    role=PE&
    location_city=London&
    resourceStatus=Active
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "PhysicalRouter",
      "id": "router:pe1",
      "name": "pe1",
      "resourceCharacteristic": [
        {"name": "role", "value": "PE"},
        {"name": "location_city", "value": "London"},
        {"name": "location_lat", "value": 51.5074},
        {"name": "location_lon", "value": -0.1278},
        {"name": "status", "value": "Ready"}
      ]
    },
    {
      "@type": "PhysicalRouter",
      "id": "router:pe2",
      "name": "pe2",
      "resourceCharacteristic": [
        {"name": "role", "value": "PE"},
        {"name": "location_city", "value": "London"},
        {"name": "status", "value": "Ready"}
      ]
    }
  ],
  "totalCount": 2
}
```

**Step 2: Query available customer interfaces on pe1**
```bash
GET /tmf-api/resourceInventoryManagement/v4/transport/resource?
    resourceSpecification=PhysicalInterface&
    router_id=router:pe1&
    interface_type=customer&
    resourceStatus=Available
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "PhysicalInterface",
      "id": "router:pe1:interface:eth2",
      "name": "eth2",
      "resourceStatus": "Available",
      "resourceCharacteristic": [
        {"name": "router_id", "value": "router:pe1"},
        {"name": "interface_type", "value": "customer"},
        {"name": "status", "value": "UP"}
      ]
    }
  ],
  "totalCount": 1
}
```

**Step 3: Submit L3VPN Intent**

After identifying available PE routers and interfaces, submit a connectivity intent via the [Transport Intent API](INTENT.md):

```bash
POST /intent
{
  "@type": "Intent",
  "name": "london-backhaul",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "L3ConnectivityIntent",
      "expectationObject": {
        "serviceName": "London Backhaul",
        "topology": "any-to-any",
        "endpoints": [
          {"peRouter": "pe1", "peInterface": "eth2", ...},
          {"peRouter": "pe2", "peInterface": "eth3", ...}
        ]
      }
    }]
  }
}
```

---

### Use Case 2: Check VPN Service Name Availability

**Scenario:** Before submitting an intent, verify the VPN service name is not already in use.

```bash
GET /tmf-api/resourceInventoryManagement/v4/transport/resource?
    resourceSpecification=L3VPNService&
    name=CORPORATE_WAN
```

**Response (name available):**
```json
{
  "resourceInventory": [],
  "totalCount": 0
}
```

**Response (name conflict):**
```json
{
  "resourceInventory": [
    {
      "@type": "L3VPNService",
      "id": "vpn:CORPORATE_WAN",
      "name": "CORPORATE_WAN",
      "resourceStatus": "Ready"
    }
  ],
  "totalCount": 1
}
```

---

### Use Case 3: Monitor PE Availability for Capacity Planning

**Scenario:** Service orchestrator subscribes to PE router state changes to detect capacity issues.

**WebSocket Subscription:**
```javascript
const ws = new WebSocket('ws://api-host/tmf-api/resourceInventoryManagement/v4/transport/events');

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    subscriptionId: 'pe-capacity-monitor',
    filters: {
      resourceSpecification: ['PhysicalRouter'],
      eventTypes: ['ResourceStateChangeEvent'],
      role: 'PE'
    }
  }));
};

ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  
  if (event.eventType === 'ResourceStateChangeEvent') {
    const router = event.event.resource;
    const change = event.event.stateChange;
    
    if (change.newState === 'Suspended') {
      console.log(`PE router ${router.name} is down - check affected VPN services`);
      // Trigger service impact analysis and replanning
    }
  }
};
```

---

### Use Case 4: Track VPN Service Provisioning Status

**Scenario:** After submitting a transport intent, monitor VPN service provisioning via events.

**WebSocket Subscription:**
```json
{
  "action": "subscribe",
  "subscriptionId": "vpn-provisioning-tracker",
  "filters": {
    "resourceSpecification": ["L3VPNService"],
    "eventTypes": ["ResourceStateChangeEvent"]
  }
}
```

**Event Received (VPN Ready):**
```json
{
  "eventType": "ResourceStateChangeEvent",
  "event": {
    "resource": {
      "@type": "L3VPNService",
      "id": "vpn:London_Backhaul",
      "resourceStatus": "Ready"
    },
    "stateChange": {
      "previousState": "InProgress",
      "newState": "Ready"
    }
  }
}
```

---

## Error Codes

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 400 | `INVALID_QUERY` | Invalid query parameter |
| 404 | `RESOURCE_NOT_FOUND` | Resource ID not found |
| 500 | `SPANNER_ERROR` | Database query failed |
| 503 | `SERVICE_UNAVAILABLE` | Database unavailable |

**Error Response Schema:**
```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Resource with id 'router:unknown' not found",
    "status": 404
  }
}
```

---

## API Workflow Summary

```
┌─────────────────────────────────────────────────────────────┐
│ Northbound System (Service Orchestrator)                    │
└─────────────────────────────────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
         ▼                ▼                ▼
  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
  │  Discovery  │  │  Planning   │  │ Monitoring  │
  │             │  │             │  │             │
  │ GET /resource│  │ POST /intent│  │ WebSocket   │
  │ (PE routers) │  │   (INTENT   │  │ Subscribe   │
  │ (Interfaces) │  │    API)     │  │ (Events)    │
  │ (VPN list)   │  │             │  │             │
  └─────────────┘  └─────────────┘  └─────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
         ┌────────────────────────────────┐
         │  Transport Domain (Internal)   │
         │  - VRF provisioning            │
         │  - BGP configuration           │
         │  - QoS policies                │
         │  - Underlay validation         │
         └────────────────────────────────┘
```

**Workflow:**
1. **Discovery** — Query available PE routers and customer interfaces
2. **Planning** — Submit L3VPN intent via [Transport Intent API](INTENT.md)
3. **Monitoring** — Subscribe to events for VPN lifecycle and PE availability

---

## Notes for Northbound Developers

- **PE routers only:** This API exposes only Provider Edge routers; P routers and internal topology are not queryable.
- **Customer interfaces only:** Use `interface_type=customer` filter to find service attachment points.
- **Intent-based provisioning:** Use the [Transport Intent API](INTENT.md) to request connectivity — this inventory API is read-only.
- **Real-time updates:** Subscribe to WebSocket events for infrastructure changes rather than polling.
- **Service lifecycle tracking:** Monitor L3VPNService state changes to detect when intents complete or fail.
