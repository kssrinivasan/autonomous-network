# Spanner Query Examples

This document provides SQL and GQL query examples for exploring the network topology, metrics, and GNN embeddings across all domains.

For schema details and column mappings, see **[Table Reference](table_reference.md)**.

---

## Query Patterns

### Current State Queries

All topology tables use SCD Type 2. To query current state, filter for `valid_end_ts IS NULL`:

```sql
SELECT * FROM PhysicalRouter 
WHERE valid_end_ts IS NULL
```

### Time-Travel Queries

To query topology at a specific point in time `@T`:

```sql
SELECT * FROM PhysicalRouter
WHERE valid_start_ts <= @T 
  AND (valid_end_ts > @T OR valid_end_ts IS NULL)
```

### Cross-Domain Lookups

Property graphs don't span databases. Cross-domain references can be tracked via logical ID patterns where applicable.

---

## Transport Domain Queries

### Physical Topology

Visualizes the physical connectivity between routers through interfaces and links.

```gql
GRAPH transportGraph
MATCH (r1:PhysicalRouter)-[e1:HasInterface]->(i1:PhysicalInterface)
      -[e2:ConnectsTo]->(link:PhysicalLink)
      -[e3:LinkedTo]->(i2:PhysicalInterface)
      <-[e4:HasInterface]-(r2:PhysicalRouter)
WHERE r1.valid_end_ts IS NULL 
  AND r2.valid_end_ts IS NULL
  AND i1.valid_end_ts IS NULL
  AND i2.valid_end_ts IS NULL
  AND link.valid_end_ts IS NULL
RETURN 
    TO_JSON(r1) AS router1,
    TO_JSON(e1) AS r1_to_i1,
    TO_JSON(i1) AS interface1,
    TO_JSON(e2) AS i1_to_link,
    TO_JSON(link) AS link,
    TO_JSON(e3) AS link_to_i2,
    TO_JSON(i2) AS interface2,
    TO_JSON(e4) AS r2_to_i2,
    TO_JSON(r2) AS router2
```


**Find all current routers and their roles:**
```sql
SELECT name, role, location_city, status
FROM PhysicalRouter
WHERE valid_end_ts IS NULL
ORDER BY role, name
```

**Find all interfaces on a router:**
```sql
SELECT i.name, i.ip_address, i.speed, i.status
FROM PhysicalInterface i
WHERE i.router_id = 'router:pe1'
  AND i.valid_end_ts IS NULL
ORDER BY i.name
```

**Graph: Trace physical path between routers:**
```sql
GRAPH transportGraph
MATCH (src:PhysicalRouter)-[:HasInterface]->(i1:PhysicalInterface)
      -[:ConnectsTo]->(link:PhysicalLink)
      -[:LinkedTo]->(i2:PhysicalInterface)<-[:HasInterface]-(dst:PhysicalRouter)
WHERE src.name = 'pe1'
  AND src.valid_end_ts IS NULL
  AND i1.valid_end_ts IS NULL
  AND link.valid_end_ts IS NULL
  AND i2.valid_end_ts IS NULL
  AND dst.valid_end_ts IS NULL
RETURN dst.name AS neighbor, 
       i1.name AS local_interface,
       i2.name AS remote_interface,
       link.bandwidth
```

### L3VPN Service Topology

**Find all VPN services and their topology type:**
```sql
SELECT name, service_type, topology, status
FROM L3VPNService
WHERE valid_end_ts IS NULL
ORDER BY name
```

**Graph: Trace VPN service to routers:**
```sql
GRAPH transportGraph
MATCH (vpn:L3VPNService)<-[:RealizesVPN]-(vrf:VRF)-[:LocatedOn]->(router:PhysicalRouter)
WHERE vpn.name = 'BLUE_HUB'
  AND vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND router.valid_end_ts IS NULL
RETURN vpn.name, vpn.topology, vrf.rd, router.name, router.location_city
```

**Find all BGP sessions for a VRF:**
```sql
SELECT b.peer_ip, b.remote_as, b.status
FROM BGPSession b
WHERE b.vrf_id = 'vrf:pe1:BLUE_SPOKE'
  AND b.valid_end_ts IS NULL
ORDER BY b.peer_ip
```

### Router Metrics

**Latest system metrics for a router:**
```sql
SELECT 
    metric_name,
    interface,
    value,
    timestamp
FROM RouterSystemMetrics
WHERE router_name = 'pe1'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
  AND metric_name IN (
      'node_network_receive_bytes_total',
      'node_network_transmit_bytes_total',
      'node_network_up'
  )
ORDER BY timestamp DESC, interface, metric_name
LIMIT 50
```

**BGP peer uptime metrics:**
```sql
SELECT 
    router_name,
    peer_ip,
    safi,
    value AS uptime_seconds,
    timestamp
FROM RouterRoutingSeries
WHERE metric_name = 'frr_bgp_peer_uptime_seconds'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
ORDER BY timestamp DESC, router_name
```

**Interface traffic hotspots (last 10 minutes):**
```sql
WITH traffic AS (
    SELECT 
        router_name,
        interface,
        SUM(CASE WHEN metric_name = 'node_network_receive_bytes_total' THEN value ELSE 0 END) AS rx_bytes,
        SUM(CASE WHEN metric_name = 'node_network_transmit_bytes_total' THEN value ELSE 0 END) AS tx_bytes
    FROM RouterSystemMetrics
    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
      AND metric_name IN ('node_network_receive_bytes_total', 'node_network_transmit_bytes_total')
    GROUP BY router_name, interface
)
SELECT 
    router_name,
    interface,
    ROUND(rx_bytes / 1024 / 1024, 2) AS rx_mb,
    ROUND(tx_bytes / 1024 / 1024, 2) AS tx_mb
FROM traffic
WHERE rx_bytes + tx_bytes > 0
ORDER BY rx_bytes + tx_bytes DESC
LIMIT 20
```

### GNN Embeddings

**Graph: Find routers with high anomaly scores:**
```sql
GRAPH transportGraph
MATCH (router:PhysicalRouter)-[:RouterHasEmbedding]->(emb:NodeEmbedding)
WHERE router.valid_end_ts IS NULL
  AND emb.timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
  AND emb.hetgnn_score > 0.5
RETURN router.name, router.role, router.location_city,
       emb.hetgnn_score, emb.anomaly_explanation, emb.timestamp
ORDER BY emb.hetgnn_score DESC
```

**Top anomalous features across all nodes:**
```sql
SELECT 
    node_id,
    node_type,
    hetgnn_score,
    JSON_VALUE(anomaly_explanation, '$.top_features[0].feature') AS top_feature,
    CAST(JSON_VALUE(anomaly_explanation, '$.top_features[0].mse') AS FLOAT64) AS top_mse,
    timestamp
FROM NodeEmbedding
WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
  AND hetgnn_score > 0.3
ORDER BY hetgnn_score DESC, timestamp DESC
LIMIT 20
```

**Embedding evolution over time (for a specific router):**
```sql
SELECT 
    timestamp,
    hetgnn_score,
    JSON_VALUE(anomaly_explanation, '$.top_features[0].feature') AS top_feature
FROM NodeEmbedding
WHERE node_id = 'router:pe1'
  AND node_type = 'PhysicalRouter'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
ORDER BY timestamp ASC
```

---

## Infrastructure Domain Queries

---

## Core Domain Queries

**Find all 5G cores and their NF counts:**
```sql
SELECT 
    fc.name AS core_name,
    COUNT(nf.id) AS nf_count,
    fc.phase
FROM FiveGCore fc
LEFT JOIN NetworkFunction nf 
    ON nf.fivegcore_id = fc.id 
    AND nf.valid_end_ts IS NULL
WHERE fc.valid_end_ts IS NULL
GROUP BY fc.name, fc.phase
```

**All network functions in a core:**
```sql
SELECT name, nf_type, phase
FROM NetworkFunction
WHERE fivegcore_id = 'fivegcore:free5gc-lab'
  AND valid_end_ts IS NULL
ORDER BY nf_type, name
```

**Graph: 5G core with all its NFs:**
```sql
GRAPH coreGraph
MATCH (core:FiveGCore)<-[:CoreHasNF]-(nf:NetworkFunction)
WHERE core.name = 'free5gc-lab'
  AND core.valid_end_ts IS NULL
  AND nf.valid_end_ts IS NULL
RETURN core.name, nf.name, nf.nf_type, nf.phase
```

---

## Radio Domain Queries

**All cell sites:**
```sql
SELECT 
    name,
    gnb_id,
    mcc,
    mnc,
    tac,
    phase
FROM CellSite
WHERE valid_end_ts IS NULL
ORDER BY name
```

**UEs attached to a cell site:**
```sql
SELECT 
    imsi,
    sst,
    sd
FROM UE
WHERE cellsite_id = 'cellsite:gnb-01'
  AND valid_end_ts IS NULL
ORDER BY imsi
```

**Graph: Cell sites and their UEs:**
```sql
GRAPH radioGraph
MATCH (cell:CellSite)-[:HasUE]->(ue:UE)
WHERE cell.valid_end_ts IS NULL
  AND ue.valid_end_ts IS NULL
RETURN cell.name, cell.gnb_id, ue.imsi
```

---

## Service Domain Queries

**Active traffic flows:**
```sql
SELECT 
    name,
    src_device_id,
    dst_device_id,
    phase
FROM TrafficFlow
WHERE valid_end_ts IS NULL
  AND phase IN ('pending', 'running')
ORDER BY name
```

**Flow metrics (last 5 minutes):**

> Every leg is reported by both of its traffic agents. `role` is part of the
> primary key and must be carried through any grouping — see `TrafficFlowSeries`
> in [table_reference.md](table_reference.md) for which end measures what.

```sql
SELECT 
    flow_id,
    node_name,
    role,
    metric_name,
    ROUND(value, 2) AS value,
    timestamp
FROM TrafficFlowSeries
WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
  AND metric_name IN (
      'traffic_agent_throughput_bps',
      'traffic_agent_latency_ms',
      'traffic_agent_packet_loss_pct',
      'traffic_agent_jitter_ms'
  )
ORDER BY timestamp DESC, flow_id, metric_name, role
LIMIT 50
```

**Join flow topology with metrics:**
```sql
SELECT 
    f.name AS flow_name,
    f.src_device_id,
    f.dst_device_id,
    f.phase,
    tfm.metric_name,
    tfm.role,
    ROUND(tfm.value, 2) AS value,
    tfm.timestamp
FROM TrafficFlow f
JOIN TrafficFlowSeries tfm
    ON STARTS_WITH(tfm.flow_id, REPLACE(f.id, 'flow:', ''))
WHERE f.valid_end_ts IS NULL
  AND tfm.timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
ORDER BY tfm.timestamp DESC
LIMIT 100
```

**Flows with degraded performance:**

> Note the `role` filters. Latency, jitter and loss are only meaningful at the
> receiving end: a sender observes no returning packets and reports a flat 50%
> loss, so aggregating across both ends flags every healthy flow as degraded.
> Throughput offered is the sending end's figure.

```sql
SELECT 
    flow_id,
    MAX(CASE WHEN metric_name = 'traffic_agent_throughput_bps'
              AND role = 'source'      THEN value END) AS throughput_bps,
    MAX(CASE WHEN metric_name = 'traffic_agent_latency_ms'
              AND role = 'destination' THEN value END) AS latency_ms,
    MAX(CASE WHEN metric_name = 'traffic_agent_packet_loss_pct'
              AND role = 'destination' THEN value END) AS packet_loss_pct,
    MAX(timestamp) AS latest_timestamp
FROM TrafficFlowSeries
WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
GROUP BY flow_id
HAVING MAX(CASE WHEN metric_name = 'traffic_agent_packet_loss_pct'
                 AND role = 'destination' THEN value END) > 1.0
    OR MAX(CASE WHEN metric_name = 'traffic_agent_latency_ms'
                 AND role = 'destination' THEN value END) > 100
ORDER BY packet_loss_pct DESC, latency_ms DESC
```

---

## Operations Domain Queries

### Fault Events

**Active fault injections:**
```sql
SELECT 
    name,
    failure_type,
    injection_mode,
    target_domain,
    target_id,
    target_type,
    phase,
    injected_at
FROM FaultEvent
WHERE valid_end_ts IS NULL
ORDER BY valid_start_ts DESC
```

**Fault history for a specific router:**
```sql
SELECT 
    name,
    failure_type,
    injection_mode,
    target_interface,
    target_vrf,
    phase,
    injected_at,
    valid_start_ts,
    valid_end_ts
FROM FaultEvent
WHERE target_router = 'pe1'
  AND valid_start_ts > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY valid_start_ts DESC
```

**Correlate faults with anomaly scores:**
```sql
SELECT 
    fe.name AS fault_name,
    fe.failure_type,
    fe.target_id,
    fe.injected_at,
    ne.hetgnn_score,
    JSON_VALUE(ne.anomaly_explanation, '$.top_features[0].feature') AS top_feature,
    ne.timestamp AS embedding_time
FROM FaultEvent fe
JOIN NodeEmbedding ne
    ON ne.node_id = fe.target_id
    AND ne.timestamp >= fe.valid_start_ts
    AND ne.timestamp <= TIMESTAMP_ADD(fe.valid_start_ts, INTERVAL 30 MINUTE)
WHERE fe.valid_end_ts IS NULL
ORDER BY ne.hetgnn_score DESC
LIMIT 20
```

### Agent Traces

**Find traces for a specific operation:**
```sql
SELECT 
    trace_id,
    span_id,
    operation_name,
    timestamp,
    details
FROM AgentTrace
WHERE operation_name = 'diagnose_bgp_failure'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
ORDER BY timestamp DESC
LIMIT 20
```

**Reconstruct full trace by trace_id:**
```sql
SELECT 
    span_id,
    parent_span_id,
    operation_name,
    timestamp,
    details
FROM AgentTrace
WHERE trace_id = '<your-trace-id>'
ORDER BY timestamp ASC
```

### Network Descriptors

**List saved network configurations:**
```sql
SELECT 
    name,
    description,
    labels,
    created_at,
    updated_at
FROM NetworkDescriptor
ORDER BY updated_at DESC
```

### Intent Management

**List all active L3 VPN intents:**
```sql
SELECT 
    id,
    serviceId,
    crName,
    state,
    createdAt,
    updatedAt
FROM IntentRecords
WHERE state != 'Inactive'
ORDER BY createdAt DESC
```

**Find intents by state:**
```sql
-- In Progress intents
SELECT 
    id,
    serviceId,
    crName,
    JSON_VALUE(intentEnvelope, '$.name') AS intent_name,
    createdAt,
    updatedAt
FROM IntentRecords
WHERE state = 'InProgress'
ORDER BY createdAt ASC

-- Failed intents
SELECT 
    id,
    serviceId,
    crName,
    JSON_VALUE(intentEnvelope, '$.message') AS error_message,
    updatedAt
FROM IntentRecords
WHERE state = 'Rejected'
ORDER BY updatedAt DESC
```

**Get full intent details:**
```sql
SELECT 
    id,
    serviceId,
    crName,
    state,
    intentEnvelope,
    createdAt,
    updatedAt
FROM IntentRecords
WHERE id = '<intent-uuid>'
```

**Intent lifecycle history (by serviceId):**
```sql
SELECT 
    id,
    state,
    JSON_VALUE(intentEnvelope, '$.name') AS intent_name,
    createdAt,
    updatedAt,
    TIMESTAMP_DIFF(updatedAt, createdAt, SECOND) AS duration_seconds
FROM IntentRecords
WHERE serviceId = 'RED_MESH'
ORDER BY createdAt DESC
```

**Intent processing time analysis:**
```sql
SELECT 
    state,
    COUNT(*) AS count,
    AVG(TIMESTAMP_DIFF(updatedAt, createdAt, SECOND)) AS avg_processing_seconds,
    MAX(TIMESTAMP_DIFF(updatedAt, createdAt, SECOND)) AS max_processing_seconds
FROM IntentRecords
WHERE createdAt > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY state
ORDER BY state
```

**Find recent intent changes:**
```sql
SELECT 
    id,
    serviceId,
    state,
    JSON_VALUE(intentEnvelope, '$.name') AS intent_name,
    JSON_VALUE(intentEnvelope, '$.intentExpression.expectation[0].expectationObject.topology') AS topology,
    updatedAt
FROM IntentRecords
WHERE updatedAt > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
ORDER BY updatedAt DESC
LIMIT 20
```

**Check for service ID conflicts:**
```sql
SELECT 
    serviceId,
    COUNT(*) AS active_count,
    ARRAY_AGG(id ORDER BY createdAt) AS intent_ids
FROM IntentRecords
WHERE state != 'Inactive'
GROUP BY serviceId
HAVING COUNT(*) > 1
```

---

## Advanced Patterns

### Temporal Comparison (Before/After)

**Compare router config before and after a change:**
```sql
WITH before AS (
    SELECT config
    FROM PhysicalRouter
    WHERE id = 'router:pe1'
      AND valid_start_ts < TIMESTAMP('2026-08-11 10:00:00 UTC')
    ORDER BY valid_start_ts DESC
    LIMIT 1
),
after AS (
    SELECT config
    FROM PhysicalRouter
    WHERE id = 'router:pe1'
      AND valid_start_ts >= TIMESTAMP('2026-08-11 10:00:00 UTC')
    ORDER BY valid_start_ts ASC
    LIMIT 1
)
SELECT 
    (SELECT config FROM before) AS config_before,
    (SELECT config FROM after) AS config_after
```

### Embedding Change Detection

**Detect significant embedding changes:**
```sql
WITH changes AS (
    SELECT 
        node_id,
        node_type,
        timestamp,
        hetgnn_score,
        JSON_VALUE(anomaly_explanation, '$.top_features[0].feature') AS top_feature,
        LAG(hetgnn_score) OVER (PARTITION BY node_id ORDER BY timestamp) AS prev_score
    FROM NodeEmbedding
    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
)
SELECT 
    node_id,
    timestamp AS change_detected_at,
    hetgnn_score AS current_score,
    prev_score,
    ROUND(hetgnn_score - COALESCE(prev_score, 0), 4) AS score_jump,
    top_feature
FROM changes
WHERE hetgnn_score - COALESCE(prev_score, 0) > 0.2
ORDER BY score_jump DESC
```

---

## Query Performance Tips

1. **Always filter by `valid_end_ts IS NULL`** for current state queries
2. **Add time range filters** to metric queries: `timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)`
3. **Use LIMIT** for exploratory queries to avoid large result sets
4. **Leverage indexes**: Queries by `router_name`, `nf_type`, `fivegcore_id`, `cellsite_id` are indexed
5. **GQL vs SQL**: Use GQL for multi-hop graph traversal, SQL for filtering and aggregation

---

## Related Documentation

- **[Spanner Readme](Readme.md)** — Conceptual domain model overview
- **[Table Reference](table_reference.md)** — Complete schema reference
- **[CRD → Spanner Mapping](crd_spanner_mapping.md)** — How CRDs populate tables
