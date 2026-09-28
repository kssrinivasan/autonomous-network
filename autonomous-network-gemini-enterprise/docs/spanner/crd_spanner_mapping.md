# CRD → Spanner Mapping

This document describes how each Kubernetes Custom Resource Definition (CRD) managed by the
operator is mapped to Spanner tables, covering the full L3VPN stack from physical
infrastructure through to overlay VPN services and end-devices.

For the Linux bridge / veth mapping, see [`linux_spanner_mapping.md`](linux_spanner_mapping.md).

---

## Design Principle

The operator uses a layered CRD model that mirrors the layered network model. Each CRD layer
writes to a specific slice of the Spanner topology schema using **SCD Type 2** (Slowly Changing
Dimension Type 2) — closing the current row and inserting a new one whenever state changes.

| CRD | API Group | Network Role | Spanner Tables Written |
|---|---|---|---|
| `VyOSInfrastructure` | `google.dev/v1` | Parent composite resource — physical topology | `LogicalSubnet`, `PhysicalLink`, `Interface_Link` |
| `VyOSRouter` | `google.dev/v1` | Individual router node | `PhysicalRouter`, `PhysicalInterface`, `LogicalSubnet`, `Subnet_Association` |
| `VyOSUnderlay` | `google.dev/v1` | Core protocol layer (OSPF/MPLS/BGP) | *(indirect — patches `PhysicalRouter.config` via VyOSRouter update)* |
| `VyOSL3VPN` | `google.dev/v1` | VPN overlay service | `L3VPNService`, `VRF`, `BGPSession` |
| `Device` | `google.dev/v1` | End-host / CE device | `Device` |
| `LinuxNetwork` | `google.dev/v1` | Linux bridge (host network) | `LogicalSubnet`, `Subnet_Association`, `PhysicalLink`, `Interface_Link`, `NetworkMetrics` *(see linux_spanner_mapping.md)* |

All temporal tables use **SCD Type 2**. The comparison logic lives in
`graph/lifecycle_tasks.py` — a Spanner write is only issued when state actually changes.

---

## CRD Dependency Chain

Resources must be created in dependency order. The operator enforces this using
`kopf.TemporaryError` retries when a dependency is not yet `Ready`.

```
VyOSInfrastructure        ← creates child LinuxNetwork + VyOSRouter CRDs
      │
      ├─── LinuxNetwork    → LogicalSubnet (bridge monitoring)
      │
      └─── VyOSRouter      → PhysicalRouter, PhysicalInterface,
                             LogicalSubnet, Subnet_Association
                │
                ▼
         VyOSUnderlay       ← patches VyOSRouter spec (OSPF / MPLS / BGP)
                │
                ▼
          VyOSL3VPN         → L3VPNService, VRF, BGPSession
                │
                ▼
            Device          → Device (connects to a router via gateway IP)
```

---

## 1. VyOSInfrastructure → `LogicalSubnet` + `PhysicalLink` + `Interface_Link`

`VyOSInfrastructure` is the top-level composite resource. It declares the full physical
topology: all networks (subnets) and the routers that connect to them. Rather than writing
router rows directly, it **generates child `LinuxNetwork` and `VyOSRouter` CRDs**. The
infrastructure operator writes the inter-router link topology directly to Spanner via
`graph/lifecycle_tasks.py:sync_vyos_infrastructure()`.

### What it writes

Each entry in `spec.networks` produces a `LogicalSubnet` row. When two or more routers are
listed under `connected_routers`, a `PhysicalLink` and two `Interface_Link` rows are also
created to represent the point-to-point segment.

| `spec.networks` field | Spanner table | Column |
|---|---|---|
| `name` | `LogicalSubnet` | `id = "subnet:<name>"` |
| `subnet` | `LogicalSubnet` | `cidr` |
| `network_type` | `LogicalSubnet` | `network_type` |
| `description` | `LogicalSubnet` | `description` |
| *(network object)* | `LogicalSubnet` | `properties` (full JSON) |
| `name` (p2p, ≥2 routers) | `PhysicalLink` | `id = "link:<name>"` |
| `bandwidth` | `PhysicalLink` | `bandwidth` |
| `connected_routers[*]` | `PhysicalLink` | `properties.connected_routers` |
| router interface pair | `Interface_Link` | `(interface_id, link_id)` — two rows per link |

### ID Conventions

```
LogicalSubnet:   id = "subnet:<network-name>"
PhysicalLink:    id = "link:<network-name>"
Interface_Link:  (interface_id = "router:<router-name>:interface:<if-name>",
                  link_id     = "link:<network-name>")
```

### Example — p2p link between p1 and p2

```yaml
# VyOSInfrastructure spec.networks entry:
- name: p1-p2
  subnet: "172.16.30.0/24"
  vlan: 301
  network_type: "p2p"
  connected_routers:
    - router_name: "p1"
      interface: "eth1"
      ip_address: "172.16.30.1"
    - router_name: "p2"
      interface: "eth1"
      ip_address: "172.16.30.2"
```

Produces in Spanner:

```
LogicalSubnet:  id = "subnet:p1-p2"   cidr = "172.16.30.0/24"   network_type = "p2p"
PhysicalLink:   id = "link:p1-p2"     bandwidth = "unknown"      status = "UP"
Interface_Link: (router:p1:interface:eth1, link:p1-p2)
Interface_Link: (router:p2:interface:eth1, link:p1-p2)
```

### State Change Behaviour

```
VyOSInfrastructure created:   → insert LogicalSubnet + PhysicalLink rows
VyOSInfrastructure updated:   → compare properties; close + re-insert if changed
VyOSInfrastructure deleted:   → close all "subnet:*" and "link:*" rows
```

---

## 2. VyOSRouter → `PhysicalRouter` + `PhysicalInterface` + `LogicalSubnet` + `Subnet_Association`

Each `VyOSRouter` CRD is the canonical record for a single router node (P, PE, RR, or CE
role). The operator creates the VyOS container on the host VM and then syncs its topology
state to Spanner via `graph/lifecycle_tasks.py:sync_physical_router()`.

This function is called on every status transition (`Pending` → `Creating` → `Configuring` →
`Running` → `Failed`) and every 60-second monitor tick when state changes.

### What it writes

| CRD / Status field | Spanner table | Column |
|---|---|---|
| `metadata.name` | `PhysicalRouter` | `id = "router:<name>"`, `name` |
| `spec.vendor` | `PhysicalRouter` | `vendor` (default `"VyOS"`) |
| `spec.model` | `PhysicalRouter` | `model` (default `"Virtual"`) |
| `spec.location.city` | `PhysicalRouter` | `location_city` |
| `spec.location.latitude` | `PhysicalRouter` | `location_lat` |
| `spec.location.longitude` | `PhysicalRouter` | `location_lon` |
| `spec.role` | `PhysicalRouter` | `role` (e.g. `"PE"`, `"P"`, `"CE"`, `"RR"`) |
| `status.phase` | `PhysicalRouter` | `status` (`"Running"`, `"Failed"`, …) |
| *(sanitised body)* | `PhysicalRouter` | `config` (full CRD JSON) |
| `spec.interfaces[*].name` | `PhysicalInterface` | `id = "router:<name>:interface:<if-name>"`, `name` |
| `spec.interfaces[*].address` | `PhysicalInterface` | `ip_address` (CIDR stripped) |
| `PhysicalLink.bandwidth` of the connected link (set by `VyOSInfrastructure`) | `PhysicalInterface` | `speed` — looked up via `Interface_Link → PhysicalLink` at sync time; falls back to `spec.interfaces[*].speed` or `"1G"` if no link is found |
| `spec.interfaces[*].media_type` | `PhysicalInterface` | `media_type` |
| derived from `status.phase` or Ansible operstate | `PhysicalInterface` | `status` (`"UP"` / `"DOWN"` / `"ADMIN_DOWN"`) |
| `spec.interfaces[*].address` (CIDR) | `LogicalSubnet` | `id = "subnet:<cidr>"`, `cidr` |
| interface id + subnet id | `Subnet_Association` | `(entity_id, subnet_id, entity_type="Interface")` |

### ID Conventions

```
PhysicalRouter:   id = "router:<router-name>"
                      e.g. router:pe1   router:rr1   router:ce1-hub

PhysicalInterface: id = "router:<router-name>:interface:<if-name>"
                       e.g. router:pe1:interface:eth0
                            router:pe1:interface:eth1
                            router:pe1:interface:lo

LogicalSubnet:    id = "subnet:<cidr>"
                      e.g. subnet:172.16.90.2/24   subnet:10.0.0.7/32

Subnet_Association: (entity_id="router:pe1:interface:eth1",
                     subnet_id="subnet:172.16.90.0/24",
                     entity_type="Interface")
```

### Spanner Schema (relevant columns)

```sql
CREATE TABLE PhysicalRouter (
  id               STRING(MAX) NOT NULL,  -- "router:pe1"
  name             STRING(MAX) NOT NULL,  -- "pe1"
  vendor           STRING(MAX),           -- "VyOS"
  model            STRING(MAX),           -- "Virtual"
  location_city    STRING(MAX),           -- "Oxford"
  location_lat     FLOAT64,               -- 51.7520
  location_lon     FLOAT64,               -- -1.2577
  role             STRING(MAX),           -- "PE" | "P" | "CE" | "RR"
  status           STRING(MAX),           -- "Running" | "Failed" | "Pending"
  config           JSON,                  -- sanitised VyOSRouter CRD body
  valid_start_ts   TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts     TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)
```

### State Change Behaviour

```
VyOSRouter created (phase=Pending):    → insert PhysicalRouter (status=Pending)
VyOSRouter (phase=Running):            → close Pending row, insert Running row
                                         + insert PhysicalInterface rows
VyOSRouter (phase=Failed):             → close Running row, insert Failed row
VyOSRouter deleted:                    → close PhysicalRouter + all PhysicalInterface rows;
                                         delete Interface_Link + Subnet_Association edges
```

### Example Rows (hub-and-spoke lab)

```
id              | name    | role | status  | location_city | valid_start_ts      | valid_end_ts
router:pe1      | pe1     | PE   | Running | Oxford        | 2026-03-07 09:00:00 | NULL        ← current
router:pe2      | pe2     | PE   | Running | Cambridge     | 2026-03-07 09:00:00 | NULL
router:rr1      | rr1     | P    | Running | Birmingham    | 2026-03-07 09:00:00 | NULL
router:p1       | p1      | P    | Running | London        | 2026-03-07 09:00:00 | NULL
router:ce1-hub  | ce1-hub | CE   | Running | Nottingham    | 2026-03-07 09:00:00 | NULL
```

---

## 3. VyOSUnderlay → *(indirect — `PhysicalRouter.config`)*

`VyOSUnderlay` configures the **core routing protocols** (OSPF, MPLS/LDP, iBGP) across the
infrastructure. It does not write new Spanner rows directly. Instead, it uses
`utils/vyosnetwork.py:patch_vyos_router()` to merge protocol configuration into the
`VyOSRouter` spec, which then triggers the `VyOSRouter` update handler and ultimately a
`sync_physical_router()` call that updates `PhysicalRouter.config` in Spanner.

### Dependency

```
VyOSUnderlay.spec.infrastructureRef → must point to a Ready VyOSInfrastructure
```

### What gets persisted (indirectly)

| VyOSUnderlay spec field | Patched into VyOSRouter | Lands in Spanner |
|---|---|---|
| `routers[*].protocols.ospf` | `spec.protocols.ospf` | `PhysicalRouter.config` |
| `routers[*].protocols.bgp` | `spec.protocols.bgp` | `PhysicalRouter.config` |
| `routers[*].protocols.mpls` | `spec.protocols.mpls` | `PhysicalRouter.config` |
| `routers[*].traffic_policy` | `spec.traffic_policy` | `PhysicalRouter.config` |

### Notes

- The `VyOSRouter.config` JSON blob stored in Spanner contains the full post-patch spec,
  making underlay protocol state queryable via SQL.
- No new SCD rows are created in the topology tables. The existing `PhysicalRouter` row is
  closed and a new one opened (via `sync_physical_router`) only if the config diff changes.
- OSPF neighbour state and MPLS LDP state are surfaced in the 60-second monitor via Ansible
  facts and stored in `PhysicalRouter.config.protocols`.

---

## 4. VyOSL3VPN → `L3VPNService` + `VRF` + `BGPSession`

`VyOSL3VPN` is the overlay service layer. It patches VyOS PE routers with VRF definitions
and BGP neighbor configurations, and synchronises the full logical service topology to Spanner
via `graph/lifecycle_tasks.py:sync_l3vpn_service()`.

### Dependency

```
VyOSL3VPN.spec.underlayRef → must point to a Ready VyOSUnderlay
```

### What it writes

```
VyOSL3VPN.spec
  │
  ├── spec.services[*]
  │       └── L3VPNService          "vpn:<service-name>"
  │
  └── spec.routers[*]
          ├── VRF                   "vrf:<router-name>:<service-name>"
          └── BGPSession            "bgp:<router-name>:<service-name>:<peer-ip>"
```

### Field Mappings

#### `L3VPNService`

| `spec.services[*]` field | `L3VPNService` column |
|---|---|
| `name` | `id = "vpn:<name>"`, `name` |
| `type` | `service_type` (e.g. `"l3vpn"`) |
| `topology` | `topology` (`"hub"` \| `"spoke"` \| `"mesh"`) |
| *(static string)* | `customer_id` (set to `"cust:default"` — no Customer table) |
| derived from CRD status | `status` (`"Ready"` \| `"Unknown"`) |
| *(full service object)* | `config` (JSON) |

#### `VRF`

| `spec.routers[*]` field | `VRF` column |
|---|---|
| `"vrf:<router-name>:<service-name>"` | `id` |
| resolved via `PhysicalRouter` lookup | `router_id` |
| `"vpn:<service-name>"` | `vpn_id` |
| `"VRF-<service-name>"` | `name` |
| `vrfs[*].rd` | `rd` (Route Distinguisher e.g. `"10.50.50.1:1011"`) |
| derived from L3VPN status | `status` (`"Active"` \| `"Pending"`) |
| *(full router/vrf config)* | `config` (JSON with RT import/export, table ID) |

#### `BGPSession`

Neighbors are read from `spec.routers[*].bgp.vrfs[*].neighbors[*]`. The CRD field for the
neighbor address is `peer` (not `peer_ip`); the operator normalises this when writing to Spanner.

| `spec.routers[*].bgp.vrfs[*].neighbors[*]` field | `BGPSession` column |
|---|---|
| `"bgp:<router-name>:<service-name>:<peer>"` | `id` |
| `"vrf:<router-name>:<service-name>"` | `vrf_id` |
| *(set to 0 — not in CRD)* | `local_as` |
| `remote_as` | `remote_as` |
| `peer` | `peer_ip` |
| derived from L3VPN status | `status` (`"Established"` \| `"Idle"`) |
| *(full neighbor config)* | `config` (JSON) |

> **BGP peering edges** are not stored as a join table. The GNN derives `bgp_peer` edges at
> query time by matching `BGPSession.peer_ip` against `PhysicalInterface.ip_address`. This
> avoids write-time complexity of resolving both sides of a peering simultaneously, and works
> correctly in CE↔PE topologies where only the PE side has a `BGPSession` row.

### ID Conventions

```
L3VPNService:  id = "vpn:<service-name>"
                   e.g. vpn:BLUE_HUB   vpn:BLUE_SPOKE

VRF:           id = "vrf:<router-name>:<service-name>"
                   e.g. vrf:pe1:BLUE_SPOKE
                        vrf:pe2:BLUE_HUB

BGPSession:    id = "bgp:<router-name>:<service-name>:<peer-ip>"
                   e.g. bgp:pe1:BLUE_SPOKE:10.50.50.2
                        bgp:pe2:BLUE_HUB:10.80.80.2
```

### Spanner Schema (relevant columns)

```sql
CREATE TABLE L3VPNService (
  id           STRING(MAX) NOT NULL,  -- "vpn:BLUE_HUB"
  customer_id  STRING(MAX) NOT NULL,  -- "cust:default"
  name         STRING(MAX) NOT NULL,  -- "BLUE_HUB"
  service_type STRING(MAX),           -- "l3vpn"
  topology     STRING(MAX),           -- "hub" | "spoke" | "mesh"
  status       STRING(MAX),           -- "Ready" | "Unknown"
  config       JSON,                  -- full service config
  valid_start_ts TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts   TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)

CREATE TABLE VRF (
  id        STRING(MAX) NOT NULL,  -- "vrf:pe2:BLUE_HUB"
  router_id STRING(MAX) NOT NULL,  -- "router:pe2"
  vpn_id    STRING(MAX),           -- "vpn:BLUE_HUB"
  name      STRING(MAX) NOT NULL,  -- "VRF-BLUE_HUB"
  rd        STRING(MAX),           -- "10.80.80.1:1011"
  status    STRING(MAX),           -- "Active" | "Pending"
  config    JSON,                  -- rt_export, rt_import, table, interfaces
  valid_start_ts TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts   TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)

CREATE TABLE BGPSession (
  id        STRING(MAX) NOT NULL,  -- "bgp:pe2:BLUE_HUB:10.80.80.2"
  vrf_id    STRING(MAX) NOT NULL,  -- "vrf:pe2:BLUE_HUB"
  local_as  INT64,                 -- 65001
  remote_as INT64,                 -- 65035
  peer_ip   STRING(MAX),           -- "10.80.80.2"
  status    STRING(MAX),           -- "Established" | "Idle"
  config    JSON,
  valid_start_ts TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts   TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)
```

### State Change Behaviour

```
VyOSL3VPN created (status=Ready):    → insert L3VPNService,
                                        VRF rows per PE router,
                                        BGPSession rows per VRF neighbor
VyOSL3VPN updated:                   → close changed rows, insert new rows
VyOSL3VPN deleted:                   → close all vpn:*, vrf:* rows and associated
                                        BGPSession rows
```

### Example Rows (hub-and-spoke lab)

```
L3VPNService:
  id           | name       | topology | status | valid_end_ts
  vpn:BLUE_HUB | BLUE_HUB   | hub      | Ready  | NULL        ← current
  vpn:BLUE_SPOKE| BLUE_SPOKE| spoke    | Ready  | NULL

VRF:
  id                   | router_id   | vpn_id     | rd                | status
  vrf:pe2:BLUE_HUB     | router:pe2  | vpn:BLUE_HUB | 10.80.80.1:1011 | Active
  vrf:pe1:BLUE_SPOKE   | router:pe1  | vpn:BLUE_SPOKE| 10.50.50.1:1011| Active
  vrf:pe3:BLUE_SPOKE   | router:pe3  | vpn:BLUE_SPOKE| 10.60.60.1:1011| Active

BGPSession:
  id                                  | vrf_id             | peer_ip    | status
  bgp:pe2:BLUE_HUB:10.80.80.2        | vrf:pe2:BLUE_HUB   | 10.80.80.2 | Established
  bgp:pe1:BLUE_SPOKE:10.50.50.2      | vrf:pe1:BLUE_SPOKE | 10.50.50.2 | Established
  bgp:pe3:BLUE_SPOKE:10.60.60.2      | vrf:pe3:BLUE_SPOKE | 10.60.60.2 | Established
```

---

## 5. Device → `Device`

A `Device` CRD represents an end-host or CE device (VM, container, or test endpoint)
attached to a named `LinuxNetwork`. The operator creates the device using Ansible and links
it to its parent `PhysicalRouter` in Spanner by matching the device's `gateway` IP against
existing `PhysicalInterface.ip_address` records.

### What it writes

| `spec` / `metadata` field | `Device` column |
|---|---|
| `metadata.name` | `id = "device:<name>"`, `name` |
| resolved via gateway IP lookup | `interface_id` (e.g. `"router:ce1-hub:interface:eth2"`) |
| `spec.network_name` | `network_name` (e.g. `"lan-hub"`) |
| `spec.ip_address` | `ip_address` |
| `spec.gateway` | `gateway` |
| `spec.vlan` | `vlan` |
| `status.phase` | `status` |
| *(sanitised body)* | `config` (JSON) |

### ID Convention

```
id = "device:<metadata.name>"

Examples:
  device:devhub
  device:dev1
  device:dev2
```

### Gateway → Interface Resolution

The operator resolves `interface_id` by querying Spanner for the `PhysicalInterface` whose
`ip_address` matches the device's `spec.gateway`. This links the device directly to the
specific CE router port it is attached to — more precise than a router-level link — so that
graph traversals and the GNN can access interface-level metrics without an extra hop.

```sql
-- From graph/lifecycle_tasks.py:sync_device()
SELECT i.id
FROM PhysicalInterface i
WHERE i.ip_address = @gateway_ip
  AND i.valid_end_ts IS NULL
LIMIT 1
```

### Spanner Schema

```sql
CREATE TABLE Device (
  id           STRING(MAX) NOT NULL,  -- "device:devhub"
  name         STRING(MAX) NOT NULL,  -- "devhub"
  interface_id STRING(MAX),           -- "router:ce1-hub:interface:eth2"  (resolved from gateway)
  network_name STRING(MAX),           -- "lan-hub"
  ip_address   STRING(MAX),           -- "10.100.2.10"
  mgmt_ip      STRING(MAX),           -- management IP (optional)
  gateway      STRING(MAX),           -- "10.100.2.1"
  vlan         INT64,                 -- optional
  status       STRING(MAX),           -- "Ready" | "Failed"
  config       JSON,                  -- sanitised Device CRD body
  valid_start_ts TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts   TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)
```

### State Change Behaviour

```
Device created (phase=Ready):    → insert Device row (interface_id resolved from gateway)
Device deleted:                  → close Device row (valid_end_ts = NOW())
```

### Example Rows (hub-and-spoke lab)

```
id            | name    | interface_id                        | network_name | ip_address    | gateway
device:devhub | devhub  | router:ce1-hub:interface:eth2       | lan-hub      | 10.100.2.10   | 10.100.2.1
device:dev1   | dev1    | router:ce1-spoke:interface:eth1     | lan-spoke1   | 10.100.1.10   | 10.100.1.1
device:dev2   | dev2    | router:ce2-spoke:interface:eth1     | lan-spoke2   | 10.100.3.10   | 10.100.3.1
```

---

## 6. ID Convention Summary

| Entity | Format | Example |
|---|---|---|
| `PhysicalRouter` | `router:<name>` | `router:pe1` |
| `PhysicalInterface` | `router:<name>:interface:<if>` | `router:pe1:interface:eth1` |
| `PhysicalLink` (infra p2p) | `link:<network-name>` | `link:p1-pe1` |
| `PhysicalLink` (veth) | `link:veth:<router>:<if>` | `link:veth:edge-1:eth0` |
| `LogicalSubnet` (infra) | `subnet:<network-name>` | `subnet:p1-pe1` |
| `LogicalSubnet` (bridge) | `subnet:<bridge-name>` | `subnet:management` |
| `LogicalSubnet` (iface) | `subnet:<cidr>` | `subnet:172.16.90.0/24` |
| `L3VPNService` | `vpn:<service-name>` | `vpn:BLUE_HUB` |
| `VRF` | `vrf:<router>:<service>` | `vrf:pe2:BLUE_HUB` |
| `BGPSession` | `bgp:<router>:<service>:<peer-ip>` | `bgp:pe2:BLUE_HUB:10.80.80.2` |
| `Device` | `device:<name>` | `device:devhub` |

---

## 7. End-to-End Data Flow

```
kubectl apply -f l3vpn-hub-spoke.yaml
      │
      ├─ VyOSInfrastructure created
      │       ├── validate_network_topology()
      │       ├── generate_linux_networks()  → create LinuxNetwork CRDs (bridges)
      │       ├── generate_vyos_routers()    → create VyOSRouter CRDs
      │       └── sync_vyos_infrastructure() → Spanner:
      │               ├── LogicalSubnet  (one per network)
      │               ├── PhysicalLink   (one per p2p segment with ≥2 routers)
      │               └── Interface_Link (two rows per PhysicalLink)
      │
      ├─ LinuxNetwork created (per network)
      │       ├── Ansible: create_network.yaml  (creates Linux bridge on host)
      │       └── (monitoring every 60s → LogicalSubnet bridge state)
      │
      ├─ VyOSRouter created (per router node)
      │       ├── Ansible: router_management.yaml  (docker run vyos container)
      │       ├── Ansible: router_configuration.yaml (apply VyOS config)
      │       └── sync_physical_router() → Spanner:
      │               ├── PhysicalRouter   (status transitions: Pending→Creating→Running)
      │               ├── PhysicalInterface (one row per spec.interface)
      │               ├── LogicalSubnet    (one per interface CIDR)
      │               └── Subnet_Association (interface→subnet edge)
      │
      ├─ VyOSUnderlay created
      │       ├── check_infrastructure_ready()   (wait for VyOSInfrastructure=Ready)
      │       └── patch_vyos_router() × N        (merge OSPF/MPLS/BGP into VyOSRouter spec)
      │               └── VyOSRouter update handler fires → sync_physical_router()
      │                       └── PhysicalRouter.config updated (SCD write if changed)
      │
      ├─ VyOSL3VPN created
      │       ├── check_underlay_ready()          (wait for VyOSUnderlay=Ready)
      │       ├── patch_vyos_router() × N         (merge VRF/BGP into VyOSRouter spec)
      │       └── sync_l3vpn_service() → Spanner:
      │               ├── L3VPNService     (one per spec.services[*])
      │               ├── VRF              (one per router per service)
      │               └── BGPSession       (one per VRF neighbor)
      │
      └─ Device created
              ├── Ansible: device.yaml  (create container on host network)
              └── sync_device() → Spanner:
                      └── Device  (interface_id resolved from gateway → PhysicalInterface lookup)
```

---

## 8. Property Graph Traversal Examples

### Find full L3VPN service topology (VPN → VRF → Router)

```sql
GRAPH networkGraph
MATCH (vpn:L3VPNService)<-[:RealizesVPN]-(vrf:VRF)
      -[:LocatedOn]->(r:PhysicalRouter)
WHERE vpn.name = 'BLUE_HUB'
  AND vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
RETURN vpn.name, vpn.topology, vrf.rd, r.name AS router, r.location_city
```

### Find all BGP sessions for a VPN (SQL)

BGP peering relationships are derived at query time — there is no `PeersWith` graph edge.
Use a SQL join matching `BGPSession.peer_ip` against `PhysicalInterface.ip_address`:

```sql
SELECT
  b.id        AS session_id,
  b.peer_ip,
  b.status,
  r.name      AS router
FROM BGPSession b
JOIN VRF v ON b.vrf_id = v.id
JOIN PhysicalRouter r ON v.router_id = r.id
WHERE v.vpn_id = 'vpn:BLUE_SPOKE'
  AND b.valid_end_ts IS NULL
  AND v.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
ORDER BY r.name, b.peer_ip
```

### Find all devices reachable via a VPN (Device → Interface → Router → VRF → VPN)

```sql
GRAPH networkGraph
MATCH (d:Device)-[:ConnectedTo]->(i:PhysicalInterface)<-[:HasInterface]-(r:PhysicalRouter)
      <-[:LocatedOn]-(vrf:VRF)
      -[:RealizesVPN]->(vpn:L3VPNService)
WHERE vpn.name = 'BLUE_SPOKE'
  AND d.valid_end_ts IS NULL
  AND i.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND vpn.valid_end_ts IS NULL
RETURN d.name, d.ip_address, i.name AS ce_port, r.name AS ce_router, vrf.rd
```

### Find all physical links between two specific routers

```sql
GRAPH networkGraph
MATCH (r1:PhysicalRouter)-[:HasInterface]->(i1:PhysicalInterface)
      -[:ConnectsTo]->(l:PhysicalLink)
      -[:LinkedTo]->(i2:PhysicalInterface)<-[:HasInterface]-(r2:PhysicalRouter)
WHERE r1.name = 'p1' AND r2.name = 'pe1'
  AND r1.valid_end_ts IS NULL AND r2.valid_end_ts IS NULL
  AND l.valid_end_ts IS NULL
RETURN i1.name AS p1_if, l.bandwidth, i2.name AS pe1_if, l.status
```

### Find anomalous routers in the VPN with their GNN scores

```sql
GRAPH networkGraph
MATCH (r:PhysicalRouter)<-[:LocatedOn]-(vrf:VRF)
      -[:RealizesVPN]->(vpn:L3VPNService),
      (r)-[:RouterHasEmbedding]->(e:NodeEmbedding)
WHERE vpn.name = 'BLUE_HUB'
  AND e.hetgnn_score > 0.7
  AND vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
RETURN r.name, r.location_city, vrf.name, e.hetgnn_score, e.anomaly_explanation
ORDER BY e.hetgnn_score DESC
```

### SQL: Show VRF history on a PE router

```sql
SELECT name, rd, status, valid_start_ts, valid_end_ts
FROM VRF
WHERE router_id = 'router:pe1'
ORDER BY valid_start_ts DESC
```

### SQL: Find all currently idle BGP sessions

```sql
SELECT b.id, b.peer_ip, b.local_as, b.remote_as, b.valid_start_ts AS idle_since
FROM BGPSession b
WHERE b.status = 'Idle'
  AND b.valid_end_ts IS NULL
ORDER BY idle_since DESC
```

---

## 9. Related Files

| File | Role |
|---|---|
| `gcp/spanner.tpl.yaml` | DDL — all table definitions, edge views, property graph |
| `controller/src/graph/lifecycle_tasks.py` | Central Spanner write logic: `sync_physical_router()`, `sync_l3vpn_service()`, `sync_vyos_infrastructure()`, `sync_device()` |
| `controller/src/infrastructure/vyosinfrastructure/lifecycle.py` | `VyOSInfrastructure` operator: generates child LinuxNetwork + VyOSRouter CRDs |
| `controller/src/transport/vyosrouter/lifecycle.py` | `VyOSRouter` operator: Ansible provisioning + calls `sync_physical_router()` on every status change |
| `controller/src/transport/vyosrouter/lifecycle_tasks.py` | Ansible playbook runner for router create/configure/delete/status |
| `controller/src/transport/vyosunderlay/lifecycle.py` | `VyOSUnderlay` operator: patches VyOSRouter specs with OSPF/MPLS/BGP config |
| `controller/src/transport/vyosvpn/lifecycle.py` | `VyOSL3VPN` operator: patches VyOSRouter with VRF/BGP; triggers `sync_l3vpn_service()` |
| `controller/src/infrastructure/device/lifecycle.py` | `Device` operator: Ansible provisioning + calls `sync_device()` |
| `controller/src/infrastructure/device/lifecycle_tasks.py` | Ansible playbook runner for device create/delete |
| `controller/src/infrastructure/linuxnetwork/lifecycle.py` | `LinuxNetwork` operator: 60s monitor timer triggers bridge sync |
| `controller/src/utils/vyosnetwork.py` | Shared helpers: `patch_vyos_router()`, `generate_linux_networks()`, `generate_vyos_routers()` |
| `examples/controller/transport/default-network/` | Full worked example: VyOSInfrastructure + VyOSUnderlay + VyOSL3VPN + Device |
| `docs/network/l3vpn.md` | L3VPN concept model and YAML descriptor format |
| `docs/spanner/linux_spanner_mapping.md` | Linux bridge / veth pair mapping (LinuxNetwork CRD) |
