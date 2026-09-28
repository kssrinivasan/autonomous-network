# Radio Domain Inventory API (TMF639)

**Northbound API** for discovering available radio access network (RAN) resources to support cell site deployment planning.

---

## Overview

**Purpose:** Enable northbound systems (e.g., service orchestrator) to discover deployed cell sites, coverage areas, and UE capacity when planning network slice radio coverage.

**Intended Consumers:** Service domain orchestrator, network planning systems, external service requestors

**Spanner Database:** `radiotopology-db`

**TMF Alignment:** TMF639 Resource Inventory Management API v4.0

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/radio`

**What is Exposed:**
- **Cell Sites** with coverage parameters (PLMN, TAC, gNodeB ID, location/zone)
- **UE capacity** and attachment status (active UEs, capacity headroom)
- **5G Core connectivity** (which core each cell site connects to)

**What is NOT Exposed:**
- Internal container details (UERANSIM process IDs, IP addresses)
- Low-level gNodeB parameters (NCI, gNB ID length, NGAP ports)
- Individual UE credentials (IMSI keys, OPC values)

**Next Steps:** After discovering coverage gaps, use the [Radio Intent API](INTENT.md) to deploy new cell sites.

---

## Resource Models

### CellSite

**Description:** gNodeB cell site providing 5G radio coverage in a specific area.

**TMF639 Resource Schema:**
```json
{
  "@type": "CellSite",
  "id": "cellsite:cellsite-gnb-01",
  "href": "/tmf-api/resourceInventoryManagement/v4/radio/resource/cellsite:cellsite-gnb-01",
  "name": "cellsite-gnb-01",
  "resourceSpecification": {
    "id": "CellSite",
    "name": "gNodeB Cell Site"
  },
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "mcc", "value": "234"},
    {"name": "mnc", "value": "15"},
    {"name": "tac", "value": 1},
    {"name": "coverage_zone", "value": "Downtown London"},
    {"name": "gnb_id", "value": "0x1"},
    {"name": "core_name", "value": "free5gc-core"},
    {"name": "ue_count", "value": 5},
    {"name": "ue_capacity", "value": 50}
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
| `mcc`, `mnc` | PLMN (Public Land Mobile Network) | Identify operator network |
| `tac` | Tracking Area Code | Group cell sites by tracking area |
| `coverage_zone` | Geographic coverage area | Match to service slice zones |
| `gnb_id` | gNodeB identifier | Unique cell site ID within network |
| `core_name` | Connected 5G core instance | Cross-domain reference to core |
| `ue_count` | Currently attached UEs | Monitor active connections |
| `ue_capacity` | Maximum UE capacity | Capacity planning |

**Resource Status Values:**
- `Ready` — Cell site is operational and accepting UE connections
- `Creating` — Cell site is being provisioned (intent InProgress)
- `Failed` — Cell site provisioning failed
- `Suspended` — Cell site is temporarily unavailable

---

### UESummary

**Description:** Aggregated UE statistics per cell site (individual UE details not exposed).

**TMF639 Resource Schema:**
```json
{
  "@type": "UESummary",
  "id": "uesummary:cellsite-gnb-01",
  "href": "/tmf-api/resourceInventoryManagement/v4/radio/resource/uesummary:cellsite-gnb-01",
  "name": "cellsite-gnb-01-ues",
  "resourceSpecification": {
    "id": "UESummary",
    "name": "UE Summary Statistics"
  },
  "resourceStatus": "Active",
  "resourceCharacteristic": [
    {"name": "cellsite_id", "value": "cellsite:cellsite-gnb-01"},
    {"name": "total_ues", "value": 5},
    {"name": "registered_ues", "value": 4},
    {"name": "active_sessions", "value": 3},
    {"name": "capacity_utilization", "value": "10%"}
  ],
  "relatedParty": [
    {
      "id": "cellsite:cellsite-gnb-01",
      "href": "/tmf-api/resourceInventoryManagement/v4/radio/resource/cellsite:cellsite-gnb-01",
      "role": "cellsite",
      "name": "cellsite-gnb-01"
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
| `total_ues` | Total UEs configured for this cell | Capacity tracking |
| `registered_ues` | UEs currently registered with AMF | Active UE count |
| `active_sessions` | UEs with active PDU sessions | Data session count |
| `capacity_utilization` | Percentage of capacity used | Capacity planning |

**Note:** Individual UE details (IMSI, credentials, session data) are not exposed via this API. UESummary provides only aggregated statistics for capacity planning.

---

## REST API Reference

### GET /resource

List resources with optional filtering.

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/radio/resource`

**Query Parameters:**

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `resourceSpecification` | string | Filter by resource type | `CellSite` |
| `resourceStatus` | string | Filter by status | `Ready` |
| `mcc` | string | Filter by Mobile Country Code | `234` |
| `mnc` | string | Filter by Mobile Network Code | `15` |
| `tac` | integer | Filter by Tracking Area Code | `1` |
| `coverageZone` | string | Filter by coverage zone | `Downtown London` |
| `coreName` | string | Filter by connected core | `free5gc-core` |
| `limit` | integer | Page size (max 100) | `20` |
| `offset` | integer | Page offset | `0` |

**Response Schema:**
```json
{
  "resourceInventory": [
    {
      "@type": "CellSite",
      "id": "cellsite:cellsite-gnb-01",
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

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/radio/resource/{id}`

**Path Parameters:**
- `id` (required) - Resource ID (e.g., `cellsite:cellsite-gnb-01`)

**Response Schema:** Single TMF639 Resource object

**Example:**

```bash
GET /tmf-api/resourceInventoryManagement/v4/radio/resource/cellsite:cellsite-gnb-01

Response:
{
  "@type": "CellSite",
  "id": "cellsite:cellsite-gnb-01",
  "name": "cellsite-gnb-01",
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "mcc", "value": "234"},
    {"name": "mnc", "value": "15"},
    {"name": "tac", "value": 1},
    {"name": "coverage_zone", "value": "Downtown London"},
    {"name": "ue_count", "value": 5},
    {"name": "ue_capacity", "value": 50}
  ]
}
```

---

## Event Notifications (TMF688)

Real-time notifications for cell site deployment and capacity changes.

### Event Types

#### ResourceStateChangeEvent

**Trigger:** Cell site status changed (e.g., Creating → Ready, Ready → Failed)

**Use Case:** Northbound systems monitor cell site provisioning after submitting radio intents.

**Schema:**
```json
{
  "eventId": "r2a3d4i5-6o7e-8v9e-0n1t-2s3h4e5r6e7d",
  "eventTime": "2026-08-12T10:16:45.456Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "radio",
  "event": {
    "resource": {
      "@type": "CellSite",
      "id": "cellsite:cellsite-gnb-01",
      "resourceStatus": "Ready"
    },
    "stateChange": {
      "previousState": "Creating",
      "newState": "Ready"
    }
  }
}
```

#### UESummary Capacity Events

**Event Types:**
- `ResourceStateChangeEvent` — UE capacity threshold exceeded (e.g., >80% utilization)

**Use Case:** Trigger alerts when cell sites approach capacity, prompting deployment of additional cells.

**Schema:**
```json
{
  "eventId": "r3a4d5i6-7o8e-9v0e-1n2t-3s4h5e6r7e8d",
  "eventTime": "2026-08-12T11:30:00.000Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "radio",
  "priority": "High",
  "event": {
    "resource": {
      "@type": "UESummary",
      "id": "uesummary:cellsite-gnb-01",
      "resourceCharacteristic": [
        {"name": "capacity_utilization", "value": "85%"}
      ]
    },
    "stateChange": {
      "reason": "Capacity threshold exceeded (>80%)"
    }
  }
}
```

---

### Subscription Mechanisms

#### WebSocket Subscription

**URL:** `ws://<api-host>/tmf-api/resourceInventoryManagement/v4/radio/events`

**Subscribe to Cell Site State Changes:**
```json
{
  "action": "subscribe",
  "subscriptionId": "cellsite-monitor",
  "filters": {
    "resourceSpecification": ["CellSite"],
    "eventTypes": ["ResourceStateChangeEvent"],
    "tac": 1
  }
}
```

**Subscribe to Capacity Alerts:**
```json
{
  "action": "subscribe",
  "subscriptionId": "capacity-monitor",
  "filters": {
    "resourceSpecification": ["UESummary"],
    "eventTypes": ["ResourceStateChangeEvent"]
  }
}
```

#### Webhook Callbacks

**URL:** `POST /tmf-api/resourceInventoryManagement/v4/radio/hub`

**Register Webhook for Cell Site Provisioning:**
```json
{
  "callback": "https://service-orchestrator.example.com/webhook/radio",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=CellSite"
}
```

**Event Delivery:** The API will POST events to your callback URL.

---

## Northbound Use Cases

### Use Case 1: Query Cell Site Coverage in a Zone

**Scenario:** Service orchestrator needs to check existing cell coverage in "Downtown London" zone.

**Step 1: Query cell sites by coverage zone**
```bash
GET /tmf-api/resourceInventoryManagement/v4/radio/resource?
    resourceSpecification=CellSite&
    coverageZone=Downtown%20London&
    resourceStatus=Ready
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "CellSite",
      "id": "cellsite:cellsite-gnb-01",
      "name": "cellsite-gnb-01",
      "resourceCharacteristic": [
        {"name": "coverage_zone", "value": "Downtown London"},
        {"name": "tac", "value": 1},
        {"name": "ue_count", "value": 5},
        {"name": "ue_capacity", "value": 50},
        {"name": "core_name", "value": "free5gc-core"}
      ]
    }
  ],
  "totalCount": 3
}
```

**Interpretation:** 3 cell sites already cover this zone with total capacity of 150 UEs.

---

### Use Case 2: Check UE Capacity Headroom

**Scenario:** Service orchestrator planning to add 100 UEs to a tracking area.

**Step 1: Query all cell sites in TAC 1**
```bash
GET /tmf-api/resourceInventoryManagement/v4/radio/resource?
    resourceSpecification=CellSite&
    tac=1&
    resourceStatus=Ready
```

**Step 2: Query UE summaries for each cell**
```bash
GET /tmf-api/resourceInventoryManagement/v4/radio/resource?
    resourceSpecification=UESummary&
    cellsiteId=cellsite:cellsite-gnb-01
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "UESummary",
      "id": "uesummary:cellsite-gnb-01",
      "resourceCharacteristic": [
        {"name": "total_ues", "value": 5},
        {"name": "capacity_utilization", "value": "10%"}
      ]
    }
  ]
}
```

**Calculation:** 5 UEs at 10% utilization → ~50 UE capacity → ~45 available slots per cell site.

---

### Use Case 3: Verify Core Connectivity Before Cell Deployment

**Scenario:** Radio orchestrator needs to verify 5G core is ready before deploying cell sites.

**Step 1: Query existing cell sites connected to core**
```bash
GET /tmf-api/resourceInventoryManagement/v4/radio/resource?
    resourceSpecification=CellSite&
    coreName=free5gc-core
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "CellSite",
      "id": "cellsite:cellsite-gnb-01",
      "resourceCharacteristic": [
        {"name": "core_name", "value": "free5gc-core"},
        {"name": "mcc", "value": "234"},
        {"name": "mnc", "value": "15"}
      ]
    }
  ],
  "totalCount": 2
}
```

**Interpretation:** Core "free5gc-core" has 2 cell sites already connected → core is operational and accepting connections.

**Step 2: Submit Radio Intent**

After verifying core connectivity, submit a cell site deployment intent via the [Radio Intent API](INTENT.md).

---

### Use Case 4: Monitor Cell Site Provisioning Status

**Scenario:** After submitting a radio intent, monitor cell site provisioning via events.

**WebSocket Subscription:**
```javascript
const ws = new WebSocket('ws://api-host/tmf-api/resourceInventoryManagement/v4/radio/events');

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    subscriptionId: 'cellsite-provisioning',
    filters: {
      resourceSpecification: ['CellSite'],
      eventTypes: ['ResourceStateChangeEvent']
    }
  }));
};

ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  
  if (event.eventType === 'ResourceStateChangeEvent') {
    const cell = event.event.resource;
    const change = event.event.stateChange;
    
    console.log(`Cell ${cell.name}: ${change.previousState} → ${change.newState}`);
    
    if (change.newState === 'Ready') {
      console.log('Cell site provisioning complete');
    } else if (change.newState === 'Failed') {
      console.error('Cell site provisioning failed');
    }
  }
};
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
    "message": "Resource with id 'cellsite:unknown' not found",
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
  │ (Cell sites) │  │   (INTENT   │  │ Subscribe   │
  │ (UE capacity)│  │    API)     │  │ (Events)    │
  │ (Core refs)  │  │             │  │             │
  └─────────────┘  └─────────────┘  └─────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
         ┌────────────────────────────────┐
         │  Radio Domain (Internal)       │
         │  - gNodeB provisioning         │
         │  - UE registration             │
         │  - AMF/UPF connectivity        │
         └────────────────────────────────┘
```

**Workflow:**
1. **Discovery** — Query existing cell sites and UE capacity by zone
2. **Planning** — Submit cell site deployment intent via [Radio Intent API](INTENT.md)
3. **Monitoring** — Subscribe to events for provisioning status and capacity alerts

---

## Notes for Northbound Developers

- **Coverage zones:** Cell sites are tagged with coverage zones (e.g., "Downtown London") for geographic planning.
- **UE capacity:** Use UESummary resources for capacity planning; individual UE details are not exposed.
- **Core connectivity:** Cell sites reference their connected 5G core via `core_name` — verify core exists before deploying cells.
- **Intent-based provisioning:** Use the [Radio Intent API](INTENT.md) to deploy cell sites — this inventory API is read-only.
- **Real-time capacity monitoring:** Subscribe to UESummary events to detect capacity thresholds.
- **Cross-domain validation:** Radio intents validate against core PLMN, slices, and subscribers — ensure core is provisioned first.
