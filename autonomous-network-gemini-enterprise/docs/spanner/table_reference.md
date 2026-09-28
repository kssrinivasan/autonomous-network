# Spanner Table Reference

Complete reference for all Spanner tables across the multi-domain network topology database. Each table below shows its columns, data types, and the source of each value.

![](/docs/drawings/spanner/schema.drawio.svg)

---

## Transport Domain — `transporttopology-db`

### PhysicalRouter

**Purpose**: Stores VyOS router metadata and configuration  
**Source CRD**: `VyOSRouter`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_physical_router()`  
**ID Pattern**: `router:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"router:" + metadata.name` | Primary key (with valid_start_ts) |
| `name` | STRING(MAX) | `metadata.name` | Router name from CRD |
| `vendor` | STRING(MAX) | `spec.vendor` | Default: `"VyOS"` |
| `model` | STRING(MAX) | `spec.model` | Default: `"Virtual"` |
| `location_city` | STRING(MAX) | `spec.location.city` | Geographic location |
| `location_lat` | FLOAT64 | `spec.location.latitude` | GPS latitude |
| `location_lon` | FLOAT64 | `spec.location.longitude` | GPS longitude |
| `role` | STRING(MAX) | `spec.role` | Values: `"PE"`, `"P"`, `"CE"`, `"RR"` |
| `status` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `config` | JSON | Sanitized CRD body | Full configuration snapshot |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start timestamp |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current version |

---

### PhysicalInterface

**Purpose**: Stores router interface configuration  
**Source CRD**: `VyOSRouter`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_physical_router()`  
**ID Pattern**: `router:{router-name}:interface:{iface-name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"router:" + router_name + ":interface:" + iface_name` | Primary key |
| `router_id` | STRING(MAX) | `"router:" + parent_router_name` | Logical FK to PhysicalRouter |
| `name` | STRING(MAX) | `spec.interfaces[*].name` | Interface name (e.g., `eth1`) |
| `speed` | STRING(MAX) | Derived from PhysicalLink bandwidth or spec | Fallback: `"1G"` |
| `media_type` | STRING(MAX) | `spec.interfaces[*].media_type` | Interface media type |
| `ip_address` | STRING(MAX) | `spec.interfaces[*].address` (CIDR suffix stripped) | IP address only |
| `mac_address` | STRING(MAX) | Runtime or spec | MAC address |
| `status` | STRING(MAX) | Derived from `status.phase` | Values: `"UP"`, `"DOWN"`, `"ADMIN_DOWN"` |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### PhysicalLink

**Purpose**: Physical network segment connecting router interfaces  
**Source CRD**: `VyOSInfrastructure`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_vyos_infrastructure()`  
**ID Pattern**: `link:{network-name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"link:" + spec.networks[*].name` | Created when ≥2 routers connected |
| `name` | STRING(MAX) | `spec.networks[*].name` | Network name |
| `bandwidth` | STRING(MAX) | `spec.networks[*].bandwidth` | Default: `"unknown"` |
| `status` | STRING(MAX) | Derived | Default: `"UP"` |
| `properties` | JSON | Full network object from spec | Includes connected_routers array |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### LogicalSubnet

**Purpose**: IP subnets on network segments or interfaces  
**Source CRD**: `VyOSInfrastructure`, `VyOSRouter`  
**Written by**: `sync_vyos_infrastructure()`, `sync_physical_router()`  
**ID Pattern**: `subnet:{network-name}` or `subnet:{cidr}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"subnet:" + name` or `"subnet:" + cidr` | Two naming conventions |
| `cidr` | STRING(MAX) | `spec.networks[*].subnet` or interface address | CIDR notation |
| `network_type` | STRING(MAX) | `spec.networks[*].network_type` | e.g., `"p2p"`, `"lan"` |
| `description` | STRING(MAX) | `spec.networks[*].description` | Human-readable description |
| `operational_state` | STRING(MAX) | Runtime-derived | Operational status |
| `mtu` | INT64 | Runtime or spec | MTU size |
| `mac_address` | STRING(MAX) | Runtime | Bridge MAC address |
| `bridge_ip` | STRING(MAX) | Runtime | Bridge IP if applicable |
| `host_device_name` | STRING(MAX) | Runtime | Linux device name |
| `properties` | JSON | Full network object | Complete spec |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### L3VPNService

**Purpose**: Logical VPN service definition  
**Source CRD**: `VyOSL3VPN`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_l3vpn_service()`  
**ID Pattern**: `vpn:{service-name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"vpn:" + spec.services[*].name` | Primary key |
| `customer_id` | STRING(MAX) | `spec.customer_id` or derived | Customer identifier |
| `name` | STRING(MAX) | `spec.services[*].name` | VPN service name |
| `service_type` | STRING(MAX) | `spec.services[*].type` | Default: `"l3vpn"` |
| `topology` | STRING(MAX) | `spec.services[*].topology` | Values: `"hub"`, `"spoke"`, `"mesh"` |
| `status` | STRING(MAX) | Derived from CRD status | Values: `"Ready"`, `"Unknown"` |
| `config` | JSON | Full service spec | Complete configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### VRF

**Purpose**: Virtual Routing and Forwarding instance on a router  
**Source CRD**: `VyOSL3VPN`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_l3vpn_service()`  
**ID Pattern**: `vrf:{router-name}:{service-name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"vrf:" + router_name + ":" + service_name` | Primary key |
| `router_id` | STRING(MAX) | `"router:" + router_name` | Logical FK to PhysicalRouter |
| `vpn_id` | STRING(MAX) | `"vpn:" + service_name` | Logical FK to L3VPNService |
| `name` | STRING(MAX) | Service name or VRF name | VRF identifier |
| `rd` | STRING(MAX) | `spec.routers[*].vrfs[*].rd` | Route Distinguisher (e.g., `"10.50.50.1:1011"`) |
| `status` | STRING(MAX) | Derived from L3VPN status | Values: `"Active"`, `"Pending"` |
| `config` | JSON | VRF configuration | Complete VRF spec |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### BGPSession

**Purpose**: CE↔PE eBGP session scoped to a VRF  
**Source CRD**: `VyOSL3VPN`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_l3vpn_service()`  
**ID Pattern**: `bgp:{router}:{service}:{peer-ip}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"bgp:" + router + ":" + service + ":" + peer_ip` | Primary key |
| `vrf_id` | STRING(MAX) | `"vrf:" + router + ":" + service` | Logical FK to VRF |
| `local_as` | INT64 | Derived from router BGP config | Local AS number |
| `remote_as` | INT64 | `spec.routers[*].bgp.vrfs[*].neighbors[*].remote_as` | Remote AS number |
| `peer_ip` | STRING(MAX) | `spec.routers[*].bgp.vrfs[*].neighbors[*].peer` | BGP peer IP address |
| `status` | STRING(MAX) | Derived from L3VPN status | Values: `"Established"`, `"Idle"` |
| `config` | JSON | BGP neighbor configuration | Complete neighbor spec |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### Interface_Link

**Purpose**: Edge table connecting PhysicalInterface to PhysicalLink  
**Source CRD**: `VyOSInfrastructure`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_vyos_infrastructure()`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `interface_id` | STRING(MAX) | `"router:{router}:interface:{iface}"` | Primary key part 1 |
| `link_id` | STRING(MAX) | `"link:{network-name}"` | Primary key part 2 |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### Subnet_Association

**Purpose**: Edge table mapping entities to LogicalSubnets  
**Source CRD**: `VyOSRouter`  
**Written by**: `operator/src/graph/transport_tasks.py:sync_physical_router()`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `entity_id` | STRING(MAX) | Interface ID or other entity | Primary key part 1 |
| `subnet_id` | STRING(MAX) | `"subnet:{cidr}"` or `"subnet:{name}"` | Primary key part 2 |
| `entity_type` | STRING(MAX) | `"Interface"` or other | Entity type discriminator |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### NodeEmbedding

**Purpose**: GNN-generated embeddings and anomaly scores  
**Source**: GNN training pipeline  
**Written by**: `domains/transport/gnn/src/serve.py` or training pipeline

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | `GENERATE_UUID()` | Auto-generated UUID |
| `node_id` | STRING(MAX) | Entity ID (e.g., `router:pe1`) | Primary key part 1 |
| `node_type` | STRING(MAX) | Entity type | e.g., `"PhysicalRouter"`, `"PhysicalInterface"` |
| `hetgnn_embedding` | ARRAY<FLOAT64> | GNN model output | Vector embedding |
| `hetgnn_score` | FLOAT64 | GNN model output | Anomaly score (0.0-1.0) |
| `anomaly_explanation` | JSON | GNN model output | Explanation object |
| `timestamp` | TIMESTAMP | Model inference time | Primary key part 2 (DESC) |

---

### KgLogEntryNode

**Purpose**: Log entries as knowledge graph nodes with embeddings  
**Source**: Log collector  
**Written by**: `services/logcollector/`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(17) | Generated | Primary key |
| `timestamp` | TIMESTAMP | Log entry timestamp | Primary key part 2 |
| `severity` | STRING(16) | Log severity level | e.g., `"ERROR"`, `"INFO"` |
| `source` | STRING(MAX) | Log source identifier | Component/service name |
| `message` | STRING(MAX) | Log message | Message text |
| `content` | STRING(MAX) | Full log content | Complete log entry |
| `embedding` | ARRAY<FLOAT64> | Embedding model | Vector embedding of log |

---

### RouterSystemMetrics

**Purpose**: System-level metrics from routers (CPU, memory, interfaces)  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 4 (DESC) |
| `router_name` | STRING(MAX) | Router name | Primary key part 1 |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 2 (e.g., `node_load1`) |
| `interface` | STRING(MAX) | Interface name or empty | Primary key part 3 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Prometheus labels |

**Common metrics**: `node_load1`, `node_memory_*`, `node_network_receive_bytes_total`, `node_network_transmit_bytes_total`, `node_network_up`, `node_network_carrier`, `node_network_mtu_bytes`

---

### RouterRoutingSeries

**Purpose**: Routing protocol metrics (BGP, OSPF)  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 10 (DESC) |
| `router_name` | STRING(MAX) | Router name | Primary key part 1 |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 2 |
| `vrf` | STRING(MAX) | VRF name or empty | Primary key part 3 |
| `afi` | STRING(MAX) | Address family or empty | Primary key part 4 |
| `safi` | STRING(MAX) | Sub-address family or empty | Primary key part 5 — `unicast` vs `vpn` |
| `peer_ip` | STRING(MAX) | BGP peer IP or empty | Primary key part 6 |
| `iface` | STRING(MAX) | Interface or empty | Primary key part 7 — one OSPF adjacency per interface |
| `area` | STRING(MAX) | OSPF area or empty | Primary key part 8 |
| `collector` | STRING(MAX) | FRR sub-collector or empty | Primary key part 9 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Full Prometheus label set |

**Common metrics**: `frr_bgp_peer_uptime_seconds`, `frr_bgp_peer_prefixes_advertised_count_total`, `frr_ospf_neighbor_adjacencies`, `frr_route_total`, `frr_route_total_fib`

**Replaces `RouterRoutingMetrics`**, which was keyed only on
`(router_name, metric_name, vrf, afi, peer_ip, timestamp)`. `safi`, `iface`, `area`
and `collector` all vary independently of that key, so every series differing only in
one of them overwrote the others on write — an OSPF speaker with five adjacencies
stored one row, and `frr_bgp_*{safi=vpn}` displaced `{safi=unicast}`. Spanner cannot
alter a primary key and the Config Connector `ddl` list is append-only, so the fix is a
new table. `RouterRoutingMetrics` is still declared but nothing writes it; do not read it.

---

## Infrastructure Domain — `infratopology-db`

### ComputeHost

**Purpose**: Container hosts running network functions or agents  
**Source CRD**: `Device`  
**Written by**: `operator/src/graph/infra_tasks.py:sync_compute_host()`  
**ID Pattern**: `host:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"host:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Host name |
| `network_name` | STRING(MAX) | `spec.network_name` | Network name |
| `ip_address` | STRING(MAX) | `status.ip_address` or assigned | Primary IP address |
| `mgmt_ip` | STRING(MAX) | `status.mgmt_ip` | Management IP |
| `gateway` | STRING(MAX) | `spec.gateway` | Gateway IP |
| `extra_interfaces` | JSON | `spec.extra_interfaces` | Additional network interfaces |
| `phase` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### LinuxNetwork

**Purpose**: Linux bridge networks connecting containers  
**Source CRD**: `LinuxNetwork` (generated by `VyOSInfrastructure`)  
**Written by**: `operator/src/graph/infra_tasks.py:sync_linux_network()`  
**ID Pattern**: `linuxnetwork:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"linuxnetwork:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Network name |
| `namespace` | STRING(MAX) | `metadata.namespace` | Kubernetes namespace |
| `network_type` | STRING(MAX) | `spec.network_type` | Network type |
| `cidr` | STRING(MAX) | `spec.cidr` | CIDR block |
| `gateway` | STRING(MAX) | `spec.gateway` | Gateway IP |
| `bandwidth` | STRING(MAX) | `spec.bandwidth` | Bandwidth limit |
| `connected_routers` | JSON | `spec.connected_routers` | Array of router names (cross-domain logical reference) |
| `phase` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `config` | JSON | Full spec | Complete configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### ComputeMetrics

**Purpose**: Metrics from compute hosts  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `host_id` | STRING(MAX) | `"host:{name}"` | Primary key part 1 |
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 2 (DESC) |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 3 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Prometheus labels |

---

### NodeEmbedding (infra)

**Purpose**: GNN embeddings for infrastructure entities  
**Source**: GNN training pipeline  
**Written by**: `domains/transport/gnn/src/serve.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | `GENERATE_UUID()` | Auto-generated UUID |
| `node_id` | STRING(MAX) | Entity ID (e.g., `host:dev1`) | Primary key part 1 |
| `node_type` | STRING(MAX) | Entity type | e.g., `"ComputeHost"`, `"LinuxNetwork"` |
| `hetgnn_embedding` | ARRAY<FLOAT64> | GNN model output | Vector embedding |
| `hetgnn_score` | FLOAT64 | GNN model output | Anomaly score |
| `anomaly_explanation` | JSON | GNN model output | Explanation object |
| `timestamp` | TIMESTAMP | Inference time | Primary key part 2 (DESC) |

---

## Core Domain — `coretopology-db`

### FiveGCore

**Purpose**: 5G core network orchestrator instance  
**Source CRD**: `FiveGCore`  
**Written by**: `operator/src/graph/core_tasks.py:sync_fivegcore()`  
**ID Pattern**: `fivegcore:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"fivegcore:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Core network name |
| `phase` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `mongodb_uri` | STRING(MAX) | `spec.mongodbUri` | MongoDB connection string |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### NetworkFunction

**Purpose**: Individual 5G network function (AMF, SMF, UPF, etc.)  
**Source CRD**: `NetworkFunction`  
**Written by**: `operator/src/graph/core_tasks.py:sync_network_function()`  
**ID Pattern**: `nf:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"nf:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | NF name |
| `nf_type` | STRING(MAX) | `spec.nf_type` | Values: `"AMF"`, `"SMF"`, `"UPF"`, `"NRF"`, `"AUSF"`, `"UDM"`, `"PCF"`, `"UDR"` |
| `fivegcore_id` | STRING(MAX) | `"fivegcore:" + owner_name` | Derived from owner references |
| `container_name` | STRING(MAX) | `spec.container_name` | Container/host name |
| `phase` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `ip_address` | STRING(MAX) | `status.ip_address` | Primary IP |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### NetworkFunctionMetrics

**Purpose**: Metrics from network functions  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `nf_id` | STRING(MAX) | `"nf:{name}"` | Primary key part 1 |
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 2 (DESC) |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 3 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Prometheus labels |

---

### NodeEmbedding (core)

**Purpose**: GNN embeddings for core network entities  
**Source**: GNN training pipeline  
**Written by**: `domains/transport/gnn/src/serve.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | `GENERATE_UUID()` | Auto-generated UUID |
| `node_id` | STRING(MAX) | Entity ID (e.g., `nf:amf-01`) | Primary key part 1 |
| `node_type` | STRING(MAX) | Entity type | e.g., `"NetworkFunction"` |
| `hetgnn_embedding` | ARRAY<FLOAT64> | GNN model output | Vector embedding |
| `hetgnn_score` | FLOAT64 | GNN model output | Anomaly score |
| `anomaly_explanation` | JSON | GNN model output | Explanation object |
| `timestamp` | TIMESTAMP | Inference time | Primary key part 2 (DESC) |

---

### KgLogEntryNode (core)

**Purpose**: Log entries for core domain  
**Source**: Log collector  
**Written by**: `services/logcollector/`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(17) | Generated | Primary key part 1 |
| `timestamp` | TIMESTAMP | Log timestamp | Primary key part 2 |
| `severity` | STRING(16) | Log severity | e.g., `"ERROR"`, `"INFO"` |
| `source` | STRING(MAX) | Log source | NF name or component |
| `message` | STRING(MAX) | Log message | Message text |
| `content` | STRING(MAX) | Full log entry | Complete log |
| `embedding` | ARRAY<FLOAT64> | Embedding model | Vector embedding |

---

## Radio Domain — `radiotopology-db`

### CellSite

**Purpose**: UERANSIM gNodeB cell site  
**Source CRD**: `CellSite`  
**Written by**: `operator/src/graph/radio_tasks.py:sync_cellsite()`  
**ID Pattern**: `cellsite:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"cellsite:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Cell site name |
| `container_name` | STRING(MAX) | `spec.container_name` | Container/host name |
| `gnb_id` | STRING(MAX) | `spec.gnb.gnb_id` | gNodeB ID |
| `nci` | STRING(MAX) | `spec.gnb.nci` | NR Cell Identity |
| `gnb_id_length` | INT64 | `spec.gnb.gnb_id_length` | gNodeB ID bit length |
| `mcc` | STRING(3) | `spec.gnb.mcc` | Mobile Country Code |
| `mnc` | STRING(3) | `spec.gnb.mnc` | Mobile Network Code |
| `tac` | INT64 | `spec.gnb.tac` | Tracking Area Code |
| `phase` | STRING(MAX) | `status.phase` | Lifecycle phase |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### UE

**Purpose**: Simulated user equipment  
**Source CRD**: `CellSite` (UEs defined in spec.ues array)  
**Written by**: `operator/src/graph/radio_tasks.py:_sync_ues()`  
**ID Pattern**: `ue:{cellsite-name}:{imsi}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"ue:" + cellsite_name + ":" + imsi` | Primary key |
| `cellsite_id` | STRING(MAX) | `"cellsite:" + cellsite_name` | Logical FK to CellSite |
| `imsi` | STRING(15) | `spec.ues[*].imsi` | International Mobile Subscriber Identity |
| `apn` | STRING(MAX) | `spec.ues[*].apn` | Access Point Name |
| `sst` | INT64 | `spec.ues[*].sst` | Slice/Service Type |
| `sd` | STRING(MAX) | `spec.ues[*].sd` | Slice Differentiator |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### CellSiteMetrics

**Purpose**: Metrics from cell sites and UEs  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `cellsite_id` | STRING(MAX) | `"cellsite:{name}"` | Primary key part 1 |
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 2 (DESC) |
| `imsi` | STRING(MAX) | UE IMSI or empty | Primary key part 3 |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 4 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Prometheus labels |

---

### NodeEmbedding (radio)

**Purpose**: GNN embeddings for radio entities  
**Source**: GNN training pipeline  
**Written by**: `domains/transport/gnn/src/serve.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | `GENERATE_UUID()` | Auto-generated UUID |
| `node_id` | STRING(MAX) | Entity ID (e.g., `cellsite:gnb-01`) | Primary key part 1 |
| `node_type` | STRING(MAX) | Entity type | e.g., `"CellSite"` |
| `hetgnn_embedding` | ARRAY<FLOAT64> | GNN model output | Vector embedding |
| `hetgnn_score` | FLOAT64 | GNN model output | Anomaly score |
| `anomaly_explanation` | JSON | GNN model output | Explanation object |
| `timestamp` | TIMESTAMP | Inference time | Primary key part 2 (DESC) |

---

### KgLogEntryNode (radio)

**Purpose**: Log entries for radio domain  
**Source**: Log collector  
**Written by**: `services/logcollector/`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(17) | Generated | Primary key part 1 |
| `timestamp` | TIMESTAMP | Log timestamp | Primary key part 2 |
| `severity` | STRING(16) | Log severity | e.g., `"ERROR"`, `"INFO"` |
| `source` | STRING(MAX) | Log source | Cell site or UE identifier |
| `message` | STRING(MAX) | Log message | Message text |
| `content` | STRING(MAX) | Full log entry | Complete log |
| `embedding` | ARRAY<FLOAT64> | Embedding model | Vector embedding |

---

## Service Domain — `servicetopology-db`

### TrafficFlow

**Purpose**: Traffic flow between compute hosts  
**Source CRD**: `TrafficTest`  
**Written by**: `operator/src/graph/service_tasks.py:sync_traffic_flow()`  
**ID Pattern**: `flow:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"flow:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Flow name |
| `src_device_id` | STRING(MAX) | `"host:" + spec.source_devices[0]` | Cross-domain logical FK to ComputeHost |
| `dst_device_id` | STRING(MAX) | `"host:" + spec.destination_devices[0]` | Cross-domain logical FK to ComputeHost |
| `phase` | STRING(MAX) | `status.phase` | Values: `"pending"`, `"running"`, `"completed"` |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### TrafficFlowSeries

**Purpose**: Time-series per-flow metrics  
**Source**: Metrics collector  
**Written by**: `services/metricscollector/src/main.py`

**Replaces `TrafficFlowMetrics`**, whose key `(flow_id, timestamp, node_name,
metric_name)` was one column short. Every flow leg is reported by both of its
traffic agents, and `node_name` is derived from `flow_id` — so it is identical
for both — which left `role` as the only separator. Without it in the key the
two ends collided and `insert_or_update` kept whichever landed second, throwing
away half of every poll. Several of these metrics are only meaningful from one
end (`throughput_sent_bps` is zero at the receiver, `packet_loss_pct` reads a
fabricated 50% at the sender), so the survivor was not a sample of the truth —
it was a coin flip between the reading and a fiction. `TrafficFlowMetrics`
still exists and still holds its history, but nothing writes it; do not read it.

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `flow_id` | STRING(MAX) | Flow name (without `flow:` prefix) | Primary key part 1 |
| `timestamp` | TIMESTAMP | Metric collection time | Primary key part 2 (DESC) |
| `node_name` | STRING(MAX) | Source device, parsed out of `flow_id` | Primary key part 3 |
| `metric_name` | STRING(MAX) | Metric name | Primary key part 4 |
| `role` | STRING(MAX) | Reporting end: `"source"` or `"destination"` | Primary key part 5 |
| `metric_type` | STRING(MAX) | Prometheus metric type | `"gauge"`, `"counter"`, etc. |
| `value` | FLOAT64 | Metric value | Numeric value |
| `labels` | JSON | Additional labels | Prometheus labels |

**Which end to read.** Query `role` explicitly; do not aggregate across it.

| Metric | Read from |
|--------|-----------|
| `traffic_agent_throughput_sent_bps`, `traffic_agent_bytes_sent_total` | `role='source'` |
| `traffic_agent_throughput_recv_bps`, `traffic_agent_bytes_received_total` | `role='destination'` |
| `traffic_agent_latency_ms`, `traffic_agent_jitter_ms`, `traffic_agent_packet_loss_pct` | `role='destination'` |

**Common metrics**:
- `traffic_agent_throughput_bps`: Instantaneous throughput
- `traffic_agent_bytes_sent_total`: Total bytes sent
- `traffic_agent_bytes_received_total`: Total bytes received
- `traffic_agent_active_sessions`: Concurrent sessions
- `traffic_agent_latency_ms`: Round-trip latency (UDP)
- `traffic_agent_packet_loss_pct`: Packet loss % (UDP)
- `traffic_agent_jitter_ms`: Inter-arrival jitter (UDP)

---

### NodeEmbedding (service)

**Purpose**: GNN embeddings for service entities  
**Source**: GNN training pipeline  
**Written by**: `domains/transport/gnn/src/serve.py`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | `GENERATE_UUID()` | Auto-generated UUID |
| `node_id` | STRING(MAX) | Entity ID (e.g., `flow:d1-to-hub-tcp`) | Primary key part 1 |
| `node_type` | STRING(MAX) | Entity type | e.g., `"TrafficFlow"` |
| `hetgnn_embedding` | ARRAY<FLOAT64> | GNN model output | Vector embedding |
| `hetgnn_score` | FLOAT64 | GNN model output | Anomaly score |
| `anomaly_explanation` | JSON | GNN model output | Explanation object |
| `timestamp` | TIMESTAMP | Inference time | Primary key part 2 (DESC) |

---

## Operations Domain — `agentops-db`

### FaultEvent

**Purpose**: Fault injection lifecycle tracking  
**Source CRD**: `NetworkFailure`  
**Written by**: `operator/src/graph/ops_tasks.py:sync_fault_event()`  
**ID Pattern**: `fault:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"fault:" + metadata.name` | Primary key |
| `name` | STRING(MAX) | `metadata.name` | Fault event name |
| `failure_type` | STRING(MAX) | `spec.failureType` | Type of failure injected |
| `injection_mode` | STRING(MAX) | `spec.injectionMode` | Injection method |
| `target_domain` | STRING(MAX) | `"transport"` | Always `"transport"` for NetworkFailure CRD |
| `target_id` | STRING(MAX) | Derived from target | Entity ID in target domain |
| `target_type` | STRING(MAX) | `"router"`, `"interface"`, etc. | Target entity type |
| `target_router` | STRING(MAX) | `spec.target.router` | Router name (transport-specific) |
| `target_interface` | STRING(MAX) | `spec.target.interface` | Interface name (transport-specific) |
| `target_vrf` | STRING(MAX) | `spec.target.vrf` | VRF name (transport-specific) |
| `phase` | STRING(MAX) | `status.phase` | Values: `"Injecting"`, `"Active"`, `"Restored"` |
| `injected_at` | STRING(MAX) | Timestamp when injected | Injection timestamp |
| `config` | JSON | Sanitized CRD body | Full configuration |
| `valid_start_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` | SCD Type 2 start |
| `valid_end_ts` | TIMESTAMP | `CURRENT_TIMESTAMP()` or NULL | NULL = current |

---

### AgentTrace

**Purpose**: Distributed trace spans from AI agents  
**Source**: AI agents (A2A protocol)  
**Written by**: Agent runtime

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | Generated UUID | Primary key |
| `event_type` | STRING(MAX) | Agent event type | Type of trace event |
| `trace_id` | STRING(MAX) | Distributed trace ID | Links related spans |
| `span_id` | STRING(MAX) | Unique span identifier | This span's ID |
| `parent_span_id` | STRING(MAX) | Parent span ID or NULL | For nested spans |
| `operation_name` | STRING(MAX) | Operation name | What the agent was doing |
| `timestamp` | TIMESTAMP | Event timestamp | When the event occurred |
| `details` | JSON | Additional details | Span metadata and context |

---

### NetworkDescriptor

**Purpose**: Named network configuration bundles  
**Source**: Supervisor UI or API  
**Written by**: Supervisor backend (not operator)  
**ID Pattern**: `network:{name}`

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(MAX) | `"network:" + name` | Primary key |
| `name` | STRING(MAX) | User-provided | Network configuration name |
| `description` | STRING(MAX) | User-provided | Human-readable description |
| `infrastructure` | JSON | `VyOSInfrastructure` CRD body | Infrastructure configuration |
| `underlay` | JSON | `VyOSUnderlay` CRD body | Underlay routing config |
| `vpns` | JSON | Array of `VyOSL3VPN` CRD bodies | VPN service configs |
| `traffic_tests` | JSON | Array of `TrafficTest` CRD bodies | Traffic test configs |
| `labels` | JSON | User-provided tags | Metadata labels |
| `created_at` | TIMESTAMP | Creation timestamp | When created |
| `updated_at` | TIMESTAMP | Last update timestamp | When last modified |

**Note**: This table does NOT use SCD Type 2. Updates overwrite the record.

---

### IntentRecords

**Purpose**: TMF921 Intent Management records for L3 VPN provisioning  
**Source**: Transport Intent API (`/intent` endpoints)  
**Written by**: `domains/transport/services/intentserver/src/clients/spanner_client.py`  
**ID Pattern**: UUID (generated)

| Column | Type | Source | Notes |
|--------|------|--------|-------|
| `id` | STRING(36) | Generated UUID | Primary key, intent identifier |
| `serviceId` | STRING(64) | `intentExpression.expectation[0].expectationObject.serviceId` | L3VPN service ID (for conflict detection) |
| `crName` | STRING(128) | Orchestrator response | VyOSL3VPN CR name (e.g., `red-mesh`) |
| `state` | STRING(16) | TMF921 state enum | Values: `"Acknowledged"`, `"InProgress"`, `"Completed"`, `"Rejected"`, `"Inactive"` |
| `intentEnvelope` | JSON | Full TMF921 intent body | Complete intent for audit trail |
| `createdAt` | TIMESTAMP | Creation timestamp | When intent was submitted |
| `updatedAt` | TIMESTAMP | Last update timestamp | When state last changed |

**Indexes**:
- `idx_service_id` — Fast conflict detection by serviceId
- `idx_state` — Query active intents (state != 'Inactive')
- `idx_created` — Chronological ordering (DESC)

**State Lifecycle**:
```
POST /intent → Acknowledged → InProgress → Completed/Rejected
DELETE /intent → Inactive
```

**State Mapping** (VyOSL3VPN CR phase → TMF921 state):
- `Waiting` or `Processing` → `InProgress`
- `Ready` → `Completed`
- `Error` → `Rejected`
- `(deleted)` → `Inactive`

**Note**: This table does NOT use SCD Type 2. The `state` field is updated in-place as the VyOSL3VPN CR progresses through its lifecycle. The `intentEnvelope` JSON preserves the original intent for audit purposes.

---

## Summary: Cross-Domain Logical References

These ID patterns enable cross-domain lookups without foreign keys:

| Relationship | Source DB | Target DB | Field | Target ID Pattern |
|--------------|-----------|-----------|-------|-------------------|
| NetworkFunction → ComputeHost | core | infra | `device_id` | `host:{container_name}` |
| CellSite → ComputeHost | radio | infra | `device_id` | `host:{container_name}` |
| CellSite_NF → NetworkFunction | radio | core | `nf_id` | `nf:{amf_name}` or `nf:{upf_name}` |
| TrafficFlow → ComputeHost (src) | service | infra | `src_device_id` | `host:{name}` |
| TrafficFlow → ComputeHost (dst) | service | infra | `dst_device_id` | `host:{name}` |
| LinuxNetwork → PhysicalRouter | infra | transport | `connected_routers` JSON | Array of router CR names |
| ComputeHost → LinuxNetwork | infra | infra | `linuxnetwork_id` | `linuxnetwork:{name}` |

---

## SCD Type 2 Pattern

All topology tables use this pattern:

```sql
-- Insert new version
INSERT INTO Table (..., valid_start_ts, valid_end_ts)
VALUES (..., CURRENT_TIMESTAMP(), NULL)

-- Close previous version
UPDATE Table
SET valid_end_ts = CURRENT_TIMESTAMP()
WHERE id = @id AND valid_end_ts IS NULL

-- Query current state
SELECT * FROM Table WHERE valid_end_ts IS NULL

-- Query state at time T
SELECT * FROM Table
WHERE valid_start_ts <= @T
  AND (valid_end_ts > @T OR valid_end_ts IS NULL)
```

---

## Related Documentation

- [CRD → Spanner Mapping](crd_spanner_mapping.md) — High-level CRD to table mapping
- [Spanner Readme](Readme.md) — Property graph architecture and query examples
- [Linux → Spanner Mapping](linux_spanner_mapping.md) — Linux artifact to Spanner mapping
