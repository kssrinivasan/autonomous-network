# Core Domain Orchestrator API

REST service that accepts TMF921 network intents from northbound systems and translates them into FiveGCore Kubernetes CR operations for 5G core network provisioning.

---

## Architecture

This service implements a **TMF921 Intent Management** interface for 5G core network deployment, including network slicing, subscriber management, and network function provisioning.

```
Northbound system  ──TMF921 intent──▶  Core API  ──FiveGCore CR──▶  K8s Operator  ──Ansible──▶  free5gc NFs
```

**Key principle:** Northbound callers express **what** 5G services they need using abstract network slice and subscriber definitions. The API translates these to FiveGCore CRs that the operator deploys across pre-provisioned Device containers.

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

### Expectation Type: FiveGCoreExpectation

Requests deployment of a complete 5G core network with specified PLMN, network slices, subscribers, and network functions.

```json
{
  "@type": "FiveGCoreExpectation",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "@type": "FiveGCoreService",
    "coreId": "free5gc-core",
    "image": "device:latest",
    "mongodbUri": "mongodb://10.102.1.21:27017",
    "plmn": {
      "mcc": "208",
      "mnc": "93"
    },
    "slices": [
      {
        "sst": 1,
        "sd": "010203",
        "dnnInfos": [
          {
            "dnn": "internet",
            "dns": {
              "ipv4": "8.8.8.8"
            },
            "pools": [
              {
                "cidr": "10.60.0.0/16"
              }
            ],
            "staticPools": [
              {
                "cidr": "10.60.100.0/24"
              }
            ]
          }
        ]
      }
    ],
    "subscribers": [
      {
        "imsi": "208930000000001",
        "msisdn": "0900000001",
        "key": "465B5CE8B199B49FAA5F0A2EE238A6BC",
        "opc": "E8ED289DEBA952E4283B54E88E6183CA",
        "apn": "internet",
        "sst": 1,
        "sd": "010203"
      }
    ],
    "networkFunctions": [
      {
        "type": "NRF",
        "containerName": "nrf"
      },
      {
        "type": "AMF",
        "containerName": "amf",
        "config": {
          "tac": 1
        }
      },
      {
        "type": "SMF",
        "containerName": "smf"
      },
      {
        "type": "UPF",
        "containerName": "upf"
      }
    ]
  }
}
```

**Field Mapping to FiveGCore CRD:**

| TMF921 Field | CRD Field | Notes |
|--------------|-----------|-------|
| `coreId` | `metadata.name` | Unique identifier for the 5G core instance |
| `image` | `spec.image` | Container image for all network functions (default: device:latest) |
| `mongodbUri` | `spec.mongodbUri` | MongoDB connection URI for UDM/UDR/PCF/NRF |
| `plmn.mcc` | `spec.plmnList[0].mcc` | Mobile Country Code (3 digits) |
| `plmn.mnc` | `spec.plmnList[0].mnc` | Mobile Network Code (2-3 digits) |
| `slices[]` | `spec.slices[]` | Network slice definitions (S-NSSAI + DNN configuration) |
| `subscribers[]` | `spec.subscribers[]` | Subscriber credentials provisioned in WebUI |
| `networkFunctions[]` | `spec.network_functions[]` | Network function deployment specs |

**Child Resource Naming:**

When the FiveGCore CR is created, the operator automatically creates child NetworkFunction CRs with names:
- `{coreId}-{nf-type}` (lowercase)

Examples:
- FiveGCore name: `free5gc-core`
- Child NetworkFunctions: `free5gc-core-amf`, `free5gc-core-smf`, `free5gc-core-upf`, `free5gc-core-nrf`, etc.

**Important:** Radio domain intents (CellSite) reference these NetworkFunction names for AMF/UPF/SMF connectivity.

---

## REST API Reference

Base URL: `http://<core-api-host>:8080`

### POST /intent

Submit a new 5G core deployment intent.

**Request body:** TMF921 Intent envelope with FiveGCoreExpectation.

**Responses:**

| Status | Meaning |
|--------|---------|
| 202 | Intent accepted; `id` and `state: Acknowledged` added to the response body |
| 400 | Validation error — missing or invalid fields |
| 409 | Conflict — FiveGCore CR with this coreId already exists |
| 424 | Failed Dependency — Referenced Device containers not Ready |
| 500 | Internal error |

**Example:**

```bash
curl -X POST http://localhost:8080/intent \
  -H 'Content-Type: application/json' \
  -d '{
    "@type": "Intent",
    "name": "free5gc-core-deployment",
    "description": "5G Core for downtown region",
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [
        {
          "@type": "FiveGCoreExpectation",
          "expectationVerb": "DELIVER",
          "expectationObject": {
            "@type": "FiveGCoreService",
            "coreId": "free5gc-core",
            "mongodbUri": "mongodb://10.102.1.21:27017",
            "plmn": {
              "mcc": "208",
              "mnc": "93"
            },
            "slices": [
              {
                "sst": 1,
                "sd": "010203",
                "dnnInfos": [
                  {
                    "dnn": "internet",
                    "dns": {
                      "ipv4": "8.8.8.8"
                    },
                    "pools": [
                      {
                        "cidr": "10.60.0.0/16"
                      }
                    ]
                  }
                ]
              }
            ],
            "subscribers": [
              {
                "imsi": "208930000000001",
                "key": "465B5CE8B199B49FAA5F0A2EE238A6BC",
                "opc": "E8ED289DEBA952E4283B54E88E6183CA",
                "apn": "internet",
                "sst": 1,
                "sd": "010203"
              }
            ],
            "networkFunctions": [
              {
                "type": "NRF",
                "containerName": "nrf"
              },
              {
                "type": "AMF",
                "containerName": "amf",
                "config": {
                  "tac": 1
                }
              },
              {
                "type": "SMF",
                "containerName": "smf"
              },
              {
                "type": "UPF",
                "containerName": "upf"
              }
            ]
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
  "id": "b4g3d2e5-6f7g-8h9i-0j1k-2l3m4n5o6p7q",
  "name": "free5gc-core-deployment",
  "state": "Acknowledged",
  "message": "Intent received and queued for orchestration",
  "intentExpression": { "..." }
}
```

---

### GET /intents

List all active 5G core deployment intents.

```bash
curl http://localhost:8080/intents
```

**Response (200):**
```json
{
  "intents": [
    {
      "@type": "Intent",
      "id": "b4g3d2e5-...",
      "name": "free5gc-core-deployment",
      "state": "Completed",
      "intentExpression": { "..." }
    }
  ]
}
```

---

### GET /intent/{id}

Get the current state of a 5G core deployment intent, including aggregated CR status.

```bash
curl http://localhost:8080/intent/b4g3d2e5-...
```

**Response (200):** Full intent record with live `state` derived from underlying FiveGCore CR phase.

```json
{
  "@type": "Intent",
  "id": "b4g3d2e5-...",
  "name": "free5gc-core-deployment",
  "state": "Completed",
  "message": "All network functions are Ready",
  "crStatus": {
    "phase": "Ready",
    "networkFunctions": [
      {
        "type": "AMF",
        "containerName": "amf",
        "phase": "Ready",
        "ready": true,
        "ipAddress": "10.102.1.10"
      },
      {
        "type": "SMF",
        "containerName": "smf",
        "phase": "Ready",
        "ready": true,
        "ipAddress": "10.102.1.11"
      },
      {
        "type": "UPF",
        "containerName": "upf",
        "phase": "Ready",
        "ready": true,
        "ipAddress": "10.102.1.15"
      }
    ]
  },
  "intentExpression": { "..." }
}
```

**CR phase to TMF921 state mapping:**

| FiveGCore CR phase | TMF921 state |
|--------------------|--------------|
| Pending | InProgress |
| Creating | InProgress |
| Ready | Completed |
| Failed | Rejected |
| (deleted) | Inactive |

---

### PATCH /intent/{id}

Update an existing intent (e.g., add subscribers, modify network functions).

```bash
curl -X PATCH http://localhost:8080/intent/b4g3d2e5-... \
  -H 'Content-Type: application/json' \
  -d '{
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [{
        "@type": "FiveGCoreExpectation",
        "expectationVerb": "DELIVER",
        "expectationObject": {
          "@type": "FiveGCoreService",
          "subscribers": [
            {
              "imsi": "208930000000002",
              "key": "465B5CE8B199B49FAA5F0A2EE238A6BC",
              "opc": "E8ED289DEBA952E4283B54E88E6183CA",
              "apn": "internet",
              "sst": 1,
              "sd": "010203"
            }
          ]
        }
      }]
    }
  }'
```

**Note:** Subscribers are merged into the existing array. Network functions can be added/removed by updating the `networkFunctions` array.

---

### DELETE /intent/{id}

Revoke the intent and delete the FiveGCore CR and all child NetworkFunction CRs.

```bash
curl -X DELETE http://localhost:8080/intent/b4g3d2e5-...
```

**Response (200):**

```json
{
  "@type": "Intent",
  "id": "b4g3d2e5-...",
  "state": "Inactive",
  "message": "Intent revoked, 5G core deleted"
}
```

**Warning:** Deleting a FiveGCore CR will cascade delete all child NetworkFunction CRs. Any CellSite CRs referencing these NetworkFunctions will fail.

---

## Orchestration Pipeline

When implemented, `POST /intent` will drive this pipeline:

1. **Validate** — check TMF921 envelope and FiveGCoreExpectation schema
2. **Validate PLMN** — ensure MCC/MNC are valid 3-digit/2-3-digit codes
3. **Validate Device containers** — check that all containerName values reference existing Device CRs that are Ready
4. **Check for conflicts** — ensure FiveGCore CR with coreId doesn't already exist
5. **Generate intent ID** — assign UUID
6. **Map TMF921 → FiveGCore CR spec** — transform expectationObject to CRD format
7. **Create FiveGCore CR** — apply to Kubernetes via CustomObjectsApi
8. **Store intent mapping** — save intent ID → CR name for state tracking
9. **Return 202 Acknowledged** — intent accepted, orchestration in progress

**State tracking:** The API polls FiveGCore CR `status.phase` and maps to TMF921 state for `GET /intent/{id}` queries.

---

## Dependencies

**FiveGCore depends on:**
- **VyOSInfrastructure** (Ready) — creates Device CRs that host the network functions
- **MongoDB** — reachable at `mongodbUri` for UDM/UDR/PCF/NRF

**Dependency chain:**
```
VyOSInfrastructure → Device containers → FiveGCore → NetworkFunction CRs
```

The FiveGCore operator waits for all referenced Device containers to reach Ready phase before creating NetworkFunction child CRs.

---

## Cross-Domain Integration

**Radio Domain Integration:**

The Radio domain (CellSite) references FiveGCore NetworkFunction names to connect gNodeBs and UEs to the core network.

**Naming convention:**
- FiveGCore name: `{coreId}` (e.g., "free5gc-core")
- NetworkFunction names: `{coreId}-{nf-type}` (e.g., "free5gc-core-amf", "free5gc-core-upf")

**Example Radio intent referencing Core:**
```json
{
  "gnb": {
    "coreName": "free5gc-core"
  }
}
```

The Radio API resolves:
- `amf_name` = "free5gc-core-amf"
- `upf_name` = "free5gc-core-upf"
- `smf_name` = "free5gc-core-smf"

And validates:
- `gnb.mcc/mnc` matches `plmn.mcc/mnc`
- `gnb.tac` matches AMF `config.tac`
- `gnb.slices` match `slices[]`
- `ues[].imsi` are in `subscribers[]`

---

## Example: 5G Core Deployment

**Scenario:** Deploy a 5G core network with one slice, 5 subscribers, and essential network functions.

**Step 1: Submit Intent**

```bash
POST /intent
{
  "@type": "Intent",
  "name": "free5gc-core",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "FiveGCoreExpectation",
      "expectationVerb": "DELIVER",
      "expectationObject": {
        "@type": "FiveGCoreService",
        "coreId": "free5gc-core",
        "mongodbUri": "mongodb://10.102.1.21:27017",
        "plmn": {
          "mcc": "208",
          "mnc": "93"
        },
        "slices": [{
          "sst": 1,
          "sd": "010203",
          "dnnInfos": [{
            "dnn": "internet",
            "dns": {"ipv4": "8.8.8.8"},
            "pools": [{"cidr": "10.60.0.0/16"}]
          }]
        }],
        "subscribers": [
          {"imsi": "208930000000001", "key": "...", "opc": "...", "apn": "internet", "sst": 1, "sd": "010203"},
          {"imsi": "208930000000002", "key": "...", "opc": "...", "apn": "internet", "sst": 1, "sd": "010203"}
        ],
        "networkFunctions": [
          {"type": "NRF", "containerName": "nrf"},
          {"type": "AMF", "containerName": "amf", "config": {"tac": 1}},
          {"type": "SMF", "containerName": "smf"},
          {"type": "UPF", "containerName": "upf"},
          {"type": "UDR", "containerName": "udr"},
          {"type": "UDM", "containerName": "udm"},
          {"type": "AUSF", "containerName": "ausf"},
          {"type": "PCF", "containerName": "pcf"},
          {"type": "WebUI", "containerName": "webui"}
        ]
      }
    }]
  }
}
```

**Response:**
```json
{
  "id": "core-123",
  "state": "Acknowledged"
}
```

**Step 2: Poll Status**

```bash
GET /intent/core-123
```

**Response:**
```json
{
  "id": "core-123",
  "state": "InProgress",
  "crStatus": {
    "phase": "Creating",
    "message": "Creating network function child CRs"
  }
}
```

**Step 3: Core Ready**

```bash
GET /intent/core-123
```

**Response:**
```json
{
  "id": "core-123",
  "state": "Completed",
  "crStatus": {
    "phase": "Ready",
    "message": "All network functions are Ready",
    "networkFunctions": [
      {"type": "AMF", "phase": "Ready", "ipAddress": "10.102.1.10"},
      {"type": "SMF", "phase": "Ready", "ipAddress": "10.102.1.11"},
      {"type": "UPF", "phase": "Ready", "ipAddress": "10.102.1.15"}
    ]
  }
}
```

---

## Error Handling

**400 Bad Request**
- Missing `@type: Intent` in envelope
- Missing `intentExpression`
- Missing `coreId` in expectationObject
- Invalid PLMN (MCC/MNC format incorrect)
- Invalid IMSI (not 15 digits)
- Invalid subscriber key/opc (not 32 hex chars)

**409 Conflict**
- FiveGCore CR with same `coreId` already exists

**424 Failed Dependency**
- Device container specified in `networkFunctions[].containerName` not found
- Device container exists but not Ready

**500 Internal Server Error**
- Kubernetes API connection failure
- Unable to create FiveGCore CR

---

## Implementation Notes

When building the API implementation:

1. **Use Kubernetes CustomObjectsApi** for FiveGCore CR operations
2. **Validate Device containers** before creating CR (check existence and Ready status for all containerName values)
3. **Store intent ID → CR name mapping** in ConfigMap or in-memory (with persistence)
4. **Watch FiveGCore CR status** to update intent state in real-time
5. **Use Strategic Merge Patch** for PATCH /intent operations (especially for subscribers array)
6. **Validate PLMN** format before creating CR
7. **Validate subscriber credentials** format (IMSI, key, opc patterns)
8. **Auto-inject fields**: The FiveGCore operator automatically injects nrfUri, mongodbUri, plmnList, etc. into child NetworkFunction CRs — do not include these in the NetworkFunction config

**Future enhancements:**
- Automatic subscriber generation (bulk provisioning)
- Network slice template library (predefined eMBB/URLLC/mMTC configs)
- Metrics endpoint configuration
- Multi-PLMN support
- Integration with external HSS/UDM
