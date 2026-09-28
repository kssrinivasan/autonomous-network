# Service Domain Orchestrator API

REST service that accepts TMF921 network slice intents from northbound systems and decomposes them into coordinated Transport, Core, and Radio domain intents for end-to-end service provisioning.

---

## Architecture

This service implements a **TMF921 Intent Management** interface for end-to-end network slice orchestration. It provides the highest level of abstraction, allowing business stakeholders to request network slices based on geographic coverage and SLA requirements without understanding underlying infrastructure.

```
Northbound system  ──TMF921 NetworkSliceIntent──▶  Service API
                                                      ├─▶ Transport API (L3VPN backhaul)
                                                      ├─▶ Core API (5G service)
                                                      └─▶ Radio API (cell sites)
                                                           ↓
                                                    Multi-domain orchestration
                                                           ↓
                                                    Kubernetes CRs (VyOSL3VPN, FiveGCore, CellSite)
```

**Key principle:** Northbound callers express **business outcomes** (coverage area, capacity, SLA) and the orchestration engine decomposes this into coordinated domain-specific intents.

---

## TMF921 Intent Model

### Envelope

Every request and response uses this envelope:

```json
{
  "@type": "Intent",
  "id": "<server-assigned uuid>",
  "name": "<human-readable label>",
  "description": "<optional free text>",
  "priority": 50,
  "state": "Acknowledged | InProgress | Completed | Rejected | Inactive",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [ ]
  }
}
```

**State machine:**

```
POST → Acknowledged → InProgress → Completed
                               └─→ Rejected
DELETE → Inactive
```

`id` is assigned by the server on `POST` and must be supplied on all subsequent calls.

### Expectation Type: NetworkSliceIntent

Requests end-to-end network slice provisioning across transport, core, and radio domains for a specified geographic area.

```json
{
  "@type": "NetworkSliceIntent",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "@type": "NetworkSliceService",
    "sliceName": "London Enhanced Mobile Broadband",
    "operator": "TelecomCo UK",
    "serviceType": "eMBB",
    "coverageArea": {
      "region": "Greater London",
      "zones": [
        {
          "name": "Central London",
          "priority": "high"
        },
        {
          "name": "West London",
          "priority": "medium"
        },
        {
          "name": "East London",
          "priority": "medium"
        }
      ],
      "totalArea": "1600 sq km"
    },
    "capacity": {
      "totalSubscribers": 50000,
      "peakConcurrentUsers": 5000,
      "aggregateThroughput": "10 Gbps"
    },
    "sla": {
      "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
      "uploadSpeed": "50 Mbps typical",
      "latency": "< 20ms",
      "availability": "99.99%",
      "jitter": "< 5ms"
    },
    "backhaul": {
      "bandwidth": "10 Gbps aggregate",
      "latency": "< 5ms",
      "redundancy": "dual-path"
    }
  }
}
```

**Field Descriptions:**

| Field | Description | Required |
|-------|-------------|----------|
| `sliceName` | Human-readable service name | Yes |
| `operator` | Network operator name | Yes |
| `serviceType` | eMBB, URLLC, or mMTC | Yes |
| `coverageArea.region` | Primary geographic region | Yes |
| `coverageArea.zones[]` | Coverage zones with priority levels | Yes |
| `coverageArea.totalArea` | Approximate coverage area | No |
| `capacity.totalSubscribers` | Total subscriber capacity | Yes |
| `capacity.peakConcurrentUsers` | Peak concurrent active users | Yes |
| `capacity.aggregateThroughput` | Total network throughput | Yes |
| `sla.downloadSpeed` | Download speed requirements | Yes |
| `sla.uploadSpeed` | Upload speed requirements | Yes |
| `sla.latency` | Maximum latency | Yes |
| `sla.availability` | Service availability SLA | Yes |
| `backhaul.bandwidth` | Backhaul network capacity | Yes |
| `backhaul.latency` | Backhaul latency requirement | Yes |

---

## Orchestration Decomposition

When a NetworkSliceIntent is submitted, the orchestration engine decomposes it into domain-specific intents:

### Step 1: Transport Intent (Backhaul L3VPN)

**Purpose:** Create backhaul connectivity between core network and cell sites.

**Orchestrator derives:**
- Identifies PE routers serving the coverage region
- Calculates required backhaul bandwidth (sum of cell site throughput)
- Maps SLA requirements to transport QoS

**Generated Transport Intent:**
```json
{
  "@type": "L3ConnectivityIntent",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "serviceName": "London eMBB Backhaul",
    "topology": "any-to-any",
    "endpoints": [
      {"peRouter": "pe1", "peInterface": "eth2", "customerRouter": "ce-core-london"},
      {"peRouter": "pe2", "peInterface": "eth3", "customerRouter": "ce-cell-central"},
      {"peRouter": "pe3", "peInterface": "eth2", "customerRouter": "ce-cell-west"},
      {"peRouter": "pe4", "peInterface": "eth2", "customerRouter": "ce-cell-east"}
    ],
    "sla": {
      "bandwidth": "10 Gbps per endpoint",
      "latency": "< 5ms",
      "availability": "99.99%"
    }
  }
}
```

### Step 2: Core Intent (5G Core Service)

**Purpose:** Deploy 5G core network functions to serve the slice.

**Orchestrator derives:**
- PLMN allocation for operator/region
- Network slice configuration (SST/SD based on service type)
- Network function deployment plan (count based on capacity)
- Subscriber provisioning strategy

**Generated Core Intent:**
```json
{
  "@type": "MobileServiceIntent",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "serviceName": "London 5G eMBB Core",
    "operator": "TelecomCo UK",
    "region": "Greater London",
    "serviceType": "eMBB",
    "capacity": {
      "totalSubscribers": 50000,
      "initialSubscribers": 10000,
      "peakConcurrentUsers": 5000,
      "aggregateThroughput": "10 Gbps"
    },
    "serviceLevel": {
      "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
      "latency": "< 20ms",
      "reliability": "99.99%"
    }
  }
}
```

### Step 3: Radio Intents (Cell Sites per Zone)

**Purpose:** Deploy cell sites to provide coverage in each zone.

**Orchestrator derives:**
- gNodeB count per zone (based on area, terrain, user density)
- Cell site placement (one intent per major zone)
- Capacity distribution across zones

**Generated Radio Intents (one per zone):**
```json
[
  {
    "@type": "CellSiteIntent",
    "expectationVerb": "DELIVER",
    "expectationObject": {
      "siteName": "Central London Cell Sites",
      "coverageZone": "Central London",
      "deployment": {"gNodeBCount": 20},
      "capacity": {"peakUsers": 2000, "throughput": "4 Gbps"},
      "serviceLink": {"mobileService": "London 5G eMBB Core"}
    }
  },
  {
    "@type": "CellSiteIntent",
    "expectationVerb": "DELIVER",
    "expectationObject": {
      "siteName": "West London Cell Sites",
      "coverageZone": "West London",
      "deployment": {"gNodeBCount": 15},
      "capacity": {"peakUsers": 1500, "throughput": "3 Gbps"},
      "serviceLink": {"mobileService": "London 5G eMBB Core"}
    }
  },
  {
    "@type": "CellSiteIntent",
    "expectationVerb": "DELIVER",
    "expectationObject": {
      "siteName": "East London Cell Sites",
      "coverageZone": "East London",
      "deployment": {"gNodeBCount": 15},
      "capacity": {"peakUsers": 1500, "throughput": "3 Gbps"},
      "serviceLink": {"mobileService": "London 5G eMBB Core"}
    }
  }
]
```

---

## REST API Reference

Base URL: `http://<service-api-host>:8080`

### POST /intent

Submit a new network slice intent.

**Request body:** TMF921 Intent envelope with NetworkSliceIntent.

**Responses:**

| Status | Meaning |
|--------|---------|
| 202 | Intent accepted; `id` and `state: Acknowledged` added to the response body |
| 400 | Validation error — missing or invalid fields |
| 409 | Conflict — Network slice with this name already exists |
| 500 | Internal error |

**Example:**

```bash
curl -X POST http://localhost:8080/intent \
  -H 'Content-Type: application/json' \
  -d '{
    "@type": "Intent",
    "name": "london-embb-slice",
    "description": "eMBB network slice for Greater London",
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [
        {
          "@type": "NetworkSliceIntent",
          "expectationVerb": "DELIVER",
          "expectationObject": {
            "@type": "NetworkSliceService",
            "sliceName": "London Enhanced Mobile Broadband",
            "operator": "TelecomCo UK",
            "serviceType": "eMBB",
            "coverageArea": {
              "region": "Greater London",
              "zones": [
                {"name": "Central London", "priority": "high"},
                {"name": "West London", "priority": "medium"},
                {"name": "East London", "priority": "medium"}
              ]
            },
            "capacity": {
              "totalSubscribers": 50000,
              "peakConcurrentUsers": 5000,
              "aggregateThroughput": "10 Gbps"
            },
            "sla": {
              "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
              "latency": "< 20ms",
              "availability": "99.99%"
            }
          }
        }
      ]
    }
  }'
```

**Response (202):**

```json
{
  "@type": "Intent",
  "id": "d6i5h4j7-8k9l-0m1n-2o3p-4q5r6s7t8u9v",
  "name": "london-embb-slice",
  "state": "Acknowledged",
  "message": "Network slice intent received; decomposing into domain intents",
  "intentExpression": { "..." }
}
```

---

### GET /intents

List all active network slice intents.

```bash
curl http://localhost:8080/intents
```

**Response (200):**
```json
{
  "intents": [
    {
      "@type": "Intent",
      "id": "d6i5h4j7-...",
      "name": "london-embb-slice",
      "state": "Completed",
      "intentExpression": { "..." }
    }
  ]
}
```

---

### GET /intent/{id}

Get the current state of a network slice intent, including aggregated status from all domain intents.

```bash
curl http://localhost:8080/intent/d6i5h4j7-...
```

**Response (200):** Full intent record with live `state` aggregated from underlying domain intents.

```json
{
  "@type": "Intent",
  "id": "d6i5h4j7-...",
  "name": "london-embb-slice",
  "state": "Completed",
  "message": "All domain intents completed successfully",
  "decomposedIntents": {
    "transport": {
      "intentId": "transport-001",
      "state": "Completed",
      "serviceName": "London eMBB Backhaul"
    },
    "core": {
      "intentId": "core-001",
      "state": "Completed",
      "serviceName": "London 5G eMBB Core"
    },
    "radio": [
      {
        "intentId": "radio-001",
        "state": "Completed",
        "siteName": "Central London Cell Sites"
      },
      {
        "intentId": "radio-002",
        "state": "Completed",
        "siteName": "West London Cell Sites"
      },
      {
        "intentId": "radio-003",
        "state": "Completed",
        "siteName": "East London Cell Sites"
      }
    ]
  },
  "intentExpression": { "..." }
}
```

**State Aggregation Logic:**

| Domain Intent States | NetworkSliceIntent State |
|----------------------|--------------------------|
| All Acknowledged | Acknowledged |
| Any InProgress | InProgress |
| All Completed | Completed |
| Any Rejected | Rejected |
| All Inactive | Inactive |

---

### DELETE /intent/{id}

Revoke the network slice intent and delete all associated domain intents (cascade delete).

```bash
curl -X DELETE http://localhost:8080/intent/d6i5h4j7-...
```

**Response (200):**

```json
{
  "@type": "Intent",
  "id": "d6i5h4j7-...",
  "state": "Inactive",
  "message": "Network slice revoked; all domain intents deleted"
}
```

---

## Orchestration Algorithm

When implemented, `POST /intent` will execute this algorithm:

1. **Validate** — check TMF921 envelope and NetworkSliceIntent schema
2. **Generate slice ID** — assign UUID for tracking
3. **Analyze coverage** — determine which zones/regions are involved
4. **Calculate requirements:**
   - Cell site count per zone (based on area, user density, throughput)
   - Core capacity (NF count based on subscribers, throughput)
   - Backhaul capacity (aggregate of cell site requirements)
5. **Generate Transport intent** — create L3VPN backhaul configuration
6. **Generate Core intent** — create 5G core service configuration
7. **Generate Radio intents** — create cell site intents for each zone
8. **Submit domain intents** — POST to Transport → Core → Radio APIs (in order)
9. **Store mappings** — save NetworkSliceIntent ID → domain intent IDs
10. **Monitor domain intents** — aggregate state from all domain APIs
11. **Return 202 Acknowledged**

---

## Example: London eMBB Slice

**Scenario:** Launch eMBB network slice covering Greater London with 50k subscribers.

**Step 1: Submit NetworkSliceIntent**

```bash
POST /intent
{
  "@type": "Intent",
  "name": "london-embb-slice",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "NetworkSliceIntent",
      "expectationVerb": "DELIVER",
      "expectationObject": {
        "sliceName": "London Enhanced Mobile Broadband",
        "operator": "TelecomCo UK",
        "serviceType": "eMBB",
        "coverageArea": {
          "region": "Greater London",
          "zones": [
            {"name": "Central London", "priority": "high"},
            {"name": "West London", "priority": "medium"},
            {"name": "East London", "priority": "medium"}
          ],
          "totalArea": "1600 sq km"
        },
        "capacity": {
          "totalSubscribers": 50000,
          "peakConcurrentUsers": 5000,
          "aggregateThroughput": "10 Gbps"
        },
        "sla": {
          "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
          "latency": "< 20ms",
          "availability": "99.99%"
        },
        "backhaul": {
          "bandwidth": "10 Gbps aggregate",
          "latency": "< 5ms"
        }
      }
    }]
  }
}
```

**Response:**
```json
{
  "id": "slice-001",
  "state": "Acknowledged"
}
```

**Step 2: Orchestrator decomposes and submits domain intents**

```
1. POST to Transport API → creates backhaul L3VPN
2. POST to Core API → creates 5G core service
3. POST to Radio API (3x) → creates cell sites for each zone
```

**Step 3: Poll aggregate status**

```bash
GET /intent/slice-001
```

**Response (in progress):**
```json
{
  "id": "slice-001",
  "state": "InProgress",
  "decomposedIntents": {
    "transport": {"state": "Completed"},
    "core": {"state": "InProgress"},
    "radio": [
      {"state": "InProgress"},
      {"state": "InProgress"},
      {"state": "Acknowledged"}
    ]
  }
}
```

**Step 4: All domains complete**

```bash
GET /intent/slice-001
```

**Response:**
```json
{
  "id": "slice-001",
  "state": "Completed",
  "message": "Network slice operational across all domains"
}
```

---

## Error Handling

**400 Bad Request**
- Missing `@type: Intent` in envelope
- Missing `intentExpression`
- Missing required fields in NetworkSliceService
- Invalid service type (must be eMBB, URLLC, or mMTC)

**409 Conflict**
- Network slice with same name already exists

**424 Failed Dependency**
- Domain API unavailable (Transport, Core, or Radio)
- Insufficient infrastructure in coverage region

**500 Internal Server Error**
- Unable to decompose intent
- Domain intent submission failed

---

## Implementation Notes

When building the orchestration engine:

1. **Maintain zone/region registry** mapping geographic names to infrastructure
2. **Implement placement algorithms** for cell site count calculation
3. **Calculate capacity requirements** (NF sizing, backhaul bandwidth)
4. **Submit domain intents in order** (Transport → Core → Radio)
5. **Aggregate state** from multiple domain intent APIs
6. **Handle partial failures** (e.g., Transport succeeds but Core fails → rollback)
7. **Monitor domain intents** via polling or webhooks

**Future enhancements:**
- Multi-region slices
- Slice modification (add/remove zones)
- Automatic scaling based on usage
- Cost optimization (minimize infrastructure while meeting SLA)
- Slice inter-connection (slice routing)
