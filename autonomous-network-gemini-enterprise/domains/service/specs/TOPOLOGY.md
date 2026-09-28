# Service Domain Topology API (GQL)

Graph Query Language (GQL) API for traversing end-to-end service topology using Spanner property graphs.

---

## Overview

**Purpose:** Execute graph queries to discover traffic flow paths and performance metrics.

**Spanner Property Graph:** `serviceGraph`

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/service`

---

## Property Graph Schema

### Node Types

**TrafficFlow**
- Properties: `id`, `name`, `src_device_id`, `dst_device_id`, `phase`, `config`, `valid_start_ts`, `valid_end_ts`
- Labels: `TrafficFlow`

**TrafficFlowSeries**
- Properties: `flow_id`, `timestamp`, `node_name`, `metric_name`, `role`, `metric_type`, `value`, `labels`
- Labels: `TrafficFlowSeries`

This is a **long-format** table: one row per metric per reporting end, not one row
per flow with a column per KPI. The metric is named in `metric_name` and its
reading is in `value`. Known `metric_name` values:

| `metric_name` | Unit | Reported by `role` |
|---------------|------|--------------------|
| `traffic_agent_throughput_sent_bps` | bits/s | `source` |
| `traffic_agent_throughput_recv_bps` | bits/s | `destination` |
| `traffic_agent_latency_ms` | ms | `destination` |
| `traffic_agent_jitter_ms` | ms | `destination` |
| `traffic_agent_packet_loss_pct` | percent | `destination` |
| `traffic_agent_bytes_sent_total` | bytes (counter) | `source` |
| `traffic_agent_bytes_received_total` | bytes (counter) | `destination` |

Both ends of a leg report every metric under the same `flow_id` and the same
`node_name` — the collector derives `node_name` from `flow_id` — so **`role` is
the only thing that separates them**, and it is part of the primary key for that
reason. Always filter on the end that actually measures the metric: a sender
observes no delay, jitter or loss, and its `packet_loss_pct` reads a fabricated
~50%. See migration plan 7.8a.

**NodeEmbedding**
- Properties: `id`, `node_id`, `node_type`, `hetgnn_embedding`, `hetgnn_score`, `anomaly_explanation`, `timestamp`
- Labels: `NodeEmbedding`

### Edge Types

| Edge Label | Source Node | Target Node | Description |
|------------|-------------|-------------|-------------|
| `FlowHasMetrics` | TrafficFlow | TrafficFlowSeries | Flow has performance metrics, within the flow's validity window |
| `FlowHasEmbedding` | TrafficFlow | NodeEmbedding | Flow has a GNN embedding / anomaly score |

---

## Common Query Patterns

### 1. Find Metrics for a Traffic Flow

```gql
GRAPH serviceGraph
MATCH (flow:TrafficFlow {id: $flow_id})-[:FlowHasMetrics]->(m:TrafficFlowSeries)
WHERE flow.valid_end_ts IS NULL
  AND m.timestamp >= $start_time
  AND m.metric_name = 'traffic_agent_packet_loss_pct'
  AND m.role = 'destination'
ORDER BY m.timestamp DESC
LIMIT 100
RETURN m.timestamp AS timestamp,
       m.node_name AS node_name,
       m.value AS packet_loss_pct
```

### 2. Find All Flows Between Devices

```gql
GRAPH serviceGraph
MATCH (flow:TrafficFlow)
WHERE flow.src_device_id = $src_device
  AND flow.dst_device_id = $dst_device
  AND flow.valid_end_ts IS NULL
RETURN flow.id AS flow_id,
       flow.name AS name,
       flow.phase AS phase
```

---

## Complete Example

```bash
POST /tmf-api/resourceInventoryManagement/v4/service/graph/query
Content-Type: application/json

{
  "gql": "GRAPH serviceGraph MATCH (flow:TrafficFlow {id: $flow_id})-[:FlowHasMetrics]->(m:TrafficFlowSeries) WHERE flow.valid_end_ts IS NULL AND m.metric_name = 'traffic_agent_latency_ms' AND m.role = 'destination' ORDER BY m.timestamp DESC LIMIT 10 RETURN m.timestamp, m.metric_name, m.value",
  "parameters": {
    "flow_id": "flow:d1-to-hub-tcp"
  }
}

Response:
{
  "results": [
    {
      "m.timestamp": "2026-08-12T10:15:00Z",
      "m.metric_name": "traffic_agent_latency_ms",
      "m.value": 5.2
    },
    {
      "m.timestamp": "2026-08-12T10:14:40Z",
      "m.metric_name": "traffic_agent_latency_ms",
      "m.value": 5.1
    }
  ],
  "totalCount": 10
}
```
