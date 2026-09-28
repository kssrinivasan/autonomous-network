# Radio Domain Orchestrator API

REST service that accepts TMF921 network intents from northbound systems and translates them into CellSite Kubernetes CR operations for gNodeB and UE provisioning.

---

## Architecture

This service implements a **TMF921 Intent Management** interface for radio access network deployment, specifically UERANSIM gNodeB (base station) and UE (user equipment) provisioning.

```
Northbound system  ──TMF921 intent──▶  Radio API  ──CellSite CR──▶  K8s Operator  ──UERANSIM──▶  gNodeB + UEs
```

**Key principle:** Northbound callers express **what** radio coverage they need using abstract cell site and UE definitions. The API translates these to CellSite CRs that the operator provisions as UERANSIM processes connected to the 5G core network.

**Cross-domain dependency:** Radio domain intents reference Core domain NetworkFunction CRs for AMF, UPF, and SMF connectivity.

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

### Expectation Type: CellSiteExpectation

Requests deployment of a gNodeB base station and associated UE devices connected to a 5G core network.

```json
{
  "@type": "CellSiteExpectation",
  "expectationVerb": "DELIVER",
  "expectationObject": {
    "@type": "CellSiteService",
    "siteId": "cellsite-cell1a",
    "containerName": "cell1a",
    "gnb": {
      "mcc": "208",
      "mnc": "93",
      "tac": 1,
      "gnbId": "0x1",
      "nci": "0x000000010",
      "gnbIdLength": 32,
      "coreName": "free5gc-core",
      "amfPort": 38412,
      "slices": [
        {
          "sst": 1,
          "sd": "010203"
        }
      ]
    },
    "ues": [
      {
        "imsi": "208930000000001",
        "key": "465B5CE8B199B49FAA5F0A2EE238A6BC",
        "opc": "E8ED289DEBA952E4283B54E88E6183CA",
        "amf": "8000",
        "apn": "internet",
        "sst": 1,
        "sd": "010203",
        "enabled": true
      }
    ]
  }
}
```

**Field Mapping to CellSite CRD:**

| TMF921 Field | CRD Field | Notes |
|--------------|-----------|-------|
| `siteId` | `metadata.name` | Unique identifier for the cell site |
| `containerName` | `spec.container_name` | Device container hosting gNodeB/UEs (max 8 chars) |
| `gnb.mcc` | `spec.gnb.mcc` | Mobile Country Code (must match Core PLMN) |
| `gnb.mnc` | `spec.gnb.mnc` | Mobile Network Code (must match Core PLMN) |
| `gnb.tac` | `spec.gnb.tac` | Tracking Area Code (must match Core AMF TAC) |
| `gnb.gnbId` | `spec.gnb.gnbId` | gNodeB identifier (hex format, e.g., "0x1") |
| `gnb.nci` | `spec.gnb.nci` | NR Cell Identity (optional, auto-generated from gnbId) |
| `gnb.gnbIdLength` | `spec.gnb.gnbIdLength` | gNB ID length in bits (default 32) |
| `gnb.coreName` | Resolves to `spec.gnb.amf_name`, `upf_name`, `smf_name` | Core NetworkFunction CR name prefix |
| `gnb.amfPort` | `spec.gnb.amf_port` | AMF NGAP port (default 38412) |
| `gnb.slices[]` | `spec.gnb.slices[]` | Network slices (must match Core slices) |
| `ues[]` | `spec.ues[]` | User equipment devices |

**Cross-Domain Dependency Resolution:**

When `gnb.coreName` is specified (e.g., "free5gc-core"), the Radio API automatically resolves:
- `amf_name` = "{coreName}-amf"
- `upf_name` = "{coreName}-upf"
- `smf_name` = "{coreName}-smf"

The API validates these NetworkFunction CRs exist and are Ready before creating the CellSite CR.

---

## REST API Reference

Base URL: `http://<radio-api-host>:8080`

### POST /intent

Submit a new cell site deployment intent.

**Request body:** TMF921 Intent envelope with CellSiteExpectation.

**Responses:**

| Status | Meaning |
|--------|---------|
| 202 | Intent accepted; `id` and `state: Acknowledged` added to the response body |
| 400 | Validation error — missing or invalid fields |
| 409 | Conflict — CellSite CR with this siteId already exists |
| 422 | Unprocessable Entity — Cross-domain validation failure (PLMN/slice/IMSI mismatch) |
| 424 | Failed Dependency — Core NetworkFunction CRs not Ready |
| 500 | Internal error |

**Example:**

```bash
curl -X POST http://localhost:8080/intent \
  -H 'Content-Type: application/json' \
  -d '{
    "@type": "Intent",
    "name": "downtown-cell-site-1a",
    "description": "Downtown sector 1, cell A",
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [
        {
          "@type": "CellSiteExpectation",
          "expectationVerb": "DELIVER",
          "expectationObject": {
            "@type": "CellSiteService",
            "siteId": "cellsite-cell1a",
            "containerName": "cell1a",
            "gnb": {
              "mcc": "208",
              "mnc": "93",
              "tac": 1,
              "gnbId": "0x1",
              "coreName": "free5gc-core",
              "slices": [
                {
                  "sst": 1,
                  "sd": "010203"
                }
              ]
            },
            "ues": [
              {
                "imsi": "208930000000001",
                "key": "465B5CE8B199B49FAA5F0A2EE238A6BC",
                "opc": "E8ED289DEBA952E4283B54E88E6183CA",
                "apn": "internet",
                "sst": 1,
                "sd": "010203"
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
  "id": "c5h4e3f6-7g8h-9i0j-1k2l-3m4n5o6p7q8r",
  "name": "downtown-cell-site-1a",
  "state": "Acknowledged",
  "message": "Intent received and queued for orchestration",
  "intentExpression": { "..." }
}
```

---

### GET /intents

List all active cell site deployment intents.

```bash
curl http://localhost:8080/intents
```

**Response (200):**
```json
{
  "intents": [
    {
      "@type": "Intent",
      "id": "c5h4e3f6-...",
      "name": "downtown-cell-site-1a",
      "state": "Completed",
      "intentExpression": { "..." }
    }
  ]
}
```

---

### GET /intent/{id}

Get the current state of a cell site intent, including aggregated CR status.

```bash
curl http://localhost:8080/intent/c5h4e3f6-...
```

**Response (200):** Full intent record with live `state` derived from underlying CellSite CR phase.

```json
{
  "@type": "Intent",
  "id": "c5h4e3f6-...",
  "name": "downtown-cell-site-1a",
  "state": "Completed",
  "message": "gNodeB connected, all UEs registered",
  "crStatus": {
    "phase": "Ready",
    "gnb": {
      "processId": 12345,
      "ngapIp": "10.102.2.10",
      "gtpIp": "10.102.2.10",
      "amfAddress": "10.102.1.10",
      "upfAddress": "10.102.1.15",
      "connected": true
    },
    "ues": [
      {
        "imsi": "208930000000001",
        "processId": 12346,
        "registered": true,
        "pduSessionActive": true,
        "ipAddress": "10.60.0.1"
      }
    ]
  },
  "intentExpression": { "..." }
}
```

**CR phase to TMF921 state mapping:**

| CellSite CR phase | TMF921 state |
|-------------------|--------------|
| Pending | InProgress |
| Creating | InProgress |
| Ready | Completed |
| Failed | Rejected |
| (deleted) | Inactive |

---

### PATCH /intent/{id}

Update an existing intent (e.g., add/remove UEs, modify slice configuration).

```bash
curl -X PATCH http://localhost:8080/intent/c5h4e3f6-... \
  -H 'Content-Type: application/json' \
  -d '{
    "intentExpression": {
      "@type": "IntentExpression",
      "expectation": [{
        "@type": "CellSiteExpectation",
        "expectationVerb": "DELIVER",
        "expectationObject": {
          "@type": "CellSiteService",
          "ues": [
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

**Note:** UEs are merged into the existing array. The operator will start new nr-ue processes for added UEs.

---

### DELETE /intent/{id}

Revoke the intent and delete the CellSite CR (stops gNodeB and all UEs).

```bash
curl -X DELETE http://localhost:8080/intent/c5h4e3f6-...
```

**Response (200):**

```json
{
  "@type": "Intent",
  "id": "c5h4e3f6-...",
  "state": "Inactive",
  "message": "Intent revoked, cell site deleted"
}
```

---

## Orchestration Pipeline

When implemented, `POST /intent` will drive this pipeline:

1. **Validate** — check TMF921 envelope and CellSiteExpectation schema
2. **Validate Device container** — check that containerName references an existing Device CR that is Ready
3. **Resolve Core dependencies** — resolve `gnb.coreName` to NetworkFunction CR names:
   - `amf_name` = "{coreName}-amf"
   - `upf_name` = "{coreName}-upf"
   - `smf_name` = "{coreName}-smf"
4. **Validate Core NetworkFunctions** — check that AMF, UPF, SMF CRs exist and are Ready
5. **Cross-domain validation** — retrieve FiveGCore CR and validate:
   - `gnb.mcc/mnc` matches `FiveGCore.spec.plmnList[0].mcc/mnc`
   - `gnb.tac` matches `FiveGCore.spec.network_functions[AMF].config.tac`
   - `gnb.slices[].sst/sd` match `FiveGCore.spec.slices[].sst/sd`
   - `ues[].imsi` are in `FiveGCore.spec.subscribers[].imsi`
   - `ues[].apn` matches a `dnn` in `FiveGCore.spec.slices[].dnnInfos[].dnn`
6. **Check for conflicts** — ensure CellSite CR with siteId doesn't already exist
7. **Generate intent ID** — assign UUID
8. **Map TMF921 → CellSite CR spec** — transform expectationObject to CRD format
9. **Create CellSite CR** — apply to Kubernetes via CustomObjectsApi
10. **Store intent mapping** — save intent ID → CR name for state tracking
11. **Return 202 Acknowledged** — intent accepted, orchestration in progress

**State tracking:** The API polls CellSite CR `status.phase` and maps to TMF921 state for `GET /intent/{id}` queries.

---

## Cross-Domain Validation Rules

The Radio API enforces strict consistency with the Core domain:

| Validation Rule | Core Field | Radio Field | Error if Mismatch |
|-----------------|------------|-------------|-------------------|
| PLMN match | `FiveGCore.spec.plmnList[0].mcc/mnc` | `gnb.mcc/mnc` | 422 Unprocessable Entity |
| TAC match | `FiveGCore.spec.network_functions[AMF].config.tac` | `gnb.tac` | 422 Unprocessable Entity |
| Slice match | `FiveGCore.spec.slices[].sst/sd` | `gnb.slices[].sst/sd` | 422 Unprocessable Entity |
| Subscriber registered | `FiveGCore.spec.subscribers[].imsi` | `ues[].imsi` | 422 Unprocessable Entity |
| DNN match | `FiveGCore.spec.slices[].dnnInfos[].dnn` | `ues[].apn` | 422 Unprocessable Entity |
| Core exists | `FiveGCore` CR name = coreName | `gnb.coreName` | 404 Not Found |
| Core Ready | `FiveGCore.status.phase` = Ready | N/A | 424 Failed Dependency |
| NetworkFunction Ready | `NetworkFunction.status.ready` = true | amf_name, upf_name, smf_name | 424 Failed Dependency |

**Example validation failure:**

```bash
POST /intent
{
  "gnb": {
    "mcc": "999",  # Does not match FiveGCore PLMN
    "mnc": "99",
    "coreName": "free5gc-core"
  }
}
```

**Response (422):**
```json
{
  "error": "Unprocessable Entity",
  "message": "PLMN mismatch: gnb.mcc/mnc (999/99) does not match FiveGCore plmnList[0] (208/93)",
  "details": {
    "field": "gnb.mcc",
    "expected": "208",
    "actual": "999"
  }
}
```

---

## Dependencies

**CellSite depends on:**
- **Device container** (Ready) — hosts the gNodeB and UE processes
- **FiveGCore** (Ready) — provides 5G core network functions
- **NetworkFunction CRs** (Ready) — AMF, UPF, SMF must be Ready before gNodeB can connect

**Dependency chain:**
```
VyOSInfrastructure → Device containers
                 ↓
              FiveGCore → NetworkFunction CRs (AMF, UPF, SMF)
                                           ↓
                                      CellSite → gNodeB + UEs
```

The CellSite operator waits for all dependencies to reach Ready phase before starting UERANSIM processes.

---

## Example: Multi-Domain Deployment

**Scenario:** Deploy Core, then deploy multiple CellSites referencing the Core.

### Step 1: Deploy Core (Core API)

```bash
POST http://core-api:8080/intent
{
  "@type": "Intent",
  "name": "free5gc-core",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "FiveGCoreExpectation",
      "expectationVerb": "DELIVER",
      "expectationObject": {
        "coreId": "free5gc-core",
        "plmn": {"mcc": "208", "mnc": "93"},
        "slices": [{
          "sst": 1,
          "sd": "010203",
          "dnnInfos": [{"dnn": "internet", "pools": [{"cidr": "10.60.0.0/16"}]}]
        }],
        "subscribers": [
          {"imsi": "208930000000001", "key": "...", "opc": "...", "apn": "internet", "sst": 1, "sd": "010203"},
          {"imsi": "208930000000002", "key": "...", "opc": "...", "apn": "internet", "sst": 1, "sd": "010203"}
        ],
        "networkFunctions": [
          {"type": "AMF", "containerName": "amf", "config": {"tac": 1}},
          {"type": "SMF", "containerName": "smf"},
          {"type": "UPF", "containerName": "upf"}
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

### Step 2: Wait for Core Ready

```bash
GET http://core-api:8080/intent/core-123
```

**Response:**
```json
{
  "id": "core-123",
  "state": "Completed",
  "crStatus": {
    "phase": "Ready",
    "networkFunctions": [
      {"type": "AMF", "phase": "Ready", "ipAddress": "10.102.1.10"},
      {"type": "UPF", "phase": "Ready", "ipAddress": "10.102.1.15"}
    ]
  }
}
```

### Step 3: Deploy Cell Site (Radio API)

```bash
POST http://radio-api:8080/intent
{
  "@type": "Intent",
  "name": "cellsite-1a",
  "intentExpression": {
    "@type": "IntentExpression",
    "expectation": [{
      "@type": "CellSiteExpectation",
      "expectationVerb": "DELIVER",
      "expectationObject": {
        "siteId": "cellsite-cell1a",
        "containerName": "cell1a",
        "gnb": {
          "mcc": "208",
          "mnc": "93",
          "tac": 1,
          "gnbId": "0x1",
          "coreName": "free5gc-core",
          "slices": [{"sst": 1, "sd": "010203"}]
        },
        "ues": [
          {"imsi": "208930000000001", "key": "...", "opc": "...", "apn": "internet", "sst": 1, "sd": "010203"}
        ]
      }
    }]
  }
}
```

**Response:**
```json
{
  "id": "radio-456",
  "state": "Acknowledged"
}
```

### Step 4: Cell Site Ready

```bash
GET http://radio-api:8080/intent/radio-456
```

**Response:**
```json
{
  "id": "radio-456",
  "state": "Completed",
  "crStatus": {
    "phase": "Ready",
    "gnb": {
      "connected": true,
      "amfAddress": "10.102.1.10"
    },
    "ues": [
      {
        "imsi": "208930000000001",
        "registered": true,
        "pduSessionActive": true,
        "ipAddress": "10.60.0.1"
      }
    ]
  }
}
```

---

## Error Handling

**400 Bad Request**
- Missing `@type: Intent` in envelope
- Missing `intentExpression`
- Missing `siteId` in expectationObject
- Invalid IMSI (not 15 digits)
- Invalid key/opc (not 32 hex chars)
- Invalid gnbId (not hex format)

**404 Not Found**
- Core FiveGCore CR specified in `gnb.coreName` not found
- Device container specified in `containerName` not found

**409 Conflict**
- CellSite CR with same `siteId` already exists

**422 Unprocessable Entity**
- PLMN mismatch: `gnb.mcc/mnc` does not match Core `plmnList[0]`
- TAC mismatch: `gnb.tac` does not match Core AMF `config.tac`
- Slice mismatch: `gnb.slices` do not match Core `slices`
- IMSI not registered: `ues[].imsi` not in Core `subscribers[]`
- DNN mismatch: `ues[].apn` not in Core slice `dnnInfos[].dnn`

**424 Failed Dependency**
- FiveGCore CR exists but not Ready
- NetworkFunction CRs (AMF/UPF/SMF) exist but not Ready
- Device container exists but not Ready

**500 Internal Server Error**
- Kubernetes API connection failure
- Unable to create CellSite CR

---

## Implementation Notes

When building the API implementation:

1. **Use Kubernetes CustomObjectsApi** for CellSite and FiveGCore CR operations
2. **Resolve Core dependencies** — translate `gnb.coreName` to NetworkFunction CR names
3. **Validate cross-domain consistency** — retrieve FiveGCore CR and check PLMN, TAC, slices, subscribers
4. **Store intent ID → CR name mapping** in ConfigMap or in-memory (with persistence)
5. **Watch CellSite CR status** to update intent state in real-time
6. **Use Strategic Merge Patch** for PATCH /intent operations (especially for ues array)
7. **Error handling**: Provide detailed error messages for cross-domain validation failures (include expected vs actual values)

**Future enhancements:**
- Automatic UE credential generation (bulk provisioning)
- Coverage area mapping (lat/lon to gNodeB parameters)
- Load-based UE distribution across multiple gNodeBs
- Network slice selection optimization
- Integration with RF planning tools
