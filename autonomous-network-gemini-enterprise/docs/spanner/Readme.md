# Spanner Network Graph

Spanner stores the complete network topology as a temporal, multi-domain knowledge graph. It captures both the [physical network infrastructure](/docs/network/Readme.md) and [logical service models](/docs/automation/Readme.md), tracking every change over time.

This document explains the **conceptual domain model** and how to query the network graph. For detailed schema reference, column mappings, and data sources, see **[Table Reference](table_reference.md)**.

---

## Multi-Domain Architecture

The network is modeled as **six independent domains**, each with its own Spanner database and property graph. This separation mirrors real-world organizational boundaries and enables independent scaling and access control.

| Domain | Database | Models |
|--------|----------|--------|
| **Transport** | `transporttopology-db` | Physical network infrastructure (routers, links, interfaces), L3VPN services, BGP sessions, routing metrics |
| **Infrastructure** | `infratopology-db` | Compute hosts (containers), Linux bridge networks |
| **Core** | `coretopology-db` | 5G packet core (FiveGCore orchestrators, network functions, 3GPP interfaces) |
| **Radio** | `radiotopology-db` | Radio access network (cell sites, UEs, RAN connections) |
| **Service** | `servicetopology-db` | End-to-end traffic flows and performance metrics |
| **Operations** | `agentops-db` | Fault injection events, AI agent traces, operational logs |

**Cross-domain references** use logical ID strings (e.g., `device_id = "host:amf-node"` in the Core domain points to a `ComputeHost` in the Infrastructure domain). There are no foreign key constraints between databases.

**All topology changes are CRD-driven**. The Kubernetes operator writes to Spanner when CRDs are created, updated, or deleted. Runtime monitoring (metrics, logs) writes to time-series tables but never modifies topology.

---

## Temporal History Model

Every topology table uses **Slowly Changing Dimensions (SCD) Type 2**. When a network entity changes, the previous row is closed (by setting `valid_end_ts`) and a new row is inserted. This preserves complete history for:
- Post-incident analysis ("what was the BGP configuration when the outage started?")
- Temporal graph queries ("show me the network topology as of 3pm yesterday")
- ML training on historical topology evolution

**Key concept**: Every entity has a timeline of versions. The current state has `valid_end_ts IS NULL`. Past states have timestamps showing when they were active.

**Primary key pattern**: `(id, valid_start_ts DESC)` — newest version first.

---

## Domain Models

### Transport Domain — `transporttopology-db`

**Models**: Physical network infrastructure and MPLS L3VPN overlay services

The transport domain represents the underlay network (VyOS routers, interfaces, physical links, subnets) and the L3VPN services running on top. It's the foundation of the network topology.

**Physical layer**:
- **Routers** (`PhysicalRouter`) — VyOS devices with geographic locations and roles (PE, P, CE, RR)
- **Interfaces** (`PhysicalInterface`) — router ports with IP addresses and status
- **Links** (`PhysicalLink`) — network segments connecting router interfaces
- **Subnets** (`LogicalSubnet`) — IP addressing schemes on links and interfaces

**Service layer**:
- **L3VPN services** (`L3VPNService`) — customer VPN definitions with topology type (hub-and-spoke, mesh)
- **VRFs** (`VRF`) — VPN instances on PE routers, each with a Route Distinguisher
- **BGP sessions** (`BGPSession`) — CE↔PE eBGP peering relationships per VRF

**Observability**:
- **System metrics** (`RouterSystemMetrics`) — CPU, memory, interface counters (scraped every 20s)
- **Routing metrics** (`RouterRoutingSeries`) — BGP peer state, OSPF adjacencies, route counts, keyed on the full FRR label set (supersedes `RouterRoutingMetrics`)
- **GNN embeddings** (`NodeEmbedding`) — ML-generated anomaly scores and explanations
- **Logs** (`KgLogEntryNode`) — router log entries with vector embeddings

**Property graph**: `transportGraph` enables path discovery (router → interface → link → interface → router) and service topology traversal (VPN → VRF → router).

**Configuration snapshots**: `NetworkDescriptor` stores complete network configuration bundles (infrastructure + underlay + VPNs + traffic tests) for the supervisor UI. This is the only non-temporal table in the domain.

---

### Infrastructure Domain — `infratopology-db`

**Models**: Container compute infrastructure and Linux bridge networks

The infrastructure domain represents the virtualization layer — where containers run and how they're networked.

**Compute layer**:
- **Compute hosts** (`ComputeHost`) — containers hosting network functions, cell sites, or traffic agents
- **Linux networks** (`LinuxNetwork`) — bridge networks connecting containers to each other and to VyOS routers
- **Metrics** (`ComputeMetrics`) — host-level performance metrics

**Cross-domain linkage**: `LinuxNetwork.connected_routers` is a JSON array of transport router names, enabling cross-domain topology queries (e.g., "which routers connect to this Linux network?").

**Property graph**: `infraGraph` connects hosts to their networks.

---

### Core Domain — `coretopology-db`

**Models**: 5G packet core network functions and their 3GPP-defined interfaces

The core domain represents the 5G control and user plane — the free5gc network functions and how they interconnect.

**Core network layer**:
- **5G Core instances** (`FiveGCore`) — orchestrators managing a set of network functions
- **Network functions** (`NetworkFunction`) — individual NFs (AMF, SMF, UPF, NRF, AUSF, UDM, PCF, UDR)

**Observability**:
- **Metrics** (`NetworkFunctionMetrics`) — NF-level performance and health metrics
- **GNN embeddings** (`NodeEmbedding`) — anomaly detection for network functions
- **Logs** (`KgLogEntryNode`) — NF log entries

**Property graph**: `coreGraph` traverses the 5G service graph (Core → NFs → interfaces → NFs).

---

### Radio Domain — `radiotopology-db`

**Models**: Radio Access Network (gNodeB cell sites and user equipment)

The radio domain represents the 5G RAN — simulated UERANSIM cell sites and the UEs attached to them.

**RAN layer**:
- **Cell sites** (`CellSite`) — gNodeB instances with 5G radio parameters (gNB ID, NCI, PLMN, TAC)
- **User equipment** (`UE`) — simulated devices with IMSIs and slice selection (SST/SD)

**Observability**:
- **Metrics** (`CellSiteMetrics`) — per-cell and per-UE metrics
- **GNN embeddings** (`NodeEmbedding`) — anomaly detection for cell sites
- **Logs** (`KgLogEntryNode`) — cell site and UE log entries

**Property graph**: `radioGraph` connects cell sites to their UEs and (logically) to core NFs.

---

### Service Domain — `servicetopology-db`

**Models**: End-to-end traffic flows and their performance metrics

The service domain represents application-level traffic flows between compute hosts, capturing both topology (what's connected to what) and performance (throughput, latency, jitter, packet loss).

**Service layer**:
- **Traffic flows** (`TrafficFlow`) — provisioned flows between source and destination devices with lifecycle phases (pending → running → completed)
- **Flow metrics** (`TrafficFlowSeries`) — time-series performance data from the `traffic-agent` (throughput, latency, jitter, packet loss, byte counts), one row per reporting end (`role`)
- **GNN embeddings** (`NodeEmbedding`) — anomaly detection for traffic flows

**Cross-domain linkage**: `src_device_id` and `dst_device_id` point to `ComputeHost` entries in the infrastructure domain, enabling end-to-end flow path analysis.

**Observability**: Traffic agent metrics are scraped from source and destination containers and indexed by flow ID, enabling performance troubleshooting ("show me throughput degradation for this flow over the past hour").

**Property graph**: `serviceGraph` connects flows to their metrics time-series.


---

### Operations Domain — `agentops-db`

**Models**: Fault injection events, AI agent traces, and operational observability

The operations domain is a cross-cutting layer that captures fault scenarios, AI agent behavior, and complete network configuration snapshots. It's where chaos engineering and agent observability data lives.

**Fault injection**:
- **Fault events** (`FaultEvent`) — tracks fault injection lifecycle (Injecting → Active → Restored) with full history
- Cross-domain targeting: `target_domain`, `target_id`, and `target_type` enable faults to target any entity in any domain

**Agent observability**:
- **Agent traces** (`AgentTrace`) — distributed trace spans from AI agents (A2A protocol) for debugging agent decision-making
- **Network descriptors** (`NetworkDescriptor`) — complete network configuration bundles (infrastructure + underlay + VPNs + traffic tests) saved by the supervisor UI

**Intent management**:
- **Intent records** (`IntentRecords`) — TMF921 Intent Management for L3 VPN provisioning via the Transport Intent API
- Tracks intent lifecycle: Acknowledged → InProgress → Completed/Rejected
- Maps high-level network intents to VyOSL3VPN custom resources

**Why a separate domain?** Fault events, agent traces, and intent management are operational concerns that span all domains. Keeping them separate avoids polluting domain-specific topology databases with operational metadata.

See [Table Reference: Operations Domain](table_reference.md#operations-domain--agentops-db) for detailed schema.

---

## Querying the Network Graph

Spanner supports both **SQL** and **GQL** (Graph Query Language) for querying the network topology.

**SQL queries** are used for:
- Fetching current topology state (`WHERE valid_end_ts IS NULL`)
- Time-travel queries (reconstructing topology at a specific point in time)
- Joining topology with metrics time-series
- Aggregating performance data

**GQL (Graph Query Language)** enables graph traversal for:
- Path discovery (e.g., trace route between two routers)
- Relationship exploration (e.g., find all VRFs on a router)
- Multi-hop queries across property graph edges

Each domain has its own property graph: `transportGraph`, `coreGraph`, `radioGraph`, `infraGraph`, `serviceGraph`.

**Cross-domain queries** use logical ID references at the application layer. Property graphs cannot span database boundaries, so cross-domain relationships (e.g., NetworkFunction → ComputeHost) are resolved by matching ID strings like `device_id = "host:amf-node"`.

See [CRD → Spanner Mapping](crd_spanner_mapping.md) for query examples.

---

## ID Conventions

All entities use structured IDs with domain-specific prefixes. Examples:
- `router:pe1` (transport)
- `host:amf-node` (infrastructure)
- `nf:amf-01` (core)
- `cellsite:gnb-01` (radio)
- `flow:d1-to-hub-tcp` (service)
- `fault:bgp-drop-pe1` (operations)

This enables cross-domain references without foreign keys. See [Table Reference](table_reference.md#summary-cross-domain-logical-references) for the complete ID convention list.

---

## Design Principles

**Why no foreign keys?** Rigid FK constraints are omitted to:
- Increase write throughput (no FK validation overhead)
- Support SCD Type 2 temporal history (complex temporal FK validation)
- Enable cross-database logical references (Spanner doesn't support cross-DB FKs)

**How are relationships maintained?**
1. **Logical IDs** — `router_id` columns store ID strings like `"router:pe1"`, validated at the application layer
2. **Edge views** — SQL views handle temporal overlap logic automatically (e.g., `HasInterface_Edge` joins routers and interfaces with valid timestamp overlap)
3. **Cross-domain refs** — application-layer joins using ID conventions (e.g., `device_id = "host:amf-node"`)

---

## Related Documentation

- **[Table Reference](table_reference.md)** — Complete schema reference with column mappings and data sources
- **[CRD → Spanner Mapping](crd_spanner_mapping.md)** — How Kubernetes CRDs map to Spanner tables
- **[Spanner Schema](../../gcp/spanner.tpl.yaml)** — DDL definitions for all databases and property graphs
