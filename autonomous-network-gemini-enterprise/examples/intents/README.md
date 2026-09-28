# TMF921 Network Intent Files

This directory contains sample TMF921-compliant network intent files demonstrating **intent-based network management** with proper abstraction levels across the Autonomous Network Lab domains.

## Overview

Intent-based networking allows operators to express **WHAT** connectivity or services they need without specifying **HOW** to implement it. The domain orchestrator APIs act as ACTN MDSC (Multi-Domain Service Coordinator) components, deriving technical implementation details from business-level requirements.

## Abstraction Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│ BUSINESS INTENT (Service Domain)                            │
│ "Provide eMBB coverage for 50K subscribers in UK regions"  │
│ ↓ Orchestrator decomposes into domain intents               │
└─────────────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ TRANSPORT    │  │ CORE         │  │ RADIO        │
│ INTENT       │  │ INTENT       │  │ INTENT       │
│              │  │              │  │              │
│ WHAT:        │  │ WHAT:        │  │ WHAT:        │
│ • Endpoints  │  │ • Capacity   │  │ • Coverage   │
│ • Topology   │  │ • Service    │  │ • Subscribers│
│ • SLA        │  │   type       │  │ • Core link  │
│              │  │ • SLA        │  │              │
│ ↓            │  │ ↓            │  │ ↓            │
│ HOW:         │  │ HOW:         │  │ HOW:         │
│ • VRF names  │  │ • PLMN       │  │ • Validates  │
│ • RD/RT      │  │   allocation │  │   cross-     │
│ • BGP config │  │ • Slice      │  │   domain     │
│ • QoS rules  │  │   config     │  │ • Retrieves  │
│              │  │ • NF sizing  │  │   credentials│
└──────────────┘  └──────────────┘  └──────────────┘
        │                │                │
        └────────────────┼────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ KUBERNETES CUSTOM RESOURCES                                  │
│ VyOSL3VPN, FiveGCore, NetworkFunction, CellSite            │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ NETWORK INFRASTRUCTURE                                       │
│ VyOS routers, 5G NFs, gNodeBs, UEs                         │
└─────────────────────────────────────────────────────────────┘
```

## Key Principles

### 1. **Separation of WHAT and HOW**

**User specifies WHAT (business intent):**
- Transport: "Connect these PE routers to customer sites with 99.99% availability"
- Core: "Provide eMBB service for 50K subscribers with <20ms latency"
- Radio: "Cover Oxford region with these subscribers"

**Orchestrator derives HOW (technical implementation):**
- Transport: VRF names, RD/RT values, BGP configurations, QoS policies
- Core: PLMN allocation, slice configuration, NF deployment plan, IP pools
- Radio: Cross-domain validation, credential retrieval, NF resolution

### 2. **ACTN Architecture Alignment (RFC 8453)**

The orchestrators implement IETF ACTN (Abstraction and Control of TE Networks):

- **CNC (Customer Network Controller)**: Northbound system submitting intents
- **MDSC (Multi-Domain Service Coordinator)**: Domain orchestrator APIs
- **PNC (Physical Network Controller)**: Kubernetes Operator

### 3. **Cross-Domain Validation**

Radio orchestrator enforces consistency with Core domain:
- ✓ PLMN (MCC/MNC) must match
- ✓ TAC must match AMF configuration  
- ✓ Network slices must match
- ✓ Subscriber IMSIs must be registered
- ✓ DNN must exist in slice configuration

This prevents configuration drift and ensures end-to-end consistency.

## Intent Files

| File | Abstraction Level | What You Specify | What Orchestrator Derives |
|------|-------------------|------------------|---------------------------|
| `transport-default.yaml` | Domain | PE endpoints, topology, SLA | VRF name, RD/RT, BGP config, QoS |
| `core-default.yaml` | Domain | Capacity, service type, SLA | PLMN, slices, NF plan, IP pools |
| `radio-default.yaml` | Domain | Coverage zones, subscribers | Cross-domain validation, credentials |
| `service-default.yaml` | Business | Coverage, capacity, SLA | All domain intents, orchestration |

## TMF921 Intent Structure

All intents follow TMF921 Intent Management API specification:

```yaml
"@type": "Intent"
name: "<human-readable-name>"
description: "<business purpose>"
priority: 50
intentExpression:
  "@type": "IntentExpression"
  expectation:
    - "@type": "<ExpectationType>"
      expectationVerb: "DELIVER"
      expectationObject:
        "@type": "<ObjectType>"
        # High-level requirements (WHAT)
        # Orchestrator derives implementation (HOW)
```

### Intent Types by Domain

| Domain | Intent Type | What It Requests |
|--------|-------------|------------------|
| Transport | `L3ConnectivityIntent` | L3VPN service between PE routers and customer sites |
| Core | `MobileServiceIntent` | 5G core network with specified capacity and service level |
| Radio | `CellSiteIntent` | Radio coverage for specific zones and subscribers |
| Service | `NetworkSliceIntent` | End-to-end network slice (orchestrates all domains) |

## Usage Examples

### Option 1: Individual Domain Intents

Apply intents in dependency order (Transport → Core → Radio).

> **Note:** The transport orchestrator uses the A2A JSON-RPC protocol (`POST /a2a/app`).
> Core and radio orchestrators expose a simpler REST `/intent` endpoint.

```bash
# 1. Transport - Backhaul L3VPN (A2A JSON-RPC)
INTENT=$(cat examples/intents/transport-default.yaml)
curl -X POST http://transport-api:8080/a2a/app \
  -H 'Content-Type: application/json' \
  -d "{
    \"jsonrpc\": \"2.0\",
    \"method\": \"message/send\",
    \"id\": 1,
    \"params\": {
      \"message\": {
        \"role\": \"user\",
        \"parts\": [{\"kind\": \"data\", \"data\": $INTENT}]
      }
    }
  }"

# 2. Core - 5G Network Functions
curl -X POST http://core-api:8080/intent \
  -H 'Content-Type: application/x-yaml' \
  --data-binary '@examples/intents/core-default.yaml'
# Response: {"id": "core-001", "state": "Acknowledged"}

# 3. Radio - Cell Sites (submit each separately)
curl -X POST http://radio-api:8080/intent \
  -H 'Content-Type: application/x-yaml' \
  --data-binary '@examples/intents/radio-default.yaml'
# Response: {"id": "radio-001", "state": "Acknowledged"}
```

### Option 2: Service Intent (Auto-Orchestration)

The Service API decomposes the business intent into domain intents:

```bash
curl -X POST http://service-api:8080/intent \
  -H 'Content-Type: application/x-yaml' \
  --data-binary '@examples/intents/service-default.yaml'

# Response: {"id": "slice-001", "state": "Acknowledged"}

# Poll for aggregate state
curl http://service-api:8080/intent/slice-001

# Response shows decomposed domain intents:
{
  "id": "slice-001",
  "state": "InProgress",
  "decomposedIntents": {
    "transport": {"intentId": "transport-001", "state": "Completed"},
    "core": {"intentId": "core-001", "state": "InProgress"},
    "radio": [
      {"intentId": "radio-001", "state": "InProgress"},
      {"intentId": "radio-002", "state": "Acknowledged"}
    ]
  }
}
```

## Dependency Chain

```
VyOSInfrastructure (creates routers and devices)
        ↓
VyOSUnderlay (OSPF, BGP, MPLS)
        ↓
VyOSL3VPN ← Transport Intent
        ↓
FiveGCore → NetworkFunction CRs ← Core Intent
        ↓
CellSite ← Radio Intent

Service Intent orchestrates all of the above
```

## Cross-Domain Validation Example

When you submit a Radio intent that references Core:

```yaml
serviceLink:
  mobileService: "free5gc-core"
subscribers:
  - "208930000000001"
```

The Radio orchestrator:
1. ✓ Looks up FiveGCore CR "free5gc-core"
2. ✓ Retrieves PLMN (208/93), TAC (1), slices (SST=1, SD=010203)
3. ✓ Validates subscriber IMSI is in Core subscriber list
4. ✓ Retrieves K, OPc credentials from Core
5. ✓ Resolves NetworkFunction CRs: free5gc-core-amf, free5gc-core-upf, free5gc-core-smf
6. ✗ Rejects with HTTP 422 if any validation fails

## Intent State Machine

```
POST → Acknowledged → InProgress → Completed
                               └─→ Rejected
DELETE → Inactive
```

| K8s CR Phase | TMF921 Intent State |
|--------------|---------------------|
| Waiting/Pending | InProgress |
| Processing/Creating | InProgress |
| Ready | Completed |
| Error/Failed | Rejected |
| (deleted) | Inactive |

## Generated Resources

### Transport Intent → VyOSL3VPN CR
Orchestrator derives VRF names, RD/RT values, BGP neighbors from endpoints and topology.

### Core Intent → FiveGCore CR + NetworkFunction CRs
Orchestrator allocates PLMN, configures slices, provisions subscribers, deploys 9 NF CRs.

### Radio Intent → CellSite CR
Orchestrator validates cross-domain, retrieves credentials, resolves NF CR names.

## Comparing Abstraction Levels

| Approach | Who Specifies | What They Specify | Example |
|----------|---------------|-------------------|---------|
| **Direct CRs** | Infrastructure engineer | Full CR spec with VRF, RD, RT, BGP config | VyOSL3VPN with 80+ lines |
| **Domain Intent** | Network engineer | Endpoints, topology, SLA | Transport intent with 20 lines |
| **Service Intent** | Business stakeholder | Coverage, capacity, SLA | Service intent with 30 lines |

## Prerequisites

Before applying intents:

1. **VyOSInfrastructure CR** must be Ready (creates routers and device containers)
2. **VyOSUnderlay CR** must be Ready (configures OSPF, BGP, MPLS)
3. **MongoDB** must be accessible (for Core UDM/UDR/PCF/NRF)

Check status:
```bash
kubectl get vyosinfrastructure,vyosunderlay,device -n network
```

## Troubleshooting

**Intent stuck in Acknowledged:**
- Check prerequisites are Ready
- For Core: Verify Device containers exist
- For Radio: Verify FiveGCore and NetworkFunction CRs are Ready

**Intent rejected with 422:**
- Cross-domain validation failure
- Check PLMN, TAC, slices, IMSIs match between Core and Radio
- Verify DNN exists in Core slice configuration

**Intent rejected with 424:**
- Dependency not Ready
- Transport: Check VyOSUnderlay is Ready
- Radio: Check FiveGCore and all NetworkFunction CRs are Ready

**Query intent status:**
```bash
curl http://<api>:8080/intent/{id}
```

**Check underlying CRs:**
```bash
kubectl get vyosl3vpn,fivegcore,cellsite -n network
kubectl describe cellsite <name> -n network
```

## Reference Documentation

- **TMF921 Intent Management API**: https://www.tmforum.org/resources/specification/tmf921-intent-management-api-rest-specification-r21-5-0/
- **IETF ACTN RFC 8453**: https://datatracker.ietf.org/doc/html/rfc8453
- **Domain Orchestrator Documentation**:
  - [Transport Orchestrator](../../domains/transport/Readme.md)
  - [Core Orchestrator](../../domains/core/knowledge/ORCHESTRATOR.md)
  - [Radio Orchestrator](../../domains/radio/knowledge/ORCHESTRATOR.md)
  - [Service Orchestrator](../../domains/service/knowledge/ORCHESTRATOR.md)
- **Domain Specifications**:
  - [Transport Domain](../../domains/transport/Readme.md)
  - [Core Domain](../../domains/core/Readme.md)
  - [Radio Domain](../../domains/radio/Readme.md)
  - [Service Domain](../../domains/service/Readme.md)
