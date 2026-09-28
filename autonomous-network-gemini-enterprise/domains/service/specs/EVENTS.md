# Service Domain Event API (TMF688)

Event Management API for subscribing to traffic flow lifecycle changes following TMF688 specification.

---

## Overview

**Purpose:** Real-time notifications for topology changes in the service domain (traffic flows).

**TMF Alignment:** TMF688 Event Management API v4.0

**Base URL:** `/tmf-api/resourceInventoryManagement/v4/service/events`

---

## Event Types

### ResourceCreateEvent

**Trigger:** New traffic flow provisioned

**Example:**
```json
{
  "eventId": "s1e2r3v4-5i6c-7e8e-9v0e-1n2t3i4d5s6r",
  "eventTime": "2026-08-12T10:15:30.123Z",
  "eventType": "ResourceCreateEvent",
  "domain": "service",
  "event": {
    "resource": {
      "@type": "TrafficFlow",
      "id": "flow:d1-to-hub-tcp",
      "name": "d1-to-hub-tcp",
      "resourceCharacteristic": [
        {"name": "src_device_id", "value": "host:traffic-d1"},
        {"name": "dst_device_id", "value": "host:traffic-hub"},
        {"name": "protocol", "value": "TCP"},
        {"name": "phase", "value": "pending"}
      ]
    }
  }
}
```

---

### ResourceStateChangeEvent

**Trigger:** Flow phase changed (pending → running → completed)

**Example:**
```json
{
  "eventId": "s2e3r4v5-6i7c-8e9e-0v1e-2n3t4i5d6s7r",
  "eventTime": "2026-08-12T10:16:00.456Z",
  "eventType": "ResourceStateChangeEvent",
  "domain": "service",
  "priority": "Normal",
  "event": {
    "resource": {
      "@type": "TrafficFlow",
      "id": "flow:d1-to-hub-tcp",
      "resourceStatus": "Running"
    },
    "stateChange": {
      "previousState": "Pending",
      "newState": "Running"
    }
  }
}
```

---

### ResourceDeleteEvent

**Trigger:** Flow terminated

**Example:**
```json
{
  "eventId": "s3e4r5v6-7i8c-9e0e-1v2e-3n4t5i6d7s8r",
  "eventTime": "2026-08-12T11:00:00.000Z",
  "eventType": "ResourceDeleteEvent",
  "domain": "service",
  "event": {
    "resource": {
      "@type": "TrafficFlow",
      "id": "flow:d1-to-hub-tcp",
      "validFor": {
        "startDateTime": "2026-08-12T10:15:30.123Z",
        "endDateTime": "2026-08-12T11:00:00.000Z"
      }
    }
  }
}
```

---

## Subscription APIs

### WebSocket

**URL:** `ws://<api-host>/tmf-api/resourceInventoryManagement/v4/service/events`

**Subscribe:**
```json
{
  "action": "subscribe",
  "filters": {
    "resourceSpecification": ["TrafficFlow"],
    "eventTypes": ["ResourceStateChangeEvent"],
    "protocol": "TCP"
  }
}
```

### Server-Sent Events

**URL:** `GET /tmf-api/resourceInventoryManagement/v4/service/events/stream?resourceSpecification=TrafficFlow&protocol=TCP`

---

## Complete Example

Monitor all traffic flow state changes:

```javascript
const ws = new WebSocket('ws://api-host/tmf-api/resourceInventoryManagement/v4/service/events');

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    filters: {
      resourceSpecification: ['TrafficFlow'],
      eventTypes: ['ResourceStateChangeEvent']
    }
  }));
};

ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  if (event.eventType === 'ResourceStateChangeEvent') {
    console.log(`Flow ${event.event.resource.id}: ${event.event.stateChange.previousState} → ${event.event.stateChange.newState}`);
    
    if (event.event.stateChange.newState === 'Running') {
      console.log('Traffic flow started successfully');
    } else if (event.event.stateChange.newState === 'Completed') {
      console.log('Traffic flow completed');
    }
  }
};
```
