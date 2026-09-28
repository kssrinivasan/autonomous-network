# Transport Domain Topology API (GQL)

Graph Query Language (GQL) API for traversing transport network topology using Spanner property graphs.

---

## Overview

**Purpose:** Execute graph queries to discover paths, relationships, and topology patterns in the transport network.

**Spanner Property Graph:** `transportGraph`

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/transport`

**Key Capabilities:**
- Path discovery between routers (multi-hop traversal)
- Neighbor discovery (find connected routers, interfaces, links)
- Service topology traversal (VPN → VRF → Router)
- BGP session relationship queries
- Cross-domain logical references

---

## Property Graph Schema

### Node Types

**PhysicalRouter**
- Properties: `id`, `name`, `vendor`, `model`, `role`, `location_city`, `location_lat`, `location_lon`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `PhysicalRouter`

**PhysicalInterface**
- Properties: `id`, `name`, `router_id`, `ip_address`, `speed`, `media_type`, `mac_address`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `PhysicalInterface`

**PhysicalLink**
- Properties: `id`, `name`, `bandwidth`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `PhysicalLink`

**LogicalSubnet**
- Properties: `id`, `cidr`, `network_type`, `description`, `valid_start_ts`, `valid_end_ts`
- Labels: `LogicalSubnet`

**L3VPNService**
- Properties: `id`, `name`, `customer_id`, `service_type`, `topology`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `L3VPNService`

**VRF**
- Properties: `id`, `name`, `router_id`, `vpn_id`, `rd`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `VRF`

**BGPSession**
- Properties: `id`, `vrf_id`, `local_as`, `remote_as`, `peer_ip`, `status`, `valid_start_ts`, `valid_end_ts`
- Labels: `BGPSession`

### Edge Types

| Edge Label | Source Node | Target Node | Description |
|------------|-------------|-------------|-------------|
| `HasInterface` | PhysicalRouter | PhysicalInterface | Router owns interface |
| `ConnectsTo` | PhysicalInterface | PhysicalLink | Interface connected to link |
| `LinkedTo` | PhysicalLink | PhysicalInterface | Link connected to interface (reverse) |
| `BelongsToSubnet` | PhysicalInterface | LogicalSubnet | Interface assigned to subnet |
| `SubnetOnLink` | PhysicalLink | LogicalSubnet | Subnet assigned to link |
| `RealizesVPN` | VRF | L3VPNService | VRF implements VPN service |
| `LocatedOn` | VRF | PhysicalRouter | VRF resides on router |
| `BelongsToVRF` | BGPSession | VRF | BGP session scoped to VRF |

---

## GQL Endpoint

**URL:** `POST /tmf-api/resourceInventoryManagement/v4/transport/graph/query`

**Request Body:**
```json
{
  "gql": "GRAPH transportGraph MATCH ... RETURN ...",
  "parameters": {
    "router_name": "pe1"
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "column1": "value1",
      "column2": "value2"
    }
  ],
  "totalCount": 10
}
```

---

## Common Query Patterns

### 1. Find Router Neighbors

**Use Case:** Discover all routers directly connected to a given router

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (r1:PhysicalRouter {name: $router_name})-[:HasInterface]->(i1:PhysicalInterface)
      -[:ConnectsTo]->(link:PhysicalLink)
      -[:LinkedTo]->(i2:PhysicalInterface)<-[:HasInterface]-(r2:PhysicalRouter)
WHERE r1.valid_end_ts IS NULL
  AND i1.valid_end_ts IS NULL
  AND link.valid_end_ts IS NULL
  AND i2.valid_end_ts IS NULL
  AND r2.valid_end_ts IS NULL
RETURN DISTINCT r2.name AS neighbor_router,
       i1.name AS local_interface,
       i2.name AS remote_interface,
       link.bandwidth AS link_bandwidth
```

**Parameters:**
```json
{
  "router_name": "pe1"
}
```

**Example Response:**
```json
{
  "results": [
    {
      "neighbor_router": "pe2",
      "local_interface": "eth1",
      "remote_interface": "eth1",
      "link_bandwidth": "10G"
    },
    {
      "neighbor_router": "p1",
      "local_interface": "eth2",
      "remote_interface": "eth3",
      "link_bandwidth": "10G"
    }
  ],
  "totalCount": 2
}
```

---

### 2. Trace Path Between Routers

**Use Case:** Find physical path between two routers (2-hop example)

**GQL Query:**
```gql
GRAPH transportGraph
MATCH path = (src:PhysicalRouter {name: $src_router})
             -[:HasInterface]->(:PhysicalInterface)
             -[:ConnectsTo]->(:PhysicalLink)
             -[:LinkedTo]->(:PhysicalInterface)
             <-[:HasInterface]-(mid:PhysicalRouter)
             -[:HasInterface]->(:PhysicalInterface)
             -[:ConnectsTo]->(:PhysicalLink)
             -[:LinkedTo]->(:PhysicalInterface)
             <-[:HasInterface]-(dst:PhysicalRouter {name: $dst_router})
WHERE ALL(node IN nodes(path) WHERE node.valid_end_ts IS NULL)
RETURN mid.name AS intermediate_router,
       mid.location_city AS intermediate_city
```

**Parameters:**
```json
{
  "src_router": "pe1",
  "dst_router": "pe2"
}
```

---

### 3. VPN Service Topology

**Use Case:** Find all routers participating in a VPN service

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (vpn:L3VPNService {name: $vpn_name})<-[:RealizesVPN]-(vrf:VRF)
      -[:LocatedOn]->(router:PhysicalRouter)
WHERE vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND router.valid_end_ts IS NULL
RETURN vpn.name AS vpn_service,
       vpn.topology AS vpn_topology,
       vrf.rd AS route_distinguisher,
       router.name AS router_name,
       router.location_city AS router_city
ORDER BY router.name
```

**Parameters:**
```json
{
  "vpn_name": "BLUE_HUB"
}
```

**Example Response:**
```json
{
  "results": [
    {
      "vpn_service": "BLUE_HUB",
      "vpn_topology": "hub",
      "route_distinguisher": "10.0.0.7:2000",
      "router_name": "pe1",
      "router_city": "London"
    },
    {
      "vpn_service": "BLUE_HUB",
      "vpn_topology": "hub",
      "route_distinguisher": "10.0.0.8:2001",
      "router_name": "pe2",
      "router_city": "Manchester"
    }
  ],
  "totalCount": 2
}
```

---

### 4. BGP Sessions for VRF

**Use Case:** Find all BGP sessions associated with a VRF

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (vrf:VRF {id: $vrf_id})<-[:BelongsToVRF]-(bgp:BGPSession)
WHERE vrf.valid_end_ts IS NULL
  AND bgp.valid_end_ts IS NULL
RETURN bgp.peer_ip AS bgp_peer,
       bgp.remote_as AS remote_as,
       bgp.status AS bgp_status
```

**Parameters:**
```json
{
  "vrf_id": "vrf:pe1:BLUE_HUB"
}
```

---

### 5. Interfaces on a Link

**Use Case:** Find all interfaces connected to a physical link

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (link:PhysicalLink {name: $link_name})-[:LinkedTo]->(iface:PhysicalInterface)
      <-[:HasInterface]-(router:PhysicalRouter)
WHERE link.valid_end_ts IS NULL
  AND iface.valid_end_ts IS NULL
  AND router.valid_end_ts IS NULL
RETURN router.name AS router_name,
       iface.name AS interface_name,
       iface.ip_address AS ip_address,
       iface.status AS interface_status
```

**Parameters:**
```json
{
  "link_name": "network1"
}
```

---

### 6. Subnet Assignments

**Use Case:** Find all interfaces and links in a specific subnet

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (subnet:LogicalSubnet {cidr: $subnet_cidr})
      <-[:BelongsToSubnet]-(iface:PhysicalInterface)
      <-[:HasInterface]-(router:PhysicalRouter)
WHERE subnet.valid_end_ts IS NULL
  AND iface.valid_end_ts IS NULL
  AND router.valid_end_ts IS NULL
RETURN router.name AS router_name,
       iface.name AS interface_name,
       iface.ip_address AS ip_address
```

**Parameters:**
```json
{
  "subnet_cidr": "10.0.0.0/24"
}
```

---

### 7. Router Interface Details

**Use Case:** Get all interfaces on a router with their connectivity

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (router:PhysicalRouter {name: $router_name})-[:HasInterface]->(iface:PhysicalInterface)
OPTIONAL MATCH (iface)-[:ConnectsTo]->(link:PhysicalLink)
WHERE router.valid_end_ts IS NULL
  AND iface.valid_end_ts IS NULL
  AND (link IS NULL OR link.valid_end_ts IS NULL)
RETURN iface.name AS interface_name,
       iface.ip_address AS ip_address,
       iface.speed AS speed,
       iface.status AS status,
       link.name AS connected_link
ORDER BY iface.name
```

**Parameters:**
```json
{
  "router_name": "pe1"
}
```

---

### 8. VPN Services by Topology Type

**Use Case:** Find all VPN services of a specific topology type with their routers

**GQL Query:**
```gql
GRAPH transportGraph
MATCH (vpn:L3VPNService {topology: $topology_type})<-[:RealizesVPN]-(vrf:VRF)
      -[:LocatedOn]->(router:PhysicalRouter)
WHERE vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND router.valid_end_ts IS NULL
RETURN vpn.name AS vpn_name,
       COUNT(DISTINCT router) AS router_count,
       ARRAY_AGG(DISTINCT router.name ORDER BY router.name) AS routers
GROUP BY vpn.name
```

**Parameters:**
```json
{
  "topology_type": "hub"
}
```

---

## Performance Considerations

### Graph Traversal Limits

| Parameter | Limit | Reason |
|-----------|-------|--------|
| Max path length | 10 hops | Prevent infinite loops |
| Max result set | 1000 rows | Pagination required for larger sets |
| Query timeout | 30 seconds | Spanner query timeout |
| Max parameter size | 1 MB | Spanner parameter limit |

### Optimization Guidelines

1. **Always filter on `valid_end_ts IS NULL`** - Query only current state for performance
2. **Use indexed properties** - `name`, `id`, `role`, `status` are indexed
3. **Limit traversal depth** - Use explicit hop counts instead of unbounded `*` patterns
4. **OPTIONAL MATCH carefully** - Can significantly increase result set size
5. **Use DISTINCT** - Eliminate duplicate paths when traversing bidirectional edges

### Temporal Queries

**Query historical graph state:**
```gql
GRAPH transportGraph
MATCH (r:PhysicalRouter)-[:HasInterface]->(i:PhysicalInterface)
WHERE r.valid_start_ts <= $timestamp
  AND (r.valid_end_ts > $timestamp OR r.valid_end_ts IS NULL)
  AND i.valid_start_ts <= $timestamp
  AND (i.valid_end_ts > $timestamp OR i.valid_end_ts IS NULL)
RETURN r.name, i.name
```

**Note:** Temporal queries are more expensive. Use sparingly.

---

## Integration with Spanner

### Spanner GQL Execution

The API translates GQL queries to Spanner Graph Query Language and executes them against the `transportGraph` property graph.

**Internal Flow:**
1. Parse GQL query from request body
2. Validate node and edge types against schema
3. Inject temporal filters (valid_end_ts IS NULL) if not present
4. Execute against Spanner property graph
5. Transform results to JSON response

**Property Graph Definition (Spanner DDL):**
```sql
CREATE PROPERTY GRAPH transportGraph
  NODE TABLES (PhysicalRouter, PhysicalInterface, PhysicalLink, LogicalSubnet, L3VPNService, VRF, BGPSession)
  EDGE TABLES (
    Interface_Link AS ConnectsTo
      SOURCE KEY (interface_id) REFERENCES PhysicalInterface (id)
      DESTINATION KEY (link_id) REFERENCES PhysicalLink (id),
    ...
  )
```

### Edge Table Queries

Some edges come from dedicated edge tables in Spanner:

**Interface_Link (ConnectsTo edge):**
```sql
SELECT interface_id, link_id
FROM Interface_Link
WHERE valid_end_ts IS NULL
```

**Subnet_Association (BelongsToSubnet edge):**
```sql
SELECT entity_id AS interface_id, subnet_id
FROM Subnet_Association
WHERE entity_type = 'Interface'
  AND valid_end_ts IS NULL
```

---

## Error Codes

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 400 | `INVALID_GQL_SYNTAX` | Malformed GQL query |
| 400 | `UNKNOWN_NODE_TYPE` | Referenced node type not in schema |
| 400 | `UNKNOWN_EDGE_TYPE` | Referenced edge type not in schema |
| 400 | `MISSING_PARAMETERS` | Required query parameters not provided |
| 408 | `QUERY_TIMEOUT` | Query exceeded 30 second timeout |
| 413 | `RESULT_TOO_LARGE` | Result set exceeds 1000 row limit |
| 500 | `SPANNER_ERROR` | Graph query execution failed |

**Error Response Schema:**
```json
{
  "error": {
    "code": "INVALID_GQL_SYNTAX",
    "message": "Syntax error at line 2: unexpected token 'WHRE'",
    "status": 400
  }
}
```

---

## Complete Example

**Scenario:** Find all routers in a VPN service and their BGP peers

**Request:**
```bash
POST /tmf-api/resourceInventoryManagement/v4/transport/graph/query
Content-Type: application/json

{
  "gql": "GRAPH transportGraph MATCH (vpn:L3VPNService {name: $vpn_name})<-[:RealizesVPN]-(vrf:VRF)-[:LocatedOn]->(router:PhysicalRouter) OPTIONAL MATCH (vrf)<-[:BelongsToVRF]-(bgp:BGPSession) WHERE vpn.valid_end_ts IS NULL AND vrf.valid_end_ts IS NULL AND router.valid_end_ts IS NULL AND (bgp IS NULL OR bgp.valid_end_ts IS NULL) RETURN router.name AS router, router.location_city AS city, vrf.rd AS rd, COLLECT(bgp.peer_ip) AS bgp_peers",
  "parameters": {
    "vpn_name": "BLUE_HUB"
  }
}
```

**Response:**
```json
{
  "results": [
    {
      "router": "pe1",
      "city": "London",
      "rd": "10.0.0.7:2000",
      "bgp_peers": ["10.90.90.2", "10.90.90.3"]
    },
    {
      "router": "pe2",
      "city": "Manchester",
      "rd": "10.0.0.8:2001",
      "bgp_peers": ["10.91.91.2"]
    }
  ],
  "totalCount": 2
}
```
