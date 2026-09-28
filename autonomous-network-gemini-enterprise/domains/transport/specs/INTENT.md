# Transport Domain Orchestrator API

REST service that accepts TMF921 network intents from northbound systems and translates them into VyOS Kubernetes CR operations for Layer 3 VPN provisioning.

---

## Architecture — IETF ACTN Alignment

This service implements a **TMF921 Intent Management** interface aligned with **IETF ACTN (Abstraction and Control of TE Networks) RFC 8453** architecture for transport network services.

```
Northbound system  ──TMF921 intent──▶  Transport API (MDSC)  ──VyOSL3VPN CR──▶  K8s Operator (PNC)  ──Ansible──▶  VyOS routers
     (CNC)                                 MDSC                                      PNC                          Network Elements
```

**ACTN Layers:**
- **CNC (Customer Network Controller):** Northbound system that submits L3 connectivity intents
- **MDSC (Multi-Domain Service Coordinator):** This Transport API — translates intents to infrastructure configurations
- **PNC (Physical Network Controller):** Kubernetes Operator — provisions configurations on network elements

**Key principle:** Northbound callers specify **what** connectivity they need (PE routers, customer interfaces, SLA requirements). The MDSC orchestrator derives **how** to implement it (VRF names, RD/RT values, BGP neighbors, QoS policies).

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

### Expectation Type: L3ConnectivityIntent

Requests Layer 3 VPN service between specified PE routers with defined endpoints and SLA requirements. The orchestrator derives VRF configurations, RD/RT allocation, and BGP neighbor details.

```json
{
  "@type": "L3ConnectivityIntent",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "@type": "L3VPNService",
    "serviceName": "Corporate WAN",
    "topology": "any-to-any",
    "endpoints": [
      {
        "peRouter": "pe1",
        "peInterface": "eth2",
        "customerRouter": "ce-core1",
        "customerPrefix": "10.100.0.0/24",
        "customerVlan": 100
      },
      {
        "peRouter": "pe2",
        "peInterface": "eth3",
        "customerRouter": "ce-cell1",
        "customerPrefix": "10.101.0.0/24",
        "customerVlan": 101
      },
      {
        "peRouter": "pe3",
        "peInterface": "eth2",
        "customerRouter": "ce-cell2",
        "customerPrefix": "10.102.0.0/24",
        "customerVlan": 102
      },
      {
        "peRouter": "pe4",
        "peInterface": "eth2",
        "customerRouter": "ce-cell3",
        "customerPrefix": "10.103.0.0/24",
        "customerVlan": 103
      }
    ],
    "sla": {
      "bandwidth": "100 Mbps per endpoint",
      "latency": "< 10ms",
      "availability": "99.99%",
      "trafficClass": "business-critical"
    }
  }
}
```

**What you specify (ACTN CNC role):**
- PE router names and interfaces
- Customer-facing details (CE routers, customer prefixes, VLANs)
- VPN topology (any-to-any, hub-spoke, point-to-point)
- SLA requirements (bandwidth, latency, availability, traffic class)

**What the orchestrator derives (MDSC role):**
- VRF name (auto-generated from serviceName or allocated from pool)
- Route Distinguisher (RD) per PE router
- Route Target (RT) values (common RT for any-to-any topology)
- BGP neighbor configurations (derived from topology + customer routers)
- QoS policies (maps trafficClass to VyOS queue configuration)
- Validates VyOSUnderlay CR is Ready before proceeding

**Intent Field Descriptions:**

| Field | Description | Required |
|-------|-------------|----------|
| `serviceName` | Human-readable service name | Yes |
| `topology` | VPN topology: any-to-any, hub-spoke, point-to-point | Yes |
| `endpoints[]` | PE-CE connection points | Yes (min 2) |
| `endpoints[].peRouter` | Provider Edge router name | Yes |
| `endpoints[].peInterface` | PE interface facing customer | Yes |
| `endpoints[].customerRouter` | Customer Edge router name | Yes |
| `endpoints[].customerPrefix` | Customer IP prefix to advertise | Yes |
| `endpoints[].customerVlan` | Customer VLAN ID (optional) | No |
| `sla.bandwidth` | Guaranteed bandwidth per endpoint or aggregate | Yes |
| `sla.latency` | Maximum latency requirement | Yes |
| `sla.availability` | Service availability SLA | Yes |
| `sla.trafficClass` | business-critical, standard, best-effort | Yes |

**Orchestrator-Derived Configuration:**

The orchestrator automatically generates:

| Derived Field | Derivation Logic |
|---------------|------------------|
| VRF name | Auto-generated from `serviceName` or allocated from pool |
| Route Distinguisher (RD) | Unique per PE: `{pe-loopback-ip}:{vrf-id}` |
| Route Target (RT) | Based on topology: any-to-any uses common RT, hub-spoke uses import/export policies |
| BGP neighbors | Derived from `topology` + `endpoints[].customerRouter` |
| BGP AS numbers | Resolved from CE router configuration or site registry |
| QoS policies | Maps `trafficClass` → VyOS traffic shaper (e.g., business-critical → high priority queue) |
| Underlay dependency | Validates VyOSUnderlay CR specified in config is Ready |

---

## REST API Reference

Base URL: `http://<transport-api-host>:8080`

### POST /intent

Submit a new L3 VPN service intent.

**Request body:** TMF921 Intent envelope with L3ConnectivityIntent.

**Responses:**

| Status | Meaning |
|--------|---------|
| 202 | Intent accepted; `id` and `state: Acknowledged` added to the response body |
| 400 | Validation error — missing or invalid fields |
| 409 | Conflict — VyOSL3VPN CR with this serviceName already exists |
| 424 | Failed Dependency — VyOSUnderlay CR not Ready |
| 500 | Internal error |

**Example:**

```bash
curl -X POST http://localhost:8080/intent \
  -H 'Content-Type: application/json' \
  -d '{
    "@type": "Intent",
    "name": "corporate-wan",
    "description": "Any-to-any VPN connecting core and cell sites",
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [
        {
          "@type": "L3ConnectivityExpectation",
          "expectationVerb": "DELIVER",
          "expectationObject": {
            "@type": "L3VPNService",
            "serviceName": "Corporate WAN",
            "topology": "any-to-any",
            "endpoints": [
              {
                "peRouter": "pe1",
                "peInterface": "eth2",
                "customerRouter": "ce-core1",
                "customerPrefix": "10.100.0.0/24"
              },
              {
                "peRouter": "pe2",
                "peInterface": "eth3",
                "customerRouter": "ce-cell1",
                "customerPrefix": "10.101.0.0/24"
              }
            ],
            "sla": {
              "bandwidth": "100 Mbps per endpoint",
              "latency": "< 10ms",
              "availability": "99.99%",
              "trafficClass": "business-critical"
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
  "id": "a3f2c1d4-5e6f-7g8h-9i0j-1k2l3m4n5o6p",
  "name": "corporate-wan",
  "state": "Acknowledged",
  "message": "Intent received and queued for orchestration"
}
```

---

### GET /intents

List all active L3 VPN service intents.

```bash
curl http://localhost:8080/intents
```

**Response (200):**
```json
{
  "intents": [
    {
      "@type": "Intent",
      "id": "a3f2c1d4-...",
      "name": "red-mesh-vpn",
      "state": "Completed",
      "intentExpression": { "..." }
    }
  ]
}
```

---

### GET /intent/{id}

Get the current state of an L3 VPN intent, including aggregated CR status.

```bash
curl http://localhost:8080/intent/a3f2c1d4-...
```

**Response (200):** Full intent record with live `state` derived from underlying VyOSL3VPN CR phase.

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
  "intentExpression": { "..." }
}
```

**CR phase to TMF921 state mapping:**

| VyOSL3VPN CR phase | TMF921 state |
|--------------------|--------------|
| Waiting | InProgress |
| Processing | InProgress |
| Ready | Completed |
| Error | Rejected |
| (deleted) | Inactive |

---

### PATCH /intent/{id}

Update an existing intent (e.g., add endpoints, modify SLA).

```bash
curl -X PATCH http://localhost:8080/intent/a3f2c1d4-... \
  -H 'Content-Type: application/json' \
  -d '{
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [{
        "@type": "L3ConnectivityExpectation",
        "expectationVerb": "DELIVER",
        "expectationObject": {
          "@type": "L3VPNService",
          "endpoints": [
            {
              "peRouter": "pe3",
              "peInterface": "eth2",
              "customerRouter": "ce-cell2",
              "customerPrefix": "10.102.0.0/24"
            }
          ],
          "sla": {
            "bandwidth": "200 Mbps per endpoint",
            "trafficClass": "business-critical"
          }
        }
      }]
    }
  }'
```

---

### DELETE /intent/{id}

Revoke the intent and delete the VyOSL3VPN CR and all associated VRF configurations.

```bash
curl -X DELETE http://localhost:8080/intent/a3f2c1d4-...
```

**Response (200):**

```json
{
  "@type": "Intent",
  "id": "a3f2c1d4-...",
  "state": "Inactive",
  "message": "Intent revoked, VPN service deleted"
}
```

---

## Orchestration Pipeline

When implemented, `POST /intent` will drive this pipeline:

1. **Validate** — check TMF921 envelope and L3ConnectivityExpectation schema
2. **Validate underlayRef** — verify VyOSUnderlay CR exists and phase=Ready
3. **Check for conflicts** — ensure VyOSL3VPN CR with serviceName doesn't already exist
4. **Generate intent ID** — assign UUID
5. **Map TMF921 → VyOSL3VPN CR spec** — transform expectationObject to CRD format
6. **Create VyOSL3VPN CR** — apply to Kubernetes via CustomObjectsApi
7. **Store intent mapping** — save intent ID → CR name for state tracking
8. **Return 202 Acknowledged** — intent accepted, orchestration in progress

**State tracking:** The API polls VyOSL3VPN CR `status.phase` and maps to TMF921 state for `GET /intent/{id}` queries.

---

## Dependencies

**VyOSL3VPN depends on:**
- **VyOSInfrastructure** (Ready) — creates VyOSRouter CRs for PE and CE routers
- **VyOSUnderlay** (Ready) — configures OSPF/BGP/MPLS underlay routing protocols

**Dependency chain:**
```
VyOSInfrastructure → VyOSUnderlay → VyOSL3VPN
```

The VyOSL3VPN operator waits for the referenced VyOSUnderlay to reach Ready phase before configuring VRFs and BGP VPN neighbors on PE routers.

---

## Example: Multi-Site VPN

**Scenario:** Connect core network site and three cell sites via any-to-any VPN topology.

**Step 1: Submit Intent**

```bash
POST /intent
{
  "@type": "Intent",
  "name": "mobile-backhaul-vpn",
  "description": "Mobile backhaul connecting core and cell sites",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "L3ConnectivityExpectation",
      "expectationVerb": "DELIVER",
      "expectationObject": {
        "@type": "L3VPNService",
        "serviceName": "Mobile Backhaul",
        "topology": "any-to-any",
        "endpoints": [
          {
            "peRouter": "pe1",
            "peInterface": "eth2",
            "customerRouter": "ce-core1",
            "customerPrefix": "10.100.0.0/24"
          },
          {
            "peRouter": "pe2",
            "peInterface": "eth3",
            "customerRouter": "ce-cell1",
            "customerPrefix": "10.101.0.0/24"
          },
          {
            "peRouter": "pe3",
            "peInterface": "eth2",
            "customerRouter": "ce-cell2",
            "customerPrefix": "10.102.0.0/24"
          },
          {
            "peRouter": "pe4",
            "peInterface": "eth2",
            "customerRouter": "ce-cell3",
            "customerPrefix": "10.103.0.0/24"
          }
        ],
        "sla": {
          "bandwidth": "1 Gbps aggregate",
          "latency": "< 5ms",
          "availability": "99.999%",
          "trafficClass": "business-critical"
        }
      }
    }]
  }
}
```

**Response:**
```json
{
  "@type": "Intent",
  "id": "vpn-001",
  "name": "mobile-backhaul-vpn",
  "state": "Acknowledged",
  "message": "Intent received and queued for orchestration"
}
```

**Step 2: Poll Status**

```bash
GET /intent/vpn-001
```

**Response:**
```json
{
  "@type": "Intent",
  "id": "vpn-001",
  "name": "mobile-backhaul-vpn",
  "state": "InProgress",
  "message": "Configuring VPN service",
  "crStatus": {
    "phase": "Processing"
  }
}
```

**Step 3: Service Ready**

```bash
GET /intent/vpn-001
```

**Response:**
```json
{
  "@type": "Intent",
  "id": "vpn-001",
  "name": "mobile-backhaul-vpn",
  "state": "Completed",
  "message": "VPN service is Ready",
  "crStatus": {
    "phase": "Ready",
    "message": "All PE and CE routers configured successfully"
  }
}
```

---

## Error Handling

**400 Bad Request**
- Missing `@type: Intent` in envelope
- Missing `intentExpression`
- Missing `serviceName` in expectationObject
- Missing or invalid `endpoints` array (minimum 2 required)
- Missing or invalid `topology` value
- Missing `sla` requirements

**409 Conflict**
- VyOSL3VPN CR with same `serviceName` already exists

**424 Failed Dependency**
- Required VyOSUnderlay CR not found or not Ready
- One or more referenced PE routers not provisioned

**500 Internal Server Error**
- Kubernetes API connection failure
- Unable to create VyOSL3VPN CR

---

## Implementation Notes

When building the API implementation:

1. **Use Kubernetes CustomObjectsApi** for VyOSL3VPN CR operations
2. **Validate underlayRef** before creating CR (check existence and Ready status)
3. **Store intent ID → CR name mapping** in ConfigMap or in-memory (with persistence)
4. **Watch VyOSL3VPN CR status** to update intent state in real-time
5. **Use Strategic Merge Patch** for PATCH /intent operations
6. **Error handling**: Distinguish between transient (retry) and permanent (reject) errors

**Future enhancements:**
- Site-based abstraction layer (resolve siteId → PE router + interface via Spanner)
- Automatic RD/RT allocation from resource pool
- QoS policy application via QoSExpectation type
- Multi-underlay VPN stitching
