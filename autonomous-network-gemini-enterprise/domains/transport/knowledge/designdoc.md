# Transport Domain Design & Decomposition Guide

**Version:** 2.0 (Combined Orchestrator + Design Doc)  
**Date:** 2026-08-15  
**Audience:** Transport orchestrator agent, network architects, agent developers

---

## Document Purpose and Scope

This document is the **primary reference** for the transport orchestrator agent to decompose high-level L3ConnectivityIntent into concrete VyOSL3VPN Kubernetes Custom Resources. It combines:

- **Design principles (why):** VyOS L3VPN network architecture, router roles, protocol requirements, and validation rules
- **Algorithmic decomposition (how):** 9-phase transformation from TMF921/ACTN-aligned intent to auto-generated VRF configurations with RD/RT allocation

**Assumptions:**
- VyOSInfrastructure CR already exists and is Ready (physical topology provisioned)
- VyOSUnderlay CR already exists and is Ready (OSPF, MPLS/LDP, iBGP configured)
- PE router infrastructure data is available (loopback IPs, BGP AS numbers)
- CE routers are provisioned

**Scope:** This document covers all three layers of L3VPN network design:
1. **Physical/Infrastructure Layer** (VyOSInfrastructure CRD) — routers, links, devices
2. **Underlay Layer** (VyOSUnderlay CRD) — OSPF, MPLS/LDP, iBGP
3. **Overlay/L3VPN Layer** (VyOSL3VPN CRD) — VRFs, RD/RT, eBGP to CE

---

## Table of Contents

### Part 1: Orchestrator Overview
1. [Document Purpose and Scope](#document-purpose-and-scope)
2. [Orchestrator Task Flow](#orchestrator-task-flow)
3. [Input Schema: L3ConnectivityIntent](#input-schema-l3connectivityintent)

### Part 2: Network Architecture Foundation
4. [L3VPN Layered Architecture](#l3vpn-layered-architecture)
5. [Router Roles and Responsibilities](#router-roles-and-responsibilities)
6. [VPN Topology Patterns](#vpn-topology-patterns)
7. [Hierarchical vs Flat Designs](#hierarchical-vs-flat-designs)
8. [IP Addressing Strategy](#ip-addressing-strategy)
9. [VRF and Route Target Design](#vrf-and-route-target-design)

### Part 3: Algorithmic Decomposition
10. [Decomposition Algorithm: 9 Phases](#decomposition-algorithm-9-phases)
11. [Resource Pool Management](#resource-pool-management)
12. [Complete Example Transformation](#complete-example-transformation)

### Part 4: Validation and Best Practices
13. [Design Rules and Validation](#design-rules-and-validation)
14. [Routing Configuration Best Practices](#routing-configuration-best-practices)
15. [Dependencies and Prerequisites](#dependencies-and-prerequisites)
16. [Error Handling Taxonomy](#error-handling-taxonomy)

### Part 5: Reference Examples
17. [Example: Hub-and-Spoke L3VPN](#example-hub-and-spoke-l3vpn)

---

## Orchestrator Task Flow

The orchestrator agent follows this 5-step cycle to transform network intent into domain controller CRs:

```
┌──────────────────────────────────────────────────────────────────┐
│                     Orchestration Agent                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. Network Intent (L3ConnectivityIntent)                        │
│           ↓                                                       │
│  2. Validate Intent ────────────────────────────────────────────┐│
│           │                                                      ││
│           │  Uses:                                               ││
│           │  • Domain Topology (database) ──────────────────────┘│
│           │  • Design Doc (this document, Section 3, 9, 13)      │
│           │  • Domain CRDs (Section 17)                          │
│           ↓                                                       │
│  3. Break into network changes ─────────────────────────────────┐│
│           │                                                      ││
│           │  Uses:                                               ││
│           │  • Decomposition Algorithm (Section 10)             ││
│           │  • Resource Pools (Section 11)                      ││
│           │  • VRF/RT Design Rules (Section 9)                  ││
│           ↓                                                      ││
│  4. CRUD existing/new CRs to implement each step                ││
│           │                                                      ││
│           │  Generates:                                          ││
│           │  • VyOSL3VPN CR (stacked outputs)                   ││
│           ↓                                                      ││
│  5. Domain Controller CRs ──────────────────────────────────────┘│
│           │                                                       │
│           │  VyOSL3VPN CR                                        │
│           │  VyOSL3VPN CR                                        │
│           │  VyOSL3VPN CR                                        │
│           ↓                                                       │
└──────────────────────────────────────────────────────────────────┘
```

**Workflow Mapping:**

| Flow Step | Document Sections | Agent Action |
|-----------|-------------------|--------------|
| **Validate Intent** | Section 3 (Input Schema)<br>Section 13 (Validation Rules)<br>Section 15 (Dependencies) | Parse L3ConnectivityIntent JSON<br>Run schema validation<br>Check PE/CE routers exist in topology database<br>Verify VyOSUnderlay is Ready |
| **Break into network changes** | Section 10 (Algorithm) Phases 1-4<br>Section 11 (Resource Pools)<br>Section 9 (VRF/RT Design) | Sanitize service name<br>Allocate RD per PE (loopback-based)<br>Allocate RT based on topology<br>Determine RT import/export patterns |
| **CRUD CRs** | Section 10 Phases 5-9<br>Section 12 (Example)<br>Section 14 (Best Practices) | Generate VRF configs per PE<br>Generate BGP neighbor configs<br>Generate CE router eBGP configs<br>Assemble VyOSL3VPN CR YAML |
| **Output CRs** | Section 12 (Example)<br>Section 16 (Error Handling) | Return VyOSL3VPN CR YAML<br>OR return error with HTTP status code |

---

## Input Schema: L3ConnectivityIntent

The orchestrator receives **L3ConnectivityIntent** as input, conforming to TMF921 (IETF ACTN) schema.

### Required Fields

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `serviceName` | string | Service identifier | Alphanumeric, unique |
| `topology` | enum | `any-to-any`, `hub-spoke`, `point-to-point` | See [Section 6](#vpn-topology-patterns) |
| `endpoints[]` | array | PE-CE connection points | Min 2 endpoints |
| `endpoints[].peRouter` | string | PE router name | Must exist in infrastructure |
| `endpoints[].peInterface` | string | PE interface name | e.g., `eth2`, `eth3` |
| `endpoints[].customerRouter` | string | CE router name | Must exist |
| `endpoints[].customerPrefix` | string | Customer CIDR | Valid IPv4 CIDR (e.g., `10.100.0.0/24`) |
| `sla.bandwidth` | string | Bandwidth requirement | Parseable (e.g., `"100 Mbps"`) |
| `sla.latency` | string | Maximum latency | e.g., `"< 10ms"` |
| `sla.availability` | string | Availability SLA | e.g., `"99.99%"` |
| `sla.trafficClass` | enum | `business-critical`, `standard`, `best-effort` | Maps to QoS policy |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `endpoints[].customerVlan` | null | Customer VLAN ID |
| `endpoints[].role` | auto-detect | `hub` or `spoke` (for hub-spoke topology) |
| `underlayRef` | From config | VyOSUnderlay CR reference |

### Endpoint Count Constraints

| Topology | Min Endpoints | Max Endpoints | Notes |
|----------|---------------|---------------|-------|
| `any-to-any` | 2 | Unlimited | All sites can communicate |
| `hub-spoke` | 2 | Unlimited | First endpoint or `role=hub` is hub |
| `point-to-point` | 2 | 2 | Exactly two sites |

### Example Intent (Any-to-Any)

```json
{
  "serviceName": "Acme Corp WAN",
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
    }
  ],
  "sla": {
    "bandwidth": "100 Mbps per endpoint",
    "latency": "< 10ms",
    "availability": "99.99%",
    "trafficClass": "business-critical"
  }
}
```

### Example Intent (Hub-Spoke)

```json
{
  "serviceName": "UK Branches",
  "topology": "hub-spoke",
  "endpoints": [
    {
      "peRouter": "pe2",
      "peInterface": "eth3",
      "customerRouter": "ce1-hub",
      "customerPrefix": "10.100.2.0/24",
      "role": "hub"
    },
    {
      "peRouter": "pe1",
      "peInterface": "eth2",
      "customerRouter": "ce1-spoke",
      "customerPrefix": "10.100.1.0/24",
      "role": "spoke"
    },
    {
      "peRouter": "pe3",
      "peInterface": "eth2",
      "customerRouter": "ce2-spoke",
      "customerPrefix": "10.100.3.0/24",
      "role": "spoke"
    }
  ],
  "sla": {
    "bandwidth": "50 Mbps",
    "latency": "< 20ms",
    "availability": "99.9%",
    "trafficClass": "standard"
  }
}
```

---

## L3VPN Layered Architecture

The network is built in three clearly separated layers. Each layer has its own CRD and lifecycle.

```
┌──────────────────────────────────────────────┐
│  Customer / Overlay Layer (VRFs, BGP VPNv4)  │  ← VyOSL3VPN CRD
│  Route Distinguishers, Route Targets, VRFs   │     (Section 10: Decomposition)
├──────────────────────────────────────────────┤
│  Underlay / Core Layer (OSPF + MPLS + iBGP)  │  ← VyOSUnderlay CRD
│  P routers, RR routers, LSPs via LDP         │     (Assumed Ready)
├──────────────────────────────────────────────┤
│  Physical / Infrastructure Layer             │  ← VyOSInfrastructure CRD
│  Routers, Links, Devices, IP addresses       │     (Assumed Ready)
└──────────────────────────────────────────────┘
```

### Layer 1 – Physical Infrastructure

**Defined by:** VyOSInfrastructure CRD

**Key Concepts:**
- Every router gets a unique **loopback IP** (e.g., `10.0.0.x/32`) used as its stable router ID
- Point-to-point (P2P) links use `/24` subnets in the `172.16.x.0/24` range
- Management network (`192.168.122.0/24`) connects `eth0` of every router for out-of-band access
- Each P2P link is assigned a unique **VLAN ID** (301+) to isolate traffic
- **Devices** are simulated end-hosts (traffic agents) attached to customer LANs
- QoS and security policies are bound to router interfaces

### Layer 2 – Underlay Routing

**Defined by:** VyOSUnderlay CRD

**Key Concepts:**
- **OSPF** Area `0.0.0.0` (backbone) for link-state flooding and loopback reachability
- **LDP/MPLS** for label-switched paths (LSPs) between PEs
- **iBGP** with Route Reflectors for distributing VPNv4 routes between PEs
- All P, PE, and RR routers participate in OSPF and MPLS
- CE routers do NOT participate in underlay protocols

**Protocol Coverage:**
- OSPF: All P-to-P, P-to-PE, P-to-RR, PE-to-AR links (NOT PE-to-CE)
- LDP: Same interfaces as OSPF (NOT PE-to-CE)
- iBGP: PE routers peer with ALL RRs; RRs peer with each other

### Layer 3 – L3VPN Overlay

**Defined by:** VyOSL3VPN CRD (generated by orchestrator via Section 10)

**Key Concepts:**
- Each customer site is represented by a **VRF** on its PE router
- **Route Distinguishers (RD)** make customer routes globally unique across PEs
- **Route Targets (RT)** control which VRFs import which routes (hub-spoke or any-to-any policy)
- **eBGP** sessions between PE VRF interfaces and CE routers exchange customer prefixes

**This document focuses on Layer 3 decomposition**, assuming Layers 1 and 2 are already provisioned.

---

## Router Roles and Responsibilities

### P – Provider Core Router

**Role:** MPLS label forwarding only. Carries customer traffic inside LSPs without inspecting IP payload.

**Protocols Required:**
- ✅ OSPF (backbone area, all P-to-P interfaces)
- ✅ MPLS/LDP (all P-to-P interfaces)

**Protocols NOT Required:**
- ❌ BGP (P routers do not run BGP)
- ❌ VRFs

**Interface Pattern:**
- `eth0` → management network
- `eth1`, `eth2`, … → P-to-P links to other P/PE/RR routers
- `lo` → loopback (router ID)

**When to Add:** Add P routers to increase core capacity or redundancy. Minimum: 1 P router. Recommended: 2+ for resilience.

**Critical Rule:** All P routers **MUST** have OSPF and MPLS/LDP configuration in VyOSUnderlay. Missing P router config breaks the entire MPLS backbone.

---

### PE – Provider Edge Router

**Role:** L3VPN service delivery. Maintains VRFs for customer traffic, runs VPNv4 MP-BGP with RRs, and eBGP with CE routers.

**Protocols Required:**
- ✅ OSPF (backbone area, P-facing interfaces only)
- ✅ MPLS/LDP (P-facing interfaces only — **NOT** CE-facing)
- ✅ iBGP (peers with Route Reflectors using loopback addresses)
- ✅ eBGP per VRF (peers with CE router)

**Protocols NOT Required:**
- ❌ LDP on CE-facing interfaces

**Interface Pattern:**
- `eth0` → management network
- `eth1` (or `eth1`/`eth2`) → P-to-PE link(s) into the core
- `eth2` (or next available) → PE-to-CE link (customer-facing, inside a VRF)
- `lo` → loopback (router ID)

**Rule:** A PE must connect to **at least one** P router. For redundancy, connect to two different P routers on different paths.

**BGP Peering Rule:** Every PE **MUST** peer with **ALL** Route Reflectors for redundancy (see [Section 14](#routing-configuration-best-practices)).

---

### RR – Route Reflector

**Role:** iBGP route reflection. Receives VPNv4 routes from PE clients and re-advertises them to all other PE clients, eliminating the need for full iBGP mesh.

**Protocols Required:**
- ✅ OSPF (backbone area, links to P routers)
- ✅ MPLS/LDP (links to P routers — needed for loopback reachability)
- ✅ iBGP with `route_reflector: true`, listing all PE routers as `route_reflector_client: true`
- ✅ **iBGP peering with all other RRs** (without route_reflector_client flag) — **RFC 4456 requirement**

**Protocols NOT Required:**
- ❌ VRFs
- ❌ eBGP

**Interface Pattern:**
- `eth0` → management network
- `eth1`, `eth2` → links to P routers (for OSPF/LDP reachability)
- `lo` → loopback (router ID)

**Sizing Rule:** Deploy at least **two RRs** for redundancy. **All RRs must peer with each other** (full iBGP mesh between RRs).

---

### CE – Customer Edge Router

**Role:** Customer premises router. Connects customer LAN to provider PE over eBGP. Aware only of customer's own AS.

**Protocols Required:**
- ✅ eBGP (peers with PE's VRF interface IP)

**Protocols NOT Required:**
- ❌ OSPF
- ❌ MPLS
- ❌ VRFs

**Interface Pattern:**
- `eth0` → management network
- `eth1` → CE-to-PE link (provider-facing, eBGP peer)
- `eth2` → LAN / customer site network
- `lo` → loopback

**Rule:** CE router's BGP AS must be **different** from provider iBGP AS. Common pattern: `65001` (provider) vs `65035` (customer).

---

### PA – Pre-Aggregation Router

**Role:** Regional aggregation and Area Border Router (ABR) in multi-area OSPF designs. Connects core backbone to regional access layers.

**Protocols Required:**
- ✅ OSPF (participates in Area 0 backbone AND one or more regional areas as ABR)
- ✅ MPLS/LDP (on all core-facing and AR-facing interfaces)
- ✅ ABR configuration (multi-area OSPF with area ranges for route summarization)

**Protocols NOT Required:**
- ❌ BGP
- ❌ VRFs
- ❌ eBGP

**Interface Pattern:**
- `eth0` → management network
- `eth1`, `eth2` → links to P routers in core (Area 0)
- `eth3`, `eth4`, ... → links to AR routers in regional area (Area N)
- `lo` → loopback

**When to Use:** Deploy PA routers when building a hierarchical network with multiple geographic or administrative regions (see [Section 7](#hierarchical-vs-flat-designs)).

---

### AR – Access Router

**Role:** Regional access aggregation. Connects PE routers to the pre-aggregation layer in a hierarchical design. Operates entirely within a regional OSPF area (not Area 0).

**Protocols Required:**
- ✅ OSPF (participates in regional area N only, not Area 0)
- ✅ MPLS/LDP (on all PA-facing and PE-facing interfaces)

**Protocols NOT Required:**
- ❌ BGP
- ❌ VRFs
- ❌ eBGP
- ❌ Multi-area OSPF (ARs are internal to one regional area)

**Interface Pattern:**
- `eth0` → management network
- `eth1`, `eth2` → links to PA routers (dual-homed for redundancy)
- `eth3`, `eth4`, ... → links to PE routers in the same regional area
- `lo` → loopback

**Rule:** AR routers must connect to at least **two** PA routers for redundancy.

---

## VPN Topology Patterns

The orchestrator supports three VPN topologies. Route Target (RT) import/export logic differs per topology.

### Hub-and-Spoke

**Use Case:** Central services, internet breakout, security inspection. All traffic passes through a central hub.

**Traffic Policy:** Hub can communicate with all spokes; spokes communicate **only** through hub — they cannot reach each other directly.

**RT Configuration:**

| Role | RT Export | RT Import | Rationale |
|------|-----------|-----------|-----------|
| Hub PE | `{AS}:{hub_rt}` | `{AS}:{spoke_rt}`, `{AS}:{hub_rt}` | Hub imports spoke routes + self-imports hub RT to enable spoke-to-spoke hairpin |
| Spoke PE | `{AS}:{spoke_rt}` | `{AS}:{hub_rt}` | Spoke imports only hub routes |

**Example:**
```yaml
# Hub VRF
rt_export: ["65001:2000"]  # hub_rt
rt_import: ["65001:2001", "65001:2000"]  # spoke_rt + hub_rt (self-import)

# Spoke VRF
rt_export: ["65001:2001"]  # spoke_rt
rt_import: ["65001:2000"]  # hub_rt only
```

**Note:** Hub self-imports its own export RT (`65001:2000`) to allow spoke-to-spoke traffic to hairpin through the hub VRF.

---

### Any-to-Any (Full Mesh)

**Use Case:** Latency-sensitive peer-to-peer applications (distributed databases, real-time collaboration).

**Traffic Policy:** All sites can communicate with all other sites directly.

**RT Configuration:**

| Role | RT Export | RT Import |
|------|-----------|-----------|
| All PEs | `{AS}:{shared_rt}` | `{AS}:{shared_rt}` |

**Example:**
```yaml
# All VRFs use the same RT
rt_export: ["65001:2000"]
rt_import: ["65001:2000"]
```

---

### Point-to-Point

**Use Case:** Isolated connection between two sites only.

**Traffic Policy:** Endpoint A can communicate only with endpoint B, and vice versa.

**RT Configuration:**

| Endpoint | RT Export | RT Import |
|----------|-----------|-----------|
| Endpoint A | `{AS}:{rt_a}` | `{AS}:{rt_b}` |
| Endpoint B | `{AS}:{rt_b}` | `{AS}:{rt_a}` |

**Example:**
```yaml
# Endpoint A VRF
rt_export: ["65001:2000"]  # rt_a
rt_import: ["65001:2001"]  # rt_b

# Endpoint B VRF
rt_export: ["65001:2001"]  # rt_b
rt_import: ["65001:2000"]  # rt_a
```

---

### Topology Selection Matrix

| Requirement | Recommended Topology |
|-------------|---------------------|
| Internet/security hub | Hub-and-Spoke |
| Centralized application server | Hub-and-Spoke |
| Branch-to-branch collaboration | Any-to-Any |
| Isolated site pair | Point-to-Point |
| Mixed: hub services + branch direct | Hub-and-Spoke with selected spoke import overrides |

---

## Hierarchical vs Flat Designs

### Flat L3VPN Design (Area 0 Only)

**Architecture:**
```
P ← (Area 0) → RR
↓              ↑
PE ← (Area 0) ← iBGP
↓
CE (no OSPF)
```

**Characteristics:**
- All P, PE, RR routers in OSPF Area 0 (backbone)
- Simple, minimal configuration
- Best for small-to-medium deployments (<20 routers)
- Full mesh or near-full-mesh topology
- No route summarization

**When to Use:**
- Small networks (2-4 P routers, 2-12 PE routers)
- Single geographic location
- Flat organizational structure

---

### 4-Tier Hierarchical Design (Multi-Area OSPF)

**Architecture:**
```
Tier 1: P + RR (Area 0)
        ↓
Tier 2: PA (ABR - Area 0 + Area N)
        ↓
Tier 3: AR (Area N)
        ↓
Tier 4: PE (Area N) → CE (no OSPF)
```

**Characteristics:**
- Core (P, RR) and pre-aggregation (PA) in Area 0
- Regional areas (1-N) for AR and PE routers
- PA routers act as ABRs (Area Border Routers)
- Route summarization at area boundaries
- Scalable to 100+ routers
- Dual-homing at each tier for redundancy

**When to Use:**
- Large networks (>20 backbone routers)
- Multiple geographic regions
- Need for OSPF route summarization
- Hierarchical administrative domains

---

### Design Comparison

| Aspect | Flat (Area 0) | Hierarchical (Multi-Area) |
|--------|---------------|---------------------------|
| Router count | <20 | 20-100+ |
| OSPF areas | 1 (Area 0) | 2-5 (Area 0 + regional) |
| Router tiers | 2 (P, PE) | 4 (P, PA, AR, PE) |
| ABRs | None | PA routers |
| Route summarization | No | Yes (at ABRs) |
| Geographic span | Single region | Multi-region |
| OSPF convergence | Fast (small SPF) | Bounded per area |
| Configuration complexity | Low | Medium-High |
| Scalability | Limited | High |

---

## IP Addressing Strategy

Use a disciplined allocation plan for consistency.

| Purpose | Range | Example |
|---------|-------|---------|
| Loopbacks (Router IDs) | `10.0.0.0/24` | `10.0.0.1` – `10.0.0.254` |
| P-to-P core links | `172.16.x.0/24` | `172.16.30.0/24` – `172.16.140.0/24` |
| PE-to-CE links | `10.50–90.x.0/24` | `10.50.50.0/24` (PE1↔CE1) |
| Customer LANs | `10.100.x.0/24` | `10.100.1.0/24` (spoke1 LAN) |
| Management | `192.168.122.0/24` | `192.168.122.11` – `.254` |

### Loopback Allocation Rules

- RR routers: start at `10.0.0.1` (e.g., rr1=`.1`, rr2=`.2`)
- P routers: start at `10.0.0.3` (e.g., p1=`.3`, p2=`.4`)
- PE routers: start at `10.0.0.7` (e.g., pe1=`.7`, pe2=`.8`)
- CE routers: start at `10.0.0.80` (e.g., ce1-spoke=`.80`, ce1-hub=`.100`)

### VLAN Allocation Rules

- P2P core links: start at VLAN 301
- PE-to-CE links: start at VLAN 401
- Management network: no VLAN tag
- Loopback network: no VLAN tag

### BGP AS Numbers

- Provider iBGP: `65001`
- Customer eBGP (all CEs): `65035` (or allocated from pool 65100-65199)

---

## VRF and Route Target Design

### Route Distinguisher (RD)

**Updated Format (v2.0):** Every VRF on every PE must have a **globally unique** RD using loopback-based allocation:

```
{pe-loopback-ip}:{vrf-seq}
```

**Why Loopback-Based:**
- Globally unique per VRF across the network
- Stable across VRF reconfigurations (loopback IP doesn't change)
- Supports automated allocation from per-PE sequence pool

**Example:** PE1 (`10.0.0.7`) hosting first VRF with allocated sequence `200`:
```yaml
rd: "10.0.0.7:200"
```

**VRF Sequence Pool:**
- Range: 200-65535
- Per-PE router allocation
- Managed by orchestrator (see [Section 11](#resource-pool-management))

**BREAKING CHANGE from v1.0:** Previous versions used PE-to-CE interface IP (e.g., `10.50.50.1:1011`). This has been replaced with loopback-based RD for automated allocation.

---

### Route Target (RT)

Route Targets implement traffic policy between sites using the convention:

```
{provider-as}:{rt-seq}
```

**RT Allocation Logic (per topology):**

| Topology | RT Count | Allocation Strategy |
|----------|----------|---------------------|
| Any-to-any | 1 | Single shared RT for all PEs |
| Hub-spoke | 2 | Hub RT + Spoke RT |
| Point-to-point | 2 | RT_A + RT_B (unique pair) |

**Hub VRF RT Configuration:**
```yaml
rt_export: ["65001:2000"]  # hub RT
rt_import: ["65001:2001", "65001:2000"]  # spoke RT + self-import
```

**Spoke VRF RT Configuration:**
```yaml
rt_export: ["65001:2001"]  # spoke RT
rt_import: ["65001:2000"]  # hub RT only
```

**RT Sequence Pool:**
- Range: 2000-65535
- Global allocation across all VPNs
- Managed by orchestrator (see [Section 11](#resource-pool-management))

---

### VRF Table IDs

**Updated Allocation (v2.0):** Each VRF requires a unique Linux routing table ID using dynamic allocation:

- Default starting value: `200`
- Increment per VRF on the same PE
- Example: PE1 first VRF = `200`, second VRF = `201`

**BREAKING CHANGE from v1.0:** Previous versions used fixed patterns (spoke VRFs: `200`, hub VRFs: `400`). This has been replaced with dynamic allocation from per-PE sequence pool.

---

## Decomposition Algorithm: 9 Phases

The orchestrator transforms L3ConnectivityIntent → VyOSL3VPN CR through nine sequential phases.

### Phase 1: Validation

**Purpose:** Validate input schema and check endpoint constraints.

**Algorithm:**
```
1.1 Validate L3ConnectivityIntent conforms to JSON schema (Section 3)
1.2 Sanitize serviceName:
    - Remove spaces, special characters
    - Convert to lowercase
    - Truncate to 32 characters
1.3 Validate topology ∈ {any-to-any, hub-spoke, point-to-point}
1.4 Validate endpoint count per topology:
    - any-to-any: ≥ 2 endpoints
    - hub-spoke: ≥ 2 endpoints
    - point-to-point: exactly 2 endpoints
1.5 FOR each endpoint:
    - Validate peRouter exists in VyOSInfrastructure
    - Validate customerRouter exists
    - Validate customerPrefix is valid IPv4 CIDR
```

**Error Conditions:**
- Invalid service name → 400 Validation Error (see [Section 16](#error-handling-taxonomy))
- Invalid topology → 400 Validation Error
- Insufficient endpoints → 400 Validation Error
- PE router not found → 400 Validation Error

---

### Phase 2: VRF Name Generation

**Purpose:** Generate unique VRF name with collision resolution.

**Algorithm:**
```
2.1 vrf_name = SANITIZED(serviceName)  // From Phase 1
2.2 existing_vpns = QUERY_K8S(kind="VyOSL3VPN", namespace="network")
2.3 WHILE vrf_name IN [vpn.spec.services[0].name for vpn in existing_vpns]:
      vrf_name = vrf_name + "-" + RANDOM_SUFFIX(4)  // e.g., "acme-wan-x7k2"
2.4 RETURN vrf_name
```

**Example:**
- Input: `"Acme Corp WAN"` → Sanitized: `"acme-corp-wan"`
- If `"acme-corp-wan"` exists → Add suffix: `"acme-corp-wan-a3f9"`

---

### Phase 3: RD (Route Distinguisher) Allocation

**Purpose:** Allocate globally unique RD per PE router using loopback-based format.

**Algorithm:**
```
3.1 FOR each unique PE router in endpoints:
      3.1.1 pe_router = QUERY_INFRASTRUCTURE(name=pe_router_name)
      3.1.2 loopback_ip = pe_router.spec.router_id
      3.1.3 vrf_seq = GET_NEXT_SEQUENCE("vrf_seq", scope=pe_router_name)
      3.1.4 rd = f"{loopback_ip}:{vrf_seq}"
      3.1.5 STORE rd_allocation[pe_router_name] = rd
3.2 RETURN rd_allocation
```

**Design Rationale:**
- **Why loopback IP?** Loopback IPs are stable (never change even if physical interfaces are reconfigured). Using PE-to-CE interface IPs (v1.0 approach) creates fragility if PE-CE links are renumbered.
- **Why per-PE sequence pool?** Ensures uniqueness when a single PE hosts multiple VRFs for different customers.

**Example:**
```python
# PE1 with loopback 10.0.0.7, first VRF
rd = "10.0.0.7:200"

# PE1, second VRF
rd = "10.0.0.7:201"

# PE2 with loopback 10.0.0.8, first VRF
rd = "10.0.0.8:200"  # Same sequence, different loopback → globally unique
```

**VRF Sequence Pool:**
- Range: 200-65535 per PE
- Persistent counter (see [Section 11](#resource-pool-management))

---

### Phase 4: RT (Route Target) Allocation

**Purpose:** Allocate RT values based on topology type.

**Algorithm:**
```
4.1 provider_asn = GET_CONFIG("provider_asn")  // e.g., 65001
4.2 rt_seq = GET_NEXT_SEQUENCE("rt_seq", scope="global")

4.3 IF topology == "any-to-any":
      rt = f"{provider_asn}:{rt_seq}"
      INCREMENT_SEQUENCE("rt_seq", by=1)
      RETURN {
        "rt_export": [rt],
        "rt_import": [rt]
      }

4.4 ELSE IF topology == "hub-spoke":
      rt_hub = f"{provider_asn}:{rt_seq}"
      rt_spoke = f"{provider_asn}:{rt_seq + 1}"
      INCREMENT_SEQUENCE("rt_seq", by=2)
      RETURN {
        "rt_hub": rt_hub,
        "rt_spoke": rt_spoke
      }

4.5 ELSE IF topology == "point-to-point":
      rt_a = f"{provider_asn}:{rt_seq}"
      rt_b = f"{provider_asn}:{rt_seq + 1}"
      INCREMENT_SEQUENCE("rt_seq", by=2)
      RETURN {
        "rt_endpoint_a": rt_a,
        "rt_endpoint_b": rt_b
      }
```

**Design Rationale:**
- **Any-to-any:** Single RT ensures all PEs import/export each other's routes (full mesh reachability).
- **Hub-spoke:** Two RTs implement spoke isolation (spokes cannot see each other, only hub).
- **Point-to-point:** Unique RT pair ensures endpoints are isolated from other VPNs.

**RT Sequence Pool:**
- Range: 2000-65535 (global)
- Persistent counter (see [Section 11](#resource-pool-management))

**Example:**
```python
# Any-to-any VPN
rt_allocation = {
  "rt_export": ["65001:2000"],
  "rt_import": ["65001:2000"]
}

# Hub-spoke VPN
rt_allocation = {
  "rt_hub": "65001:2000",
  "rt_spoke": "65001:2001"
}
```

---

### Phase 5: VRF Configuration per PE Router

**Purpose:** Generate VRF configuration entries for each PE router based on topology.

**Algorithm:**
```
5.1 vrf_configs = []
5.2 endpoints_by_pe = GROUP_BY(endpoints, key="peRouter")

5.3 FOR pe_router, pe_endpoints IN endpoints_by_pe:
      
      5.3.1 rd = rd_allocation[pe_router]  // From Phase 3
      
      5.3.2 Determine RT import/export based on topology:
      
      IF topology == "any-to-any":
        rt_export = rt_allocation["rt_export"]
        rt_import = rt_allocation["rt_import"]
      
      ELSE IF topology == "hub-spoke":
        // Determine if this PE is hub or spoke
        is_hub = ANY(ep.role == "hub" for ep in pe_endpoints) OR \
                 pe_router == endpoints[0].peRouter
        
        IF is_hub:
          rt_export = [rt_allocation["rt_hub"]]
          rt_import = [rt_allocation["rt_spoke"], rt_allocation["rt_hub"]]  // Self-import!
        ELSE:
          rt_export = [rt_allocation["rt_spoke"]]
          rt_import = [rt_allocation["rt_hub"]]
      
      ELSE IF topology == "point-to-point":
        is_endpoint_a = pe_router == endpoints[0].peRouter
        IF is_endpoint_a:
          rt_export = [rt_allocation["rt_endpoint_a"]]
          rt_import = [rt_allocation["rt_endpoint_b"]]
        ELSE:
          rt_export = [rt_allocation["rt_endpoint_b"]]
          rt_import = [rt_allocation["rt_endpoint_a"]]
      
      5.3.3 interfaces = [ep.peInterface for ep in pe_endpoints]
      
      5.3.4 vrf_config = {
        "name": pe_router,
        "vrfs": [{
          "name": vrf_name,
          "table": 300,  // Dynamic allocation in production
          "rd": rd,
          "rt_export": rt_export,
          "rt_import": rt_import,
          "interfaces": interfaces
        }]
      }
      
      5.3.5 vrf_configs.append(vrf_config)

5.4 RETURN vrf_configs
```

**Design Rationale:**
- **Hub self-import:** Hub VRF must import its own export RT (`rt_hub`) to enable spoke-to-spoke hairpin routing. Without this, spoke-to-spoke traffic would be dropped at the hub (see [Section 6](#vpn-topology-patterns)).
- **Interface assignment:** Only CE-facing interfaces (e.g., `eth2`, `eth3`) are assigned to VRFs. Core-facing interfaces remain in the global routing table.

**Example Output (Hub-Spoke):**
```python
# Hub PE (pe2)
{
  "name": "pe2",
  "vrfs": [{
    "name": "uk-branches",
    "table": 300,
    "rd": "10.0.0.8:200",
    "rt_export": ["65001:2000"],  # hub RT
    "rt_import": ["65001:2001", "65001:2000"],  # spoke RT + self-import
    "interfaces": ["eth3"]
  }]
}

# Spoke PE (pe1)
{
  "name": "pe1",
  "vrfs": [{
    "name": "uk-branches",
    "table": 300,
    "rd": "10.0.0.7:200",
    "rt_export": ["65001:2001"],  # spoke RT
    "rt_import": ["65001:2000"],  # hub RT only
    "interfaces": ["eth2"]
  }]
}
```

---

### Phase 6: BGP Neighbor Configuration

**Purpose:** Generate eBGP neighbor entries for PE-to-CE peering.

**Algorithm:**
```
6.1 bgp_configs = []
6.2 endpoints_by_pe = GROUP_BY(endpoints, key="peRouter")

6.3 FOR pe_router, pe_endpoints IN endpoints_by_pe:
      
      neighbors = []
      
      FOR endpoint IN pe_endpoints:
        6.3.1 Derive CE peer IP from customer prefix:
              customer_network = PARSE_CIDR(endpoint.customerPrefix)
              ce_peer_ip = customer_network.first_host  // e.g., 10.100.0.1 from 10.100.0.0/24
        
        6.3.2 Query or allocate CE AS number:
              ce_router = QUERY_INFRASTRUCTURE(name=endpoint.customerRouter)
              remote_as = ce_router.bgp_as IF exists ELSE ALLOCATE_CUSTOMER_AS()
        
        6.3.3 neighbor = {
          "peer": ce_peer_ip,
          "remote_as": remote_as,
          "description": f"eBGP to {endpoint.customerRouter}"
        }
        
        neighbors.append(neighbor)
      
      6.3.4 bgp_config = {
        "name": pe_router,
        "bgp": {
          "vrfs": [{
            "name": vrf_name,
            "neighbors": neighbors
          }]
        }
      }
      
      bgp_configs.append(bgp_config)

6.4 RETURN bgp_configs
```

**Design Rationale:**
- **CE peer IP derivation:** By convention, CE IP is the first host IP in the customer prefix. Example: `10.100.0.0/24` → CE IP = `10.100.0.1`.
- **CE AS allocation:** If CE router has no assigned AS, allocate from customer AS pool (65100-65199).

**Example Output:**
```python
{
  "name": "pe1",
  "bgp": {
    "vrfs": [{
      "name": "uk-branches",
      "neighbors": [{
        "peer": "10.100.1.1",  # First IP in 10.100.1.0/24
        "remote_as": 65100,
        "description": "eBGP to ce1-spoke"
      }]
    }]
  }
}
```

---

### Phase 7: CE Router eBGP Configuration

**Purpose:** Generate eBGP configuration for CE routers to peer with PE VRFs.

**Algorithm:**
```
7.1 ce_configs = []
7.2 endpoints_by_ce = GROUP_BY(endpoints, key="customerRouter")

7.3 FOR ce_router, ce_endpoints IN endpoints_by_ce:
      
      7.3.1 Query CE router details:
            ce_info = QUERY_INFRASTRUCTURE(name=ce_router)
            bgp_as = ce_info.bgp_as IF exists ELSE ALLOCATE_CUSTOMER_AS()
            router_id = ce_info.router_id IF exists ELSE DERIVE_ROUTER_ID(ce_router)
      
      7.3.2 provider_as = GET_CONFIG("provider_asn")  // e.g., 65001
      
      neighbors = []
      FOR endpoint IN ce_endpoints:
        7.3.3 Derive PE peer IP (gateway from customer's perspective):
              customer_network = PARSE_CIDR(endpoint.customerPrefix)
              pe_peer_ip = customer_network.gateway  // e.g., 10.100.0.254 from 10.100.0.0/24
        
        7.3.4 neighbor = {
          "peer": pe_peer_ip,
          "remote_as": provider_as,
          "description": f"eBGP to {endpoint.peRouter} {vrf_name} VRF"
        }
        
        neighbors.append(neighbor)
      
      7.3.5 ce_config = {
        "name": ce_router,
        "protocols": {
          "bgp": {
            "as_number": bgp_as,
            "router_id": router_id,
            "neighbors": neighbors
          }
        }
      }
      
      ce_configs.append(ce_config)

7.4 RETURN ce_configs
```

**Design Rationale:**
- **PE peer IP derivation:** By convention, PE IP is the gateway IP of the customer prefix. Example: `10.100.0.0/24` → PE IP = `10.100.0.254`.
- **Customer AS pool:** Allocate sequentially from 65100-65199 (private AS range).

**Example Output:**
```python
{
  "name": "ce1-spoke",
  "protocols": {
    "bgp": {
      "as_number": 65100,
      "router_id": "10.0.0.80",
      "neighbors": [{
        "peer": "10.100.1.254",  # Gateway IP of 10.100.1.0/24
        "remote_as": 65001,
        "description": "eBGP to pe1 uk-branches VRF"
      }]
    }
  }
}
```

---

### Phase 8: QoS Policy Mapping

**Purpose:** Map SLA traffic class to VyOS traffic shaper configuration.

**Algorithm:**
```
8.1 qos_mapping = {
  "business-critical": {
    "priority": 1,
    "bandwidth_guarantee": "80%",
    "burst": "15k"
  },
  "standard": {
    "priority": 5,
    "bandwidth_guarantee": "50%",
    "burst": "10k"
  },
  "best-effort": {
    "priority": 7,
    "bandwidth_guarantee": null,
    "burst": "5k"
  }
}

8.2 policy = qos_mapping[sla.trafficClass]

8.3 // Store policy metadata in VyOSL3VPN CR annotations
    // Future: Generate VyOS traffic-policy configuration per PE interface

8.4 RETURN policy
```

**Note:** QoS policy generation is currently metadata-only. Future versions will generate VyOS `traffic-policy` configuration.

---

### Phase 9: VyOSL3VPN CR Generation

**Purpose:** Assemble final VyOSL3VPN Custom Resource YAML.

**Algorithm:**
```
9.1 routers = []
9.2 FOR vrf_config IN vrf_configs:
      bgp_config = FIND(bgp_configs, lambda b: b.name == vrf_config.name)
      
      router = {
        "name": vrf_config.name,
        "vrfs": vrf_config.vrfs,
        "bgp": bgp_config.bgp IF bgp_config ELSE {}
      }
      
      routers.append(router)

9.3 cr = {
  "apiVersion": "google.dev/v1",
  "kind": "VyOSL3VPN",
  "metadata": {
    "name": SANITIZE_CR_NAME(serviceName),
    "namespace": "network",
    "annotations": {
      "intent.orchestrator/traffic-class": sla.trafficClass,
      "intent.orchestrator/bandwidth": sla.bandwidth,
      "intent.orchestrator/latency": sla.latency,
      "intent.orchestrator/generated-by": "transport-orchestrator",
      "intent.orchestrator/generated-at": CURRENT_TIMESTAMP()
    }
  },
  "spec": {
    "underlayRef": underlayRef OR GET_CONFIG("default_underlay_ref"),
    "services": [{
      "name": vrf_name,
      "type": "l3vpn",
      "topology": topology
    }],
    "routers": routers,
    "ce_routers": ce_configs
  }
}

9.4 RETURN cr
```

**Example Output:** See [Section 12](#complete-example-transformation)

---

## Resource Pool Management

The orchestrator maintains three persistent resource pools for automated allocation.

### VRF Sequence Pool (Per-PE Router)

**Structure:**
```json
{
  "pe1": {
    "current": 205,
    "range": [200, 65535],
    "allocated": [200, 201, 202, 203, 204, 205]
  },
  "pe2": {
    "current": 203,
    "range": [200, 65535],
    "allocated": [200, 201, 202, 203]
  },
  "pe3": {
    "current": 200,
    "range": [200, 65535],
    "allocated": [200]
  }
}
```

**Allocation Algorithm:**
```
GET_NEXT_SEQUENCE("vrf_seq", scope=pe_router_name):
  1. Load pool state for pe_router_name
  2. next_seq = pool[pe_router_name]["current"] + 1
  3. IF next_seq > 65535:
       RAISE ResourceExhaustedError("VRF sequence pool exhausted for {pe_router_name}")
  4. pool[pe_router_name]["current"] = next_seq
  5. pool[pe_router_name]["allocated"].append(next_seq)
  6. PERSIST pool state
  7. RETURN next_seq
```

**Persistence:** Store in ConfigMap, database, or etcd.

---

### RT Sequence Pool (Global)

**Structure:**
```json
{
  "global": {
    "current": 2047,
    "range": [2000, 65535],
    "allocated": [2000, 2001, 2002, ..., 2047]
  }
}
```

**Allocation Algorithm:**
```
GET_NEXT_SEQUENCE("rt_seq", scope="global"):
  1. Load global pool state
  2. next_seq = pool["global"]["current"] + 1
  3. IF next_seq > 65535:
       RAISE ResourceExhaustedError("RT sequence pool exhausted")
  4. pool["global"]["current"] = next_seq
  5. pool["global"]["allocated"].append(next_seq)
  6. PERSIST pool state
  7. RETURN next_seq

INCREMENT_SEQUENCE("rt_seq", by=N):
  1. pool["global"]["current"] += N
  2. PERSIST pool state
```

**Note:** Hub-spoke and point-to-point topologies consume 2 RTs per VPN; any-to-any consumes 1 RT.

---

### Customer AS Pool

**Structure:**
```json
{
  "allocated": {
    "ce1-spoke": 65100,
    "ce2-spoke": 65101,
    "ce1-hub": 65102
  },
  "range": [65100, 65199],
  "next": 65103
}
```

**Allocation Algorithm:**
```
ALLOCATE_CUSTOMER_AS(ce_router_name):
  1. IF ce_router_name IN pool["allocated"]:
       RETURN pool["allocated"][ce_router_name]  // Already allocated
  2. next_as = pool["next"]
  3. IF next_as > 65199:
       RAISE ResourceExhaustedError("Customer AS pool exhausted")
  4. pool["allocated"][ce_router_name] = next_as
  5. pool["next"] = next_as + 1
  6. PERSIST pool state
  7. RETURN next_as
```

**Persistence:** Store in ConfigMap or database with CE router name as key.

---

## Complete Example Transformation

This example demonstrates all 9 decomposition phases for an any-to-any VPN with 3 endpoints.

### Input: L3ConnectivityIntent (Any-to-Any)

```json
{
  "serviceName": "Acme Corp WAN",
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
    }
  ],
  "sla": {
    "bandwidth": "100 Mbps per endpoint",
    "latency": "< 10ms",
    "availability": "99.99%",
    "trafficClass": "business-critical"
  }
}
```

### Decomposition Trace

**Phase 1: Validation**
- ✅ Service name sanitized: `"Acme Corp WAN"` → `"acme-corp-wan"`
- ✅ Topology valid: `"any-to-any"`
- ✅ Endpoint count: 3 (≥ 2 for any-to-any)
- ✅ All PE routers exist: `pe1`, `pe2`, `pe3`
- ✅ All CIDRs valid: `10.100.0.0/24`, `10.101.0.0/24`, `10.102.0.0/24`

**Phase 2: VRF Name Generation**
- VRF name: `"acme-corp-wan"` (no collision)

**Phase 3: RD Allocation**
- PE1 (loopback `10.0.0.7`): RD = `"10.0.0.7:200"` (VRF seq 200)
- PE2 (loopback `10.0.0.8`): RD = `"10.0.0.8:200"` (VRF seq 200)
- PE3 (loopback `10.0.0.10`): RD = `"10.0.0.10:200"` (VRF seq 200)

**Phase 4: RT Allocation**
- Topology: any-to-any → Single RT
- RT: `"65001:2000"` (global RT seq 2000)
- All PEs: export `["65001:2000"]`, import `["65001:2000"]`

**Phase 5: VRF Configuration**
- PE1: VRF `acme-corp-wan`, table `300`, interfaces `["eth2"]`
- PE2: VRF `acme-corp-wan`, table `300`, interfaces `["eth3"]`
- PE3: VRF `acme-corp-wan`, table `300`, interfaces `["eth2"]`

**Phase 6: BGP Neighbor Configuration**
- PE1 → CE peer `10.100.0.1` (first IP in `10.100.0.0/24`), AS `65100`
- PE2 → CE peer `10.101.0.1`, AS `65101`
- PE3 → CE peer `10.102.0.1`, AS `65102`

**Phase 7: CE Router Configuration**
- CE-core1: AS `65100`, peer `10.100.0.254` (gateway), remote AS `65001`
- CE-cell1: AS `65101`, peer `10.101.0.254`, remote AS `65001`
- CE-cell2: AS `65102`, peer `10.102.0.254`, remote AS `65001`

**Phase 8: QoS Mapping**
- Traffic class: `business-critical` → Priority 1, 80% guarantee, 15k burst

**Phase 9: CR Generation**
- See output below

### Output: VyOSL3VPN CR

```yaml
apiVersion: google.dev/v1
kind: VyOSL3VPN
metadata:
  name: acme-corp-wan
  namespace: network
  annotations:
    intent.orchestrator/traffic-class: business-critical
    intent.orchestrator/bandwidth: "100 Mbps per endpoint"
    intent.orchestrator/latency: "< 10ms"
    intent.orchestrator/generated-by: transport-orchestrator
    intent.orchestrator/generated-at: "2026-08-15T10:30:00Z"
spec:
  underlayRef: default-multi-underlay
  services:
    - name: acme-corp-wan
      type: l3vpn
      topology: any-to-any
  routers:
    - name: pe1
      vrfs:
        - name: acme-corp-wan
          table: 300
          rd: "10.0.0.7:200"  # ← Loopback-based RD
          rt_export: ["65001:2000"]
          rt_import: ["65001:2000"]
          interfaces: ["eth2"]
      bgp:
        vrfs:
          - name: acme-corp-wan
            neighbors:
              - peer: "10.100.0.1"  # ← First IP in customer prefix
                remote_as: 65100     # ← Allocated from customer AS pool
                description: "eBGP to ce-core1"
    - name: pe2
      vrfs:
        - name: acme-corp-wan
          table: 300
          rd: "10.0.0.8:200"  # ← Different loopback, same seq → unique RD
          rt_export: ["65001:2000"]
          rt_import: ["65001:2000"]
          interfaces: ["eth3"]
      bgp:
        vrfs:
          - name: acme-corp-wan
            neighbors:
              - peer: "10.101.0.1"
                remote_as: 65101
                description: "eBGP to ce-cell1"
    - name: pe3
      vrfs:
        - name: acme-corp-wan
          table: 300
          rd: "10.0.0.10:200"
          rt_export: ["65001:2000"]
          rt_import: ["65001:2000"]
          interfaces: ["eth2"]
      bgp:
        vrfs:
          - name: acme-corp-wan
            neighbors:
              - peer: "10.102.0.1"
                remote_as: 65102
                description: "eBGP to ce-cell2"
  ce_routers:
    - name: ce-core1
      protocols:
        bgp:
          as_number: 65100
          router_id: "10.0.0.110"  # ← Derived from CE router infrastructure
          neighbors:
            - peer: "10.100.0.254"  # ← Gateway IP from customer prefix
              remote_as: 65001
              description: "eBGP to pe1 acme-corp-wan VRF"
    - name: ce-cell1
      protocols:
        bgp:
          as_number: 65101
          router_id: "10.0.0.111"
          neighbors:
            - peer: "10.101.0.254"
              remote_as: 65001
              description: "eBGP to pe2 acme-corp-wan VRF"
    - name: ce-cell2
      protocols:
        bgp:
          as_number: 65102
          router_id: "10.0.0.112"
          neighbors:
            - peer: "10.102.0.254"
              remote_as: 65001
              description: "eBGP to pe3 acme-corp-wan VRF"
```

**Key Derived Values:**
- ✅ RD format: `{loopback-ip}:{vrf-seq}` (automated, globally unique)
- ✅ RT: Single shared RT `65001:2000` for any-to-any topology
- ✅ CE peer IPs: First host IP in customer prefix (`.1`)
- ✅ PE peer IPs: Gateway IP in customer prefix (`.254`)
- ✅ Customer AS: Sequential allocation (65100, 65101, 65102)

---

## Design Rules and Validation

The orchestrator must validate these rules before emitting CRDs.

### Physical / Infrastructure Rules

1. ✅ Every router has exactly one `eth0` interface connected to management network
2. ✅ Every router has exactly one `lo` interface connected to loopback network
3. ✅ Every P2P network has exactly **two** `connected_routers` entries
4. ✅ Multi-access LAN has exactly **one** connected router (CE gateway)
5. ✅ Interface names match between networks and router interface definitions
6. ✅ Each P2P network has a unique VLAN ID (no overlap)
7. ✅ No two routers share the same `router_id` (loopback IP)
8. ✅ No two `connected_routers` in the same network share the same IP
9. ✅ Router names are globally unique within infrastructure

### Underlay Rules

10. ✅ Only P, PE, and RR routers listed in `VyOSUnderlay.spec.routers` (CE excluded)
11. ✅ **All P routers MUST be configured** with OSPF and MPLS/LDP (missing P config breaks MPLS backbone)
12. ✅ OSPF enabled on all P, PE, RR routers with area `0.0.0.0`
13. ✅ OSPF passive interfaces: All routers should configure `passive_interfaces: ["lo"]`
14. ✅ LDP `interfaces` list only P2P core links (never management `eth0`, CE-facing, or `lo`)
15. ✅ Every PE peers with ALL RRs via iBGP (redundancy)
16. ✅ **Every RR peers with every other RR** via iBGP (RFC 4456 requirement)
17. ✅ Every RR lists all PE loopback IPs as `route_reflector_client: true`
18. ✅ `route_reflectors` list contains all RR loopback IPs

### L3VPN / VRF Rules

19. ✅ Only PE routers listed in `VyOSL3VPN.spec.routers`
20. ✅ Every VRF `rd` is unique across all VRFs in all PE routers
21. ✅ Hub VRFs import **both** spoke RT and hub RT (self-import for hairpin)
22. ✅ Spoke VRFs import **only** hub RT
23. ✅ VRF `interfaces` reference PE's CE-facing interface (not core-facing)
24. ✅ BGP neighbor `peer` IP is the CE's IP on PE-to-CE link
25. ✅ BGP neighbor `remote_as` matches CE router's AS number

### Addressing Rules

26. ✅ All loopback IPs (`10.0.0.x`) are unique across all routers
27. ✅ All P2P link subnets (`172.16.x.0/24`) do not overlap
28. ✅ All PE-to-CE link subnets (`10.x.x.0/24`) do not overlap with core or each other
29. ✅ All customer LAN subnets (`10.100.x.0/24`) do not overlap
30. ✅ Management IPs (`192.168.122.x`) are unique per router
31. ✅ `ip_address` in `connected_routers` falls within network's declared `subnet`

---

## Routing Configuration Best Practices

### BGP Architecture and Peering Rules

#### Route Reflector Configuration

**Critical Rule: Route Reflectors MUST Peer With Each Other**

All RRs must establish iBGP sessions with each other (RFC 4456 requirement).

**Why:** Without RR-to-RR peering, routes advertised to one RR may not propagate to clients of the other RR, creating connectivity black holes.

**Configuration Pattern:**
```yaml
# RR1
- name: "rr1"
  protocols:
    bgp:
      as_number: 65001
      router_id: "10.0.0.1"
      route_reflector: true
      neighbors:
        - peer: "10.0.0.2"  # ← RR-to-RR peering (NO route_reflector_client flag)
          remote_as: 65001
          description: "iBGP to rr2"
        - peer: "10.0.0.7"  # PE1 client
          remote_as: 65001
          route_reflector_client: true
        - peer: "10.0.0.8"  # PE2 client
          remote_as: 65001
          route_reflector_client: true

# RR2
- name: "rr2"
  protocols:
    bgp:
      neighbors:
        - peer: "10.0.0.1"  # ← RR-to-RR peering
          remote_as: 65001
```

**Note:** RR-to-RR sessions should **not** have `route_reflector_client: true` — RRs are peers, not clients.

#### PE Router BGP Peering

**Rule: Every PE MUST Peer With ALL Route Reflectors**

```yaml
- name: "pe1"
  protocols:
    bgp:
      as_number: 65001
      router_id: "10.0.0.7"
      neighbors:
        - peer: "10.0.0.1"  # RR1
          remote_as: 65001
        - peer: "10.0.0.2"  # RR2
          remote_as: 65001
```

**Never** peer PE routers directly with each other — defeats route reflection.

### OSPF Configuration Best Practices

#### Passive Interface Optimization

**Best Practice: Configure Loopback as OSPF Passive**

```yaml
ospf:
  router_id: "10.0.0.3"
  areas: [{ area: "0.0.0.0", type: "backbone" }]
  passive_interfaces: ["lo"]  # ← Recommended for all routers
```

**Benefits:** Reduces OSPF protocol overhead; prevents adjacency issues on loopback.

#### OSPF Area Design

**Rule: All Provider Routers in Area 0.0.0.0**

MPLS L3VPN networks use flat OSPF design — all P, PE, RR routers in backbone area.

**Never** split P/PE routers across multiple OSPF areas — breaks MPLS LDP neighbor discovery.

#### Interface Selection

**Critical Rule: Enable OSPF Only on Provider-Facing Interfaces**

OSPF should run on:
- ✅ All P-to-P links (core mesh)
- ✅ All P-to-RR links
- ✅ All P-to-PE links
- ✅ Loopback interfaces (lo)

OSPF should **NOT** run on:
- ❌ Management interface (eth0)
- ❌ PE-to-CE links (customer-facing, in VRFs)
- ❌ CE router interfaces

### MPLS/LDP Configuration Rules

#### LDP Interface Selection

**Critical Rule: Enable LDP ONLY on MPLS Core Interfaces**

```yaml
# P Router
mpls:
  enabled: true
  ldp:
    router_id: "10.0.0.3"
    interfaces: ["eth1", "eth2", "eth3", "eth4"]  # All core-facing

# PE Router
mpls:
  enabled: true
  ldp:
    router_id: "10.0.0.7"
    interfaces: ["eth1"]  # Core-facing only (NOT eth2 which faces CE)
```

**Why:** LDP on CE-facing interfaces serves no purpose (CE routers don't participate in MPLS).

#### LDP Router ID

**Rule: Always Use Loopback Address as LDP Router ID**

```yaml
mpls:
  enabled: true
  ldp:
    router_id: "10.0.0.7"  # Must match router's loopback IP
```

Ensures LDP transport address stability even if physical interfaces flap.

### P Router Configuration Requirements

**Critical Rule: P Routers MUST Have OSPF and MPLS/LDP**

P routers are Label Switch Routers (LSRs) in MPLS forwarding path. They **must** have:

1. ✅ OSPF enabled on all core-facing interfaces
2. ✅ MPLS/LDP enabled on all core-facing interfaces
3. ✅ Passive interface for loopback

**Complete P Router Example:**
```yaml
- name: "p1"
  protocols:
    ospf:
      router_id: "10.0.0.3"
      areas: [{ area: "0.0.0.0", type: "backbone" }]
      passive_interfaces: ["lo"]
    mpls:
      enabled: true
      ldp:
        router_id: "10.0.0.3"
        interfaces: ["eth1", "eth2", "eth3", "eth4"]
```

**Impact of Missing P Config:**
- OSPF not configured → No IGP reachability, BGP sessions fail
- MPLS/LDP not configured → No LSPs established, VPN traffic cannot transit core
- **Network completely non-functional**

### Route Reflector Configuration Requirements

**Rule: RRs MUST Have MPLS/LDP Even Though They Don't Forward Data**

RRs need MPLS/LDP because:
1. RRs must be reachable via MPLS LSPs from PE routers
2. RRs advertise VPNv4 routes with MPLS labels to PE clients
3. Underlay OSPF/LDP topology must be consistent across all BGP speakers

**RR Configuration Checklist:**
```yaml
- name: "rr1"
  protocols:
    ospf:
      router_id: "10.0.0.1"
      areas: [{ area: "0.0.0.0", type: "backbone" }]
      passive_interfaces: ["lo"]
    bgp:
      as_number: 65001
      router_id: "10.0.0.1"
      route_reflector: true
      neighbors:
        - peer: "10.0.0.2"  # RR-to-RR
          remote_as: 65001
        - peer: "10.0.0.7"  # PE clients
          remote_as: 65001
          route_reflector_client: true
    mpls:
      enabled: true
      ldp:
        router_id: "10.0.0.1"
        interfaces: ["eth1", "eth2"]  # ← Required!
```

### Common Misconfigurations and Prevention

#### Misconfiguration #1: Missing RR-to-RR Peering

**Symptom:** Intermittent connectivity; some routes missing from some PE routers

**Root Cause:** RR1 and RR2 not peering with each other

**Fix:** Add iBGP neighbors between all RRs (see BGP section above)

**Prevention:** Validate RR neighbor count = `(PE_count) + (RR_count - 1)`

#### Misconfiguration #2: P Routers Missing from Underlay

**Symptom:** PE routers cannot establish BGP sessions; `show bgp summary` shows Active/Connect state

**Root Cause:** P routers not in `VyOSUnderlay.spec.routers`, so they lack OSPF/MPLS

**Fix:** Add all P routers to underlay with OSPF and MPLS

**Prevention:** Verify every router with role "P" appears in underlay config

#### Misconfiguration #3: LDP Enabled on PE-to-CE Interfaces

**Symptom:** Unnecessary LDP adjacencies; possible CE router errors

**Root Cause:** PE router LDP interface list includes customer-facing interfaces

**Fix:** Limit LDP `interfaces` to only P-facing interfaces

**Prevention:** Map PE interfaces to connected networks; include only P/RR links

#### Misconfiguration #4: CE Routers in Underlay Configuration

**Symptom:** Underlay operator attempts to configure OSPF/MPLS on CE; CE never reaches Ready

**Root Cause:** CE routers incorrectly listed in `VyOSUnderlay.spec.routers`

**Fix:** Remove CE routers from underlay; they belong only in `VyOSL3VPN.spec.ce_routers`

**Prevention:** Only P, PE, RR routers in underlay

#### Misconfiguration #5: Passive Interface Not Set on Loopbacks

**Symptom:** Unnecessary OSPF overhead; potential OSPF flapping

**Root Cause:** Missing `passive_interfaces: ["lo"]`

**Fix:** Add passive_interfaces to all routers

**Prevention:** Include `passive_interfaces: ["lo"]` in all OSPF configs by default

---

## Dependencies and Prerequisites

The orchestrator validates these dependencies before decomposition.

### VyOSUnderlay Validation

**Requirement:** VyOSUnderlay CR must exist and be Ready before creating VyOSL3VPN.

**Validation Algorithm:**
```
1. IF NOT underlayRef:
     underlayRef = GET_CONFIG("default_underlay_ref")
2. underlay_cr = QUERY_K8S(kind="VyOSUnderlay", name=underlayRef, namespace="network")
3. IF NOT underlay_cr:
     RAISE 424 DependencyError(f"VyOSUnderlay '{underlayRef}' not found")
4. IF underlay_cr.status.phase != "Ready":
     RAISE 424 DependencyError(f"VyOSUnderlay '{underlayRef}' not Ready (phase={underlay_cr.status.phase})")
5. RETURN underlayRef
```

**Why:** L3VPN overlay requires functioning OSPF, MPLS/LDP, and iBGP underlay. Without Ready underlay, VRF routes cannot propagate.

### PE Router Infrastructure Data

**Requirement:** PE router loopback IP must be queryable for RD generation.

**Validation Algorithm:**
```
1. pe_router = QUERY_INFRASTRUCTURE(kind="VyOSRouter", name=pe_router_name)
2. IF NOT pe_router:
     RAISE 400 ValidationError(f"PE router '{pe_router_name}' not found")
3. loopback_ip = pe_router.spec.router_id
4. IF NOT loopback_ip:
     RAISE 500 InternalError(f"PE router '{pe_router_name}' has no router_id")
5. RETURN loopback_ip
```

### CE Router Data Availability

**Requirement:** CE router must exist (for AS allocation and eBGP config generation).

**Validation Algorithm:**
```
1. ce_router = QUERY_INFRASTRUCTURE(kind="VyOSRouter", name=ce_router_name)
2. IF NOT ce_router:
     RAISE 400 ValidationError(f"CE router '{ce_router_name}' not found")
3. RETURN ce_router
```

### Provider AS Configuration

**Requirement:** Provider AS number must be configured for RT generation.

**Validation Algorithm:**
```
1. provider_asn = GET_CONFIG("provider_asn")
2. IF NOT provider_asn:
     RAISE 500 ConfigError("provider_asn not configured")
3. RETURN provider_asn
```

**Default:** `65001` (private AS)

---

## Error Handling Taxonomy

The orchestrator returns structured errors with HTTP status codes for API responses.

### 400 Validation Errors

| Condition | Error Code | Message Template | Remediation |
|-----------|-----------|------------------|-------------|
| Invalid service name | `INVALID_SERVICE_NAME` | `"serviceName contains invalid characters"` | Use alphanumeric + hyphen only |
| Invalid topology | `INVALID_TOPOLOGY` | `"topology must be any-to-any, hub-spoke, or point-to-point"` | Check topology enum |
| Insufficient endpoints | `INSUFFICIENT_ENDPOINTS` | `"topology {topology} requires at least {min} endpoints"` | Add more endpoints |
| Invalid CIDR | `INVALID_CIDR` | `"customerPrefix '{prefix}' is not a valid CIDR"` | Fix CIDR format |
| PE router not found | `PE_ROUTER_NOT_FOUND` | `"PE router '{router}' not found in infrastructure"` | Verify PE router exists |
| CE router not found | `CE_ROUTER_NOT_FOUND` | `"CE router '{router}' not found in infrastructure"` | Verify CE router exists |

### 424 Dependency Errors

| Condition | Error Code | Message Template | Remediation |
|-----------|-----------|------------------|-------------|
| Underlay not found | `UNDERLAY_NOT_FOUND` | `"VyOSUnderlay '{underlayRef}' not found"` | Create underlay first |
| Underlay not ready | `UNDERLAY_NOT_READY` | `"VyOSUnderlay '{underlayRef}' not Ready (phase={phase})"` | Wait for underlay Ready |

### 409 Conflict Errors

| Condition | Error Code | Message Template | Remediation |
|-----------|-----------|------------------|-------------|
| Duplicate VPN name | `DUPLICATE_VPN_NAME` | `"VyOSL3VPN with serviceName '{name}' already exists"` | Use different service name |
| VRF sequence exhausted | `VRF_SEQ_EXHAUSTED` | `"VRF sequence pool exhausted for PE router '{pe}'"` | Contact administrator |
| RT sequence exhausted | `RT_SEQ_EXHAUSTED` | `"RT sequence pool exhausted"` | Contact administrator |
| Customer AS exhausted | `CUSTOMER_AS_EXHAUSTED` | `"Customer AS pool exhausted"` | Expand AS pool range |

### Error Response Format

```json
{
  "error": {
    "code": "UNDERLAY_NOT_READY",
    "message": "VyOSUnderlay 'default-multi-underlay' not Ready (phase=Configuring)",
    "status": 424,
    "details": {
      "underlayRef": "default-multi-underlay",
      "currentPhase": "Configuring",
      "expectedPhase": "Ready"
    }
  }
}
```

---

## Example: Hub-and-Spoke L3VPN

This example demonstrates a complete hub-and-spoke L3VPN with 1 hub + 2 spokes, showing all three layers (Infrastructure, Underlay, L3VPN).

### Topology Summary

```
         [ce1-spoke]──(10.50.50.0/24)──[pe1]
                                          │
                           (172.16.90.0/24 via p1)
                                          │
[ce2-spoke]──(10.60.60.0/24)──[pe3]    [p1]──[p2]──[rr1]
                                  │      │
                        (p4-pe3)  │      │(p1-p3)
                               [p4]──[p3]──[rr2]
                                          │
                           (p3-pe2 / p1-pe2)
                                          │
                                        [pe2]
                                          │
                               (10.80.80.0/24)
                                          │
                                     [ce1-hub]
```

### Router Role Assignments

| Router | Role | Loopback | BGP Role |
|--------|------|----------|----------|
| p1 | P | 10.0.0.3 | — |
| p2 | P | 10.0.0.4 | — |
| p3 | P | 10.0.0.5 | — |
| p4 | P | 10.0.0.6 | — |
| rr1 | RR | 10.0.0.1 | Route Reflector |
| rr2 | RR | 10.0.0.2 | Route Reflector |
| pe1 | PE | 10.0.0.7 | RR client |
| pe2 | PE | 10.0.0.8 | RR client (hub) |
| pe3 | PE | 10.0.0.10 | RR client |
| ce1-spoke | CE | 10.0.0.80 | eBGP peer (spoke) |
| ce1-hub | CE | 10.0.0.100 | eBGP peer (hub) |
| ce2-spoke | CE | 10.0.0.90 | eBGP peer (spoke) |

### VRF Design

| PE | VRF | Topology | RD | RT Export | RT Import |
|----|-----|----------|----|-----------|-----------|
| pe1 | BLUE_SPOKE | spoke | `10.0.0.7:200` | `65001:2001` (spoke RT) | `65001:2000` (hub RT) |
| pe2 | BLUE_HUB | hub | `10.0.0.8:200` | `65001:2000` (hub RT) | `65001:2001`, `65001:2000` (spoke + self) |
| pe3 | BLUE_SPOKE | spoke | `10.0.0.10:200` | `65001:2001` (spoke RT) | `65001:2000` (hub RT) |

**Note:** Hub VRF self-imports hub RT (`65001:2000`) to enable spoke-to-spoke hairpin routing.

### CRD Skeleton

See original designdoc.md lines 1315-1582 for complete YAML (infrastructure, underlay, L3VPN layers).

**Key Updates for v2.0:**
- RD format changed from `{interface-ip}:{id}` to `{loopback-ip}:{vrf-seq}`
- VRF table IDs changed from fixed (200/400) to dynamic (300)
- All RRs peer with each other via iBGP
- All PEs peer with ALL RRs for redundancy

---

## Changelog

### v2.0 (2026-08-15)

**Combined designdoc.md (v1.0) and ORCHESTRATOR.md**

**Added:**
- Orchestrator task flow diagram and workflow mapping
- L3ConnectivityIntent input schema (TMF921/ACTN-aligned)
- 9-phase decomposition algorithm with design rationale
- Resource pool management (VRF seq, RT seq, Customer AS)
- Complete example transformation with phase-by-phase trace
- Error handling taxonomy with HTTP status codes
- Dependencies and prerequisites section

**BREAKING CHANGES:**
- **RD format:** Changed from `{interface-ip}:{id}` to `{loopback-ip}:{vrf-seq}` for automated allocation
- **VRF table ID:** Changed from fixed pattern (spoke=200, hub=400) to dynamic allocation starting at 200

**Reorganized:**
- Algorithmic-first structure (decomposition before design theory)
- Merged best practices into algorithm phase rationale
- Cross-referenced sections for agent navigation

**Retained:**
- Full 3-layer network architecture (Infrastructure, Underlay, L3VPN)
- All 6 router roles (P, PE, RR, CE, PA, AR)
- All validation rules (31 rules across all layers)
- Routing best practices (BGP, OSPF, MPLS/LDP)
- Complete hub-and-spoke reference example

---

**End of Document**
