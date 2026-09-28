# Service Domain Inventory API (TMF639)

REST API for querying end-to-end traffic flows from Spanner, following TMF639 Resource Inventory Management specification.

---

## Overview

**Purpose:** Query service domain resources including end-to-end traffic flows and performance metrics.

**Spanner Database:** `servicetopology-db`

**TMF Alignment:** TMF639 Resource Inventory Management API v4.0

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/service`

**Key Capabilities:**
- Query current state of traffic flows
- Filter by source/destination device, protocol, phase
- Cross-domain references to infrastructure (compute hosts)

---

## Resource Models

### TrafficFlow

**Description:** End-to-end traffic flow between source and destination devices

**TMF639 Resource Schema:**
```json
{
  "@type": "TrafficFlow",
  "id": "flow:d1-to-hub-tcp",
  "href": "/tmf-api/resourceInventoryManagement/v4/service/resource/flow:d1-to-hub-tcp",
  "name": "d1-to-hub-tcp",
  "resourceSpecification": {
    "id": "TrafficFlow",
    "name": "Traffic Flow"
  },
  "resourceStatus": "Running",
  "resourceCharacteristic": [
    {"name": "src_device_id", "value": "host:traffic-d1"},
    {"name": "dst_device_id", "value": "host:traffic-hub"},
    {"name": "protocol", "value": "TCP"},
    {"name": "port", "value": 5201},
    {"name": "phase", "value": "running"},
    {"name": "throughput_mbps", "value": "100 Mbps"},
    {"name": "pattern", "value": "constant"}
  ],
  "validFor": {
    "startDateTime": "2026-08-12T08:30:00Z",
    "endDateTime": null
  }
}
```

---

## REST API Reference

### GET /resource

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/service/resource`

**Query Parameters:**

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `resourceSpecification` | string | Filter by resource type | `TrafficFlow` |
| `resourceStatus` | string | Filter by status | `Running` |
| `phase` | string | Filter by lifecycle phase | `running` |
| `srcDeviceId` | string | Filter by source device | `host:traffic-d1` |
| `dstDeviceId` | string | Filter by destination device | `host:traffic-hub` |
| `protocol` | string | Filter by protocol | `TCP` |
| `limit` | integer | Page size (max 100) | `20` |
| `offset` | integer | Page offset | `0` |

**Examples:**

```bash
# Get all running traffic flows
GET /tmf-api/resourceInventoryManagement/v4/service/resource?resourceSpecification=TrafficFlow&phase=running

# Get all flows from a specific device
GET /tmf-api/resourceInventoryManagement/v4/service/resource?resourceSpecification=TrafficFlow&srcDeviceId=host:traffic-d1

# Get all TCP flows
GET /tmf-api/resourceInventoryManagement/v4/service/resource?resourceSpecification=TrafficFlow&protocol=TCP
```

---

## Spanner Query Patterns

### Current State Query

```sql
SELECT id, src_device_id, dst_device_id, protocol, port, phase
FROM TrafficFlow
WHERE valid_end_ts IS NULL
ORDER BY id
```

### Filter by Phase

```sql
SELECT id, src_device_id, dst_device_id, phase
FROM TrafficFlow
WHERE valid_end_ts IS NULL
  AND phase = 'running'
ORDER BY id
```

---

## Complete Example

**Scenario:** Query all active traffic flows

```bash
GET /tmf-api/resourceInventoryManagement/v4/service/resource?resourceSpecification=TrafficFlow&phase=running

Response:
{
  "resourceInventory": [
    {
      "@type": "TrafficFlow",
      "id": "flow:d1-to-hub-tcp",
      "name": "d1-to-hub-tcp",
      "resourceCharacteristic": [
        {"name": "src_device_id", "value": "host:traffic-d1"},
        {"name": "dst_device_id", "value": "host:traffic-hub"},
        {"name": "protocol", "value": "TCP"},
        {"name": "phase", "value": "running"}
      ]
    }
  ],
  "totalCount": 5
}
```
