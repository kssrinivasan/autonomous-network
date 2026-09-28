# Linux Network Artefacts → Spanner Mapping

This document describes how Linux networking primitives (bridges, veth pairs) are mapped
to Spanner tables by the operator.

---

## Design Principle

Rather than adding new tables, the enhancement **reuses existing topology tables**.
Linux network artefacts are expressed as first-class network entities:

| Linux Artefact | Network Concept | Spanner Table |
|---|---|---|
| Linux bridge (`br-management`) | Logical subnet / IP segment | `LogicalSubnet` |
| VyOS container (`edge-1`) | Physical router | `PhysicalRouter` |
| Container interface (`eth0` inside VyOS) | Physical interface of the VyOS router | `PhysicalInterface` |
| Veth pair (host↔container) | Physical link | `PhysicalLink` |
| Host-side veth name | Reference label in `PhysicalLink.properties` | — *(not a Spanner entity)* |
| Bridge ↔ container interface attachment | Subnet–interface association | `Subnet_Association` |
| Container interface ↔ veth | Interface–link edge | `Interface_Link` |
| Bridge / veth statistics | Time-series metrics | `NetworkMetrics` |

All topology tables use **SCD Type 2** (Slowly Changing Dimension Type 2) — when state
changes, the current row is closed (`valid_end_ts = NOW()`) and a new row is inserted.
This gives a complete history of every state transition.

The **Linux host VM** (`networkvm`) is **not** modelled as a `PhysicalRouter`. The host side
of each veth pair is stored only as a reference label in `PhysicalLink.properties.host_veth`
so it can be identified from Spanner but does not pollute the topology graph.

---

## 1. Linux Bridge → `LogicalSubnet`

A Linux bridge is the physical realisation of a logical subnet on the host. It is stored in
`LogicalSubnet` with additional operational-state columns.

### ID Convention
```
id = "subnet:<network-name>"

Examples:
  subnet:management
  subnet:test-recovery
  subnet:underlay-1
```

### Schema

```sql
CREATE TABLE LogicalSubnet (
  id               STRING(MAX) NOT NULL,
  cidr             STRING(MAX) NOT NULL,   -- e.g. "10.0.0.0/24"
  network_type     STRING(MAX),            -- "management", "custom", etc.
  description      STRING(MAX),

  -- Bridge operational state (monitoring columns)
  operational_state  STRING(MAX),          -- "UP" | "DOWN" | "UNKNOWN"
  mtu                INT64,                -- Bridge MTU (default 1500)
  mac_address        STRING(MAX),          -- Bridge MAC  e.g. "aa:bb:cc:dd:ee:ff"
  bridge_ip          STRING(MAX),          -- Bridge IP if assigned
  host_device_name   STRING(MAX),          -- Linux device name e.g. "management"

  -- Structural config (drives SCD change detection)
  properties         JSON,                 -- {network_type, bandwidth, gateway, vlan}

  -- SCD Type 2 temporal columns
  valid_start_ts   TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  valid_end_ts     TIMESTAMP OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id, valid_start_ts DESC)
```

> **Note:** Per-cycle bridge traffic counters (`rx_packets`, `tx_bytes`, etc.) are
> intentionally **not** stored in `properties`. They go to `NetworkMetrics` (see section 6).
> Keeping counters out of `properties` means a new SCD row is only written when structural
> state changes (bridge goes UP/DOWN, MTU changes, etc.), not on every 60-second poll.

### Data Source

The bridge state is collected every 60 seconds by the `monitor_linuxnetwork` kopf timer via
an Ansible playbook (`detailed_status_network.yaml`) that reads directly from the kernel:

```bash
/sys/class/net/<bridge>/operstate   → operational_state
/sys/class/net/<bridge>/mtu         → mtu
/sys/class/net/<bridge>/address     → mac_address
ip -4 addr show <bridge>            → bridge_ip
/sys/class/net/<bridge>/statistics/ → NetworkMetrics (separate write)
```

### State Change Behaviour

```
Monitor tick N:   operational_state = UP  → no Spanner write (unchanged)
Monitor tick N+4: bridge goes DOWN        → close current row, insert new row with DOWN
Monitor tick N+8: bridge comes back UP    → close DOWN row, insert new row with UP
```

Only structural state changes generate Spanner writes. The comparison is done in
`graph/lifecycle_tasks.py:sync_host_network_bridge()`.

### Example Rows

```
id                   | cidr          | operational_state | mtu  | valid_start_ts      | valid_end_ts
subnet:management    | 10.0.0.0/24   | UP                | 1500 | 2026-03-07 09:00:00 | NULL       ← current
subnet:management    | 10.0.0.0/24   | DOWN              | 1500 | 2026-03-06 14:32:00 | 2026-03-06 14:45:00
subnet:management    | 10.0.0.0/24   | UP                | 1500 | 2026-03-06 08:00:00 | 2026-03-06 14:32:00
```

---

## 2. VyOS Container → `PhysicalRouter`

Each VyOS router container is the canonical `PhysicalRouter` entry. The host system
(`networkvm`) is **not** represented as a `PhysicalRouter`.

### ID Convention
```
id = "router:<router-name>"

Examples:
  router:edge-1
  router:rr-1
```

The `PhysicalRouter` row is created and maintained by `vyosrouter/lifecycle.py` via
`graph/lifecycle_tasks.py:sync_physical_router()` whenever the `VyOSRouter` CRD is
created or its status changes.

---

## 3. Host-side Veth — Reference Label Only (not a Spanner entity)

The host side of each veth pair (`edge-1-eth0` on `networkvm`) is **not stored as a
`PhysicalInterface` row** in Spanner. Creating a host-side `PhysicalInterface` would require
a corresponding `PhysicalRouter` for the host, which would appear as a spurious node in
the topology graph.

Instead:
- The host-side veth device name is stored as the `host_veth` field in
  `PhysicalLink.properties` (see section 4).
- Bridge-to-container connectivity is modelled via `Subnet_Association` (see section 5).

### Container-side interface (IS a Spanner entity)

The **container-side** interface (e.g. `eth0` inside the VyOS container) **is** stored as a
`PhysicalInterface` row, owned by the VyOS container `PhysicalRouter`. These rows are created
by `sync_physical_router()` from the `VyOSRouter` CRD's `spec.interfaces` list.

```
PhysicalInterface: id = "router:edge-1:interface:eth0"
                       router_id = "router:edge-1"
                       name      = "eth0"
                       status    = "UP" | "DOWN" | "ADMIN_DOWN"
```

### Veth name resolution

Veth names on the host are formed as `{router_name[:8]}-{interface_name}`. Because the
router name is truncated to 8 characters, the operator resolves the full container interface
ID by querying Spanner:

```python
# From graph/lifecycle_tasks.py:_resolve_container_interface_id()
SELECT id FROM PhysicalInterface
WHERE name = @iface_name
  AND router_id LIKE @router_pattern   -- 'router:edge-1%'
  AND valid_end_ts IS NULL
LIMIT 1
```

If no match is found (e.g. the VyOS container hasn't registered yet), a fallback ID is used
and the `Interface_Link` is written with a placeholder — it heals automatically once the
router CRD is created and `sync_physical_router()` runs.

---

## 4. Veth Pair → `PhysicalLink`

The veth pair (the bidirectional pipe between host and container) is stored as a
`PhysicalLink`. This captures the link's state and bandwidth limit.

### ID Convention
```
id = "link:veth:<router-prefix>:<interface-name>"

Example:
  link:veth:edge-1:eth0
```

### Key Field Mappings

| `PhysicalLink` field | Value |
|---|---|
| `name` | `"veth: host:veth:edge-1-eth0 ↔ router:edge-1:interface:eth0"` |
| `bandwidth` | TC rate limit e.g. `"1gbit"` or `"N/A"` |
| `status` | `"UP"` \| `"DOWN"` (host-side operstate, uppercase) |
| `properties` | `{host_veth, container_interface}` — structural fields only |

### `properties` JSON Structure

```json
{
  "host_veth":           "host:veth:edge-1-eth0",
  "container_interface": "router:edge-1:interface:eth0"
}
```

> **No counters in `properties`.** Per-cycle rx/tx packet and byte counters are written to
> `NetworkMetrics` (section 6) instead. This means a new SCD row is only created when the
> link state or bandwidth changes — not on every 60-second monitoring poll.

### State Change Behaviour

```
Monitor tick N:   status = UP, bandwidth = 1gbit → no Spanner write (unchanged)
Monitor tick N+4: link goes DOWN                  → close current row, insert new row with DOWN
Monitor tick N+8: link comes back UP              → close DOWN row,  insert new row with UP
```

---

## 5. Bridge ↔ Container Interface — `Subnet_Association` + `Interface_Link`

Two edges connect the container interface into the graph:

### 5a. `Subnet_Association` — bridge connectivity

A `Subnet_Association` row links the container-side `PhysicalInterface` to the bridge
`LogicalSubnet`. This is how the graph answers "which interfaces are on bridge X?".

```
Subnet_Association:
  entity_id   = "router:edge-1:interface:eth0"   (container PhysicalInterface)
  subnet_id   = "subnet:management"              (bridge LogicalSubnet)
  entity_type = "Interface"
```

This row is written by `_sync_container_bridge_association()` (called from `sync_veth_pairs()`).
The bridge `LogicalSubnet` must already exist in Spanner (written by `sync_host_network_bridge()`)
before this association is created.

### 5b. `Interface_Link` — container interface ↔ veth PhysicalLink

A single `Interface_Link` row connects the container-side interface to the veth
`PhysicalLink`. The host side of the veth is NOT a Spanner entity and therefore does
**not** have a corresponding `Interface_Link` row.

```
(interface_id,                      link_id,               valid_start_ts, valid_end_ts)
router:edge-1:interface:eth0        link:veth:edge-1:eth0  <ts>            NULL
```

Because there is only one `Interface_Link` endpoint per veth `PhysicalLink`, graph
traversals that follow `ConnectsTo → PhysicalLink → LinkedTo` will return the container
interface as both source and destination (a self-loop), which the topology query filters
out via `router_id != remote_router_id`. Veth links therefore do not appear as
router-to-router connections in the topology view — which is the correct behaviour.

### Graph edges produced

| Edge | Source → Target |
|---|---|
| `AssociatedWith_Edge` | `PhysicalInterface (container eth0)` → `LogicalSubnet (bridge)` |
| `ConnectsTo_Edge` | `PhysicalInterface (container eth0)` → `PhysicalLink (veth)` |
| `LinkedTo_Edge` | `PhysicalLink (veth)` → `PhysicalInterface (container eth0)` |

---

## 6. Bridge / Veth Metrics → `NetworkMetrics`

Per-cycle traffic counters are stored in `NetworkMetrics` as time-series data. The monitoring
code uses a different set of columns from the VyOS Prometheus `metricscollector` — both
writers share the same table via nullable columns.

### ID Convention
```
id = GENERATE_UUID()  -- auto-generated; callers do not supply this value
```

### Bridge / Veth Monitoring Columns (operator)

| Column | Value |
|---|---|
| `kind` | `"LogicalSubnet"` (bridge) or `"PhysicalLink"` (veth) |
| `node_name` | e.g. `"subnet:management"` or `"link:veth:edge-1:eth0"` |
| `metric_name` | e.g. `"rx_packets"`, `"tx_bytes"`, `"rx_errors"` |
| `metric_type` | `"counter"` |
| `value` | Metric value (FLOAT64) |
| `labels` | JSON: `{entity_id: "...", entity_type: "..."}` |
| `timestamp` | Poll time |

### VyOS Prometheus Columns (metricscollector)

| Column | Value |
|---|---|
| `node_name` | Router name e.g. `"edge-1"` |
| `metric_name` | e.g. `"node_network_receive_bytes_total"` |
| `metric_type` | `"gauge"` \| `"counter"` |
| `kind` | `"SYSTEM"` \| `"ROUTING"` |
| `value` | FLOAT64 |
| `interface` | NIC name |

The `id` column has `DEFAULT (GENERATE_UUID())` so neither writer needs to supply it.

---

## End-to-End Data Flow

```
Every 60 seconds (kopf timer):

  NetworkVM (Linux host)
      │
      ├─ Ansible playbook: detailed_status_network.yaml
      │       ├── reads /sys/class/net/<bridge>/operstate
      │       ├── reads /sys/class/net/<veth>/operstate
      │       └── reads TC bandwidth limits & counters
      │
      └─ Python: graph/lifecycle_tasks.py
              │
              ├── sync_host_network_bridge()
              │       └── LogicalSubnet (SCD Type 2 — structural state only)
              │
              ├── sync_veth_pairs()
              │       │   (veth name encodes the router: edge-1-eth0 → router:edge-1)
              │       ├── _resolve_container_interface_id()
              │       │       └── Spanner lookup → "router:edge-1:interface:eth0"
              │       ├── _sync_veth_link()
              │       │       ├── PhysicalLink "link:veth:edge-1:eth0"
              │       │       │       properties: {host_veth, container_interface}
              │       │       └── Interface_Link (1 row — container side only)
              │       ├── _sync_container_bridge_association()
              │       │       └── Subnet_Association
              │       │               container interface → bridge LogicalSubnet
              │       └── sync_network_metrics()
              │               └── NetworkMetrics (veth counters — time-series)
              │
              └── sync_network_metrics()  [bridge]
                      └── NetworkMetrics (bridge stats blob — time-series)
```

---

## Property Graph Traversal

The data model enables GQL queries that traverse from a bridge subnet directly to VyOS
routers via the `AssociatedWith_Edge` (driven by `Subnet_Association`).

```sql
-- Find all VyOS routers connected to a specific bridge
GRAPH networkGraph
MATCH (s:LogicalSubnet {id: 'subnet:management'})
   <-[:AssociatedWith]- (i:PhysicalInterface)
   <-[:HasInterface]-   (r:PhysicalRouter)
WHERE s.valid_end_ts IS NULL
  AND i.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
RETURN r.name, r.status, i.name AS router_interface
```

```sql
-- Find all VyOS routers on a bridge, including veth link state
GRAPH networkGraph
MATCH (s:LogicalSubnet {id: 'subnet:management'})
   <-[:AssociatedWith]- (i:PhysicalInterface)
   <-[:HasInterface]-   (r:PhysicalRouter)
OPTIONAL MATCH (i) -[:ConnectsTo]-> (l:PhysicalLink)
WHERE s.valid_end_ts IS NULL
  AND i.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
RETURN r.name, r.status, i.name AS router_interface,
       l.status AS link_status, l.bandwidth
```

```sql
-- Show history of a bridge going UP and DOWN
SELECT operational_state, valid_start_ts, valid_end_ts
FROM LogicalSubnet
WHERE id = 'subnet:management'
ORDER BY valid_start_ts DESC
```

```sql
-- Find all bridges currently DOWN
SELECT id, host_device_name, valid_start_ts AS down_since
FROM LogicalSubnet
WHERE operational_state = 'DOWN'
  AND valid_end_ts IS NULL
ORDER BY down_since DESC
```

```sql
-- Show veth link history (state changes only — counters excluded from SCD)
SELECT name, status, bandwidth, valid_start_ts, valid_end_ts
FROM PhysicalLink
WHERE id = 'link:veth:edge-1:eth0'
ORDER BY valid_start_ts DESC
```

---

## Related Files

| File | Role |
|---|---|
| `gcp/spanner.tpl.yaml` | DDL — table definitions with all monitoring columns |
| `controller/src/infrastructure/linuxnetwork/lifecycle.py` | kopf timer that drives monitoring every 60s |
| `controller/src/infrastructure/linuxnetwork/lifecycle_tasks.py` | Ansible execution + result parsing |
| `controller/src/infrastructure/linuxnetwork/playbooks/` (`create_network.yaml`, `delete_network.yaml`) | Collects bridge/veth state from kernel |
| `controller/src/graph/lifecycle_tasks.py` | `sync_host_network_bridge()`, `sync_veth_pairs()`, `_sync_veth_link()`, `_sync_container_bridge_association()` — write to Spanner |
