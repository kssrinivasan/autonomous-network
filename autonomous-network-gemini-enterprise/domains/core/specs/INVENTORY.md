# Core Domain Inventory API (TMF639)

**Northbound API** for discovering available 5G core network infrastructure to support mobile service deployment planning.

---

## Overview

**Purpose:** Enable northbound systems (e.g., service orchestrator, radio orchestrator) to discover deployed 5G cores, network function availability, and PLMN/slice configurations when planning mobile services.

**Intended Consumers:** Service domain orchestrator, radio domain orchestrator, network planning systems

**Spanner Database:** `coretopology-db`

**TMF Alignment:** TMF639 Resource Inventory Management API v4.0

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/core`

**What is Exposed:**
- **5G Core instances** with PLMN and slice configurations
- **Network Function availability** (AMF, SMF, UPF status for radio attachment planning)
- **Slice and subscriber summaries** (capacity planning)

**What is NOT Exposed:**
- Internal container details (IP addresses, process IDs, MongoDB URIs)
- Low-level NF configurations (Ansible playbooks, config files)
- Individual subscriber credentials (keys, OPC values)

**Next Steps:** After discovering core availability, use the [Core Intent API](INTENT.md) to deploy new 5G cores or the [Radio Intent API](../../radio/specs/INTENT.md) to attach cell sites.

---

## Resource Models

### FiveGCore

**Description:** 5G core network instance with PLMN, network slices, and network functions.

**TMF639 Resource Schema:**
```json
{
  "@type": "FiveGCore",
  "id": "fivegcore:free5gc-core",
  "href": "/tmf-api/resourceInventoryManagement/v4/core/resource/fivegcore:free5gc-core",
  "name": "free5gc-core",
  "resourceSpecification": {
    "id": "FiveGCore",
    "name": "5G Core Network"
  },
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "mcc", "value": "234"},
    {"name": "mnc", "value": "15"},
    {"name": "slice_count", "value": 2},
    {"name": "subscriber_count", "value": 1000},
    {"name": "nf_count", "value": 8}
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
| `mcc`, `mnc` | PLMN (Public Land Mobile Network) | Match to radio cell site PLMN |
| `slice_count` | Number of network slices configured | Capacity indicator |
| `subscriber_count` | Total subscribers provisioned | Capacity tracking |
| `nf_count` | Number of network functions | Deployment completeness |

**Resource Status Values:**
- `Ready` — 5G core is fully operational with all NFs ready
- `InProgress` — 5G core is being provisioned (intent InProgress)
- `Degraded` — Some network functions are not ready
- `Failed` — 5G core provisioning failed

---

### NetworkFunction

**Description:** Individual 5G network function availability (AMF, SMF, UPF, NRF, etc.).

**TMF639 Resource Schema:**
```json
{
  "@type": "NetworkFunction",
  "id": "nf:free5gc-core-amf",
  "href": "/tmf-api/resourceInventoryManagement/v4/core/resource/nf:free5gc-core-amf",
  "name": "free5gc-core-amf",
  "resourceSpecification": {
    "id": "NetworkFunction",
    "name": "5G Network Function"
  },
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "nf_type", "value": "AMF"},
    {"name": "fivegcore_id", "value": "fivegcore:free5gc-core"}
  ],
  "relatedParty": [
    {
      "id": "fivegcore:free5gc-core",
      "href": "/tmf-api/resourceInventoryManagement/v4/core/resource/fivegcore:free5gc-core",
      "role": "parent",
      "name": "free5gc-core"
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
| `nf_type` | AMF, SMF, UPF, NRF, AUSF, UDM, PCF, UDR | Network function role |
| `fivegcore_id` | Parent 5G core instance | Group NFs by core |

**NF Types (Northbound Relevance):**
- **AMF** (Access and Mobility Management) — Required for radio cell attachment
- **SMF** (Session Management) — Required for UE data sessions
- **UPF** (User Plane Function) — Required for UE data path
- **NRF** (Network Repository Function) — Service discovery (internal)

**Resource Status Values:**
- `Ready` — NF is operational and accepting connections
- `Creating` — NF is being provisioned
- `Failed` — NF provisioning failed
- `NotReady` — NF exists but not yet ready

**Note:** Radio domain requires AMF, SMF, and UPF to be Ready before deploying cell sites.

---

### SliceSummary

**Description:** Network slice configuration summary (DNN, capacity, subscribers).

**TMF639 Resource Schema:**
```json
{
  "@type": "SliceSummary",
  "id": "slice:free5gc-core:1:010203",
  "href": "/tmf-api/resourceInventoryManagement/v4/core/resource/slice:free5gc-core:1:010203",
  "name": "free5gc-core-slice-1-010203",
  "resourceSpecification": {
    "id": "SliceSummary",
    "name": "Network Slice Summary"
  },
  "resourceStatus": "Active",
  "resourceCharacteristic": [
    {"name": "fivegcore_id", "value": "fivegcore:free5gc-core"},
    {"name": "sst", "value": 1},
    {"name": "sd", "value": "010203"},
    {"name": "dnn_list", "value": "internet,ims"},
    {"name": "subscriber_count", "value": 500},
    {"name": "ip_pool", "value": "10.60.0.0/16"}
  ],
  "relatedParty": [
    {
      "id": "fivegcore:free5gc-core",
      "href": "/tmf-api/resourceInventoryManagement/v4/core/resource/fivegcore:free5gc-core",
      "role": "parent",
      "name": "free5gc-core"
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
| `sst`, `sd` | S-NSSAI (Slice/Service Type, Slice Differentiator) | Match to radio slice config |
| `dnn_list` | Data Network Names (APNs) | Validate UE APN against available DNNs |
| `subscriber_count` | Subscribers in this slice | Capacity tracking |
| `ip_pool` | UE IP address pool | IP capacity planning |

**Note:** Radio intents must specify matching SST/SD and DNN values that exist in the core's slice configuration.

---

## REST API Reference

### GET /resource

List resources with optional filtering.

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/core/resource`

**Query Parameters:**

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `resourceSpecification` | string | Filter by resource type | `FiveGCore` |
| `resourceStatus` | string | Filter by status | `Ready` |
| `mcc` | string | Filter by Mobile Country Code | `234` |
| `mnc` | string | Filter by Mobile Network Code | `15` |
| `nfType` | string | Filter NFs by type | `AMF` |
| `fivegcoreId` | string | Filter by parent core | `fivegcore:free5gc-core` |
| `limit` | integer | Page size (max 100) | `20` |
| `offset` | integer | Page offset | `0` |

**Response Schema:**
```json
{
  "resourceInventory": [
    {
      "@type": "FiveGCore",
      "id": "fivegcore:free5gc-core",
      ...
    }
  ],
  "totalCount": 5,
  "limit": 20,
  "offset": 0
}
```

---

### GET /resource/{id}

Get a specific resource by ID.

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/core/resource/{id}`

**Path Parameters:**
- `id` (required) - Resource ID (e.g., `fivegcore:free5gc-core`)

**Response Schema:** Single TMF639 Resource object

**Example:**

```bash
GET /tmf-api/resourceInventoryManagement/v4/core/resource/fivegcore:free5gc-core

Response:
{
  "@type": "FiveGCore",
  "id": "fivegcore:free5gc-core",
  "name": "free5gc-core",
  "resourceStatus": "Ready",
  "resourceCharacteristic": [
    {"name": "mcc", "value": "234"},
    {"name": "mnc", "value": "15"},
    {"name": "slice_count", "value": 2},
    {"name": "subscriber_count", "value": 1000}
  ]
}
```

---

## Event Notifications (TMF688)

Real-time notifications for 5G core deployment and network function status changes.

### Event Types

#### ResourceStateChangeEvent (FiveGCore)

**Trigger:** 5G core status changed (e.g., InProgress → Ready, Ready → Degraded)

**Use Case:** Northbound systems monitor 5G core provisioning after submitting core intents.

**Schema:**
```json
{
  "eventId": "b2c3d4e5-6f7g-8h9i-0j1k-2l3m4n5o6p7q",
  "eventTime": "2026-08-12T10:16:45.456Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "core",
  "event": {
    "resource": {
      "@type": "FiveGCore",
      "id": "fivegcore:free5gc-core",
      "resourceStatus": "Ready"
    },
    "stateChange": {
      "previousState": "InProgress",
      "newState": "Ready"
    }
  }
}
```

#### ResourceStateChangeEvent (NetworkFunction)

**Trigger:** Network function status changed (e.g., Creating → Ready, Ready → Failed)

**Use Case:** Radio orchestrator monitors AMF/SMF/UPF readiness before deploying cell sites.

**Schema:**
```json
{
  "eventId": "a1b2c3d4-5e6f-7g8h-9i0j-1k2l3m4n5o6p",
  "eventTime": "2026-08-12T10:15:30.123Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "core",
  "priority": "High",
  "event": {
    "resource": {
      "@type": "NetworkFunction",
      "id": "nf:free5gc-core-amf",
      "resourceStatus": "Ready",
      "resourceCharacteristic": [
        {"name": "nf_type", "value": "AMF"}
      ]
    },
    "stateChange": {
      "previousState": "Creating",
      "newState": "Ready"
    }
  }
}
```

---

### Subscription Mechanisms

#### WebSocket Subscription

**URL:** `ws://<api-host>/tmf-api/resourceInventoryManagement/v4/core/events`

**Subscribe to 5G Core State Changes:**
```json
{
  "action": "subscribe",
  "subscriptionId": "core-monitor",
  "filters": {
    "resourceSpecification": ["FiveGCore"],
    "eventTypes": ["ResourceStateChangeEvent"]
  }
}
```

**Subscribe to Network Function State Changes (Critical NFs Only):**
```json
{
  "action": "subscribe",
  "subscriptionId": "nf-monitor",
  "filters": {
    "resourceSpecification": ["NetworkFunction"],
    "nfType": ["AMF", "SMF", "UPF"],
    "eventTypes": ["ResourceStateChangeEvent"]
  }
}
```

#### Webhook Callbacks

**URL:** `POST /tmf-api/resourceInventoryManagement/v4/core/hub`

**Register Webhook for Core Provisioning:**
```json
{
  "callback": "https://service-orchestrator.example.com/webhook/core",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=FiveGCore"
}
```

**Event Delivery:** The API will POST events to your callback URL.

---

## Northbound Use Cases

### Use Case 1: Query Available 5G Cores by PLMN

**Scenario:** Service orchestrator needs to find a 5G core for PLMN 234/15.

**Step 1: Query 5G cores by PLMN**
```bash
GET /tmf-api/resourceInventoryManagement/v4/core/resource?
    resourceSpecification=FiveGCore&
    mcc=234&
    mnc=15&
    resourceStatus=Ready
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "FiveGCore",
      "id": "fivegcore:free5gc-core",
      "name": "free5gc-core",
      "resourceCharacteristic": [
        {"name": "mcc", "value": "234"},
        {"name": "mnc", "value": "15"},
        {"name": "slice_count", "value": 2},
        {"name": "subscriber_count", "value": 1000}
      ]
    }
  ],
  "totalCount": 1
}
```

**Interpretation:** Core "free5gc-core" is available for PLMN 234/15 with 2 slices configured.

---

### Use Case 2: Verify Network Functions Ready for Radio Attachment

**Scenario:** Radio orchestrator needs to verify AMF, SMF, and UPF are ready before deploying cell sites.

**Step 1: Query critical network functions**
```bash
GET /tmf-api/resourceInventoryManagement/v4/core/resource?
    resourceSpecification=NetworkFunction&
    fivegcoreId=fivegcore:free5gc-core&
    nfType=AMF,SMF,UPF
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "NetworkFunction",
      "id": "nf:free5gc-core-amf",
      "name": "free5gc-core-amf",
      "resourceStatus": "Ready",
      "resourceCharacteristic": [
        {"name": "nf_type", "value": "AMF"}
      ]
    },
    {
      "@type": "NetworkFunction",
      "id": "nf:free5gc-core-smf",
      "resourceStatus": "Ready",
      "resourceCharacteristic": [
        {"name": "nf_type", "value": "SMF"}
      ]
    },
    {
      "@type": "NetworkFunction",
      "id": "nf:free5gc-core-upf",
      "resourceStatus": "Ready",
      "resourceCharacteristic": [
        {"name": "nf_type", "value": "UPF"}
      ]
    }
  ],
  "totalCount": 3
}
```

**Validation:** All required NFs (AMF, SMF, UPF) are Ready → safe to deploy cell sites.

**Step 2: Submit Radio Intent**

After verification, submit cell site intent via [Radio Intent API](../../radio/specs/INTENT.md) referencing `coreName: "free5gc-core"`.

---

### Use Case 3: Check Slice Configuration for Radio Intent

**Scenario:** Radio orchestrator needs to validate slice SST/SD before deploying cell sites.

**Step 1: Query slice summaries**
```bash
GET /tmf-api/resourceInventoryManagement/v4/core/resource?
    resourceSpecification=SliceSummary&
    fivegcoreId=fivegcore:free5gc-core
```

**Response:**
```json
{
  "resourceInventory": [
    {
      "@type": "SliceSummary",
      "id": "slice:free5gc-core:1:010203",
      "resourceCharacteristic": [
        {"name": "sst", "value": 1},
        {"name": "sd", "value": "010203"},
        {"name": "dnn_list", "value": "internet,ims"},
        {"name": "ip_pool", "value": "10.60.0.0/16"}
      ]
    }
  ]
}
```

**Validation:** 
- Slice SST=1, SD=010203 exists
- DNN "internet" is available
- IP pool 10.60.0.0/16 has capacity

Radio intent can specify:
```json
{
  "gnb": {
    "slices": [{"sst": 1, "sd": "010203"}]
  },
  "ues": [
    {"imsi": "234150000000001", "apn": "internet", "sst": 1, "sd": "010203"}
  ]
}
```

---

### Use Case 4: Monitor Core Provisioning Status

**Scenario:** After submitting a core intent, monitor 5G core and NF provisioning via events.

**WebSocket Subscription:**
```javascript
const ws = new WebSocket('ws://api-host/tmf-api/resourceInventoryManagement/v4/core/events');

ws.onopen = () => {
  // Monitor both FiveGCore and NetworkFunction state changes
  ws.send(JSON.stringify({
    action: 'subscribe',
    subscriptionId: 'core-provisioning',
    filters: {
      resourceSpecification: ['FiveGCore', 'NetworkFunction'],
      eventTypes: ['ResourceStateChangeEvent']
    }
  }));
};

ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  
  if (event.eventType === 'ResourceStateChangeEvent') {
    const resource = event.event.resource;
    const change = event.event.stateChange;
    
    if (resource['@type'] === 'FiveGCore') {
      console.log(`Core ${resource.name}: ${change.previousState} → ${change.newState}`);
      
      if (change.newState === 'Ready') {
        console.log('5G core fully operational');
      }
    } else if (resource['@type'] === 'NetworkFunction') {
      const nfType = resource.resourceCharacteristic.find(c => c.name === 'nf_type')?.value;
      console.log(`NF ${nfType}: ${change.previousState} → ${change.newState}`);
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
    "message": "Resource with id 'fivegcore:unknown' not found",
    "status": 404
  }
}
```

---

## API Workflow Summary

```
┌─────────────────────────────────────────────────────────────┐
│ Northbound Systems                                           │
│  - Service Orchestrator (deploy cores)                      │
│  - Radio Orchestrator (verify NF availability)              │
└─────────────────────────────────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
         ▼                ▼                ▼
  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
  │  Discovery  │  │  Planning   │  │ Monitoring  │
  │             │  │             │  │             │
  │ GET /resource│  │ POST /intent│  │ WebSocket   │
  │ (5G cores)   │  │   (INTENT   │  │ Subscribe   │
  │ (NFs: AMF/   │  │    API)     │  │ (Events)    │
  │  SMF/UPF)    │  │             │  │             │
  │ (Slices)     │  │             │  │             │
  └─────────────┘  └─────────────┘  └─────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
         ┌────────────────────────────────┐
         │  Core Domain (Internal)        │
         │  - NF provisioning (free5gc)   │
         │  - MongoDB setup               │
         │  - Subscriber management       │
         └────────────────────────────────┘
```

**Workflow:**
1. **Discovery** — Query available 5G cores, network functions, and slices
2. **Planning** — Submit core deployment intent via [Core Intent API](INTENT.md)
3. **Monitoring** — Subscribe to events for provisioning status and NF availability

---

## Notes for Northbound Developers

- **PLMN matching:** Radio cell sites must match core PLMN (MCC/MNC) — query cores by PLMN before radio planning.
- **NF dependencies:** Radio requires AMF, SMF, and UPF to be Ready — query NetworkFunction resources before cell deployment.
- **Slice validation:** Radio UEs must reference existing slices (SST/SD) and DNNs — query SliceSummary before configuring UEs.
- **Cross-domain naming:** NetworkFunction names follow pattern `{coreId}-{nfType}` (e.g., "free5gc-core-amf") — use this pattern in radio intents.
- **Intent-based provisioning:** Use the [Core Intent API](INTENT.md) to deploy 5G cores — this inventory API is read-only.
- **Real-time NF monitoring:** Subscribe to NetworkFunction events to detect failures affecting radio connectivity.
