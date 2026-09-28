# Service Domain Orchestrator - Decomposition Rules

## Overview

**Purpose:** Decompose high-level NetworkSliceIntent into coordinated Transport, Core, and Radio domain intents for end-to-end network slice provisioning.

**Input:** NetworkSliceIntent (TMF921)
- Coverage area (regions, zones)
- Capacity requirements (subscribers, throughput, concurrent users)
- SLA parameters (latency, availability, speeds)
- Service type (eMBB, URLLC, mMTC)

**Output:** Multi-domain intent submission
- 1x Transport L3ConnectivityIntent (backhaul)
- 1x Core MobileServiceIntent (5G core)
- Nx Radio CellSiteIntent (cell sites per zone)

**External Dependencies:**
- Zone registry: Maps zone names → geographic bounds, terrain type, infrastructure region
- Domain APIs: Transport API, Core API, Radio API availability
- Capacity planning database: Coverage formulas, user density data

---

## Input Intent Schema

### Required Fields

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `sliceName` | string | Service name | Unique |
| `operator` | string | Network operator | Must exist in operator registry |
| `serviceType` | enum | eMBB, URLLC, mMTC | |
| `coverageArea.region` | string | Primary region | Must exist in zone registry |
| `coverageArea.zones[]` | array | Coverage zones with priorities | Min 1 zone |
| `capacity.totalSubscribers` | integer | Total subscriber capacity | > 0 |
| `capacity.peakConcurrentUsers` | integer | Peak concurrent users | ≤ totalSubscribers |
| `capacity.aggregateThroughput` | string | Total throughput (e.g., "10 Gbps") | Parseable bandwidth string |
| `sla.downloadSpeed` | string | Download speed requirement | |
| `sla.latency` | string | Maximum latency | |
| `sla.availability` | string | Availability SLA (e.g., "99.99%") | |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `backhaul.bandwidth` | Calculated from aggregate throughput | Backhaul capacity |
| `backhaul.latency` | sla.latency / 4 | Backhaul latency budget |
| `backhaul.redundancy` | "single-path" | Redundancy mode |

---

## Decomposition Algorithm

### Phase 1: Validation and Analysis

```
1. Validate NetworkSliceIntent schema
2. Resolve all zones from zone registry
   FOR each zone in coverageArea.zones:
     - Query zone registry for geographic bounds, terrain, infrastructure region
     - Validate zone exists
     - Extract: area_km2, terrain_type, user_density, region_name
3. Calculate total coverage area (sum of zone areas)
4. Validate operator exists in operator registry
```

### Phase 2: Capacity Distribution

```
1. Calculate per-zone capacity allocation:
   
   total_weight = SUM(zone.priority_weight for all zones)
   
   FOR each zone:
     priority_weight = {high: 3, medium: 2, low: 1}[zone.priority]
     zone.subscriber_allocation = (priority_weight / total_weight) * totalSubscribers
     zone.throughput_allocation = (priority_weight / total_weight) * aggregateThroughput
     zone.user_allocation = (priority_weight / total_weight) * peakConcurrentUsers

2. Calculate cell site count per zone:
   
   FOR each zone:
     coverage_per_site_km2 = LOOKUP_COVERAGE_FORMULA(serviceType, terrain_type)
     users_per_site = LOOKUP_CAPACITY_FORMULA(serviceType)
     
     sites_for_coverage = CEIL(zone.area_km2 / coverage_per_site_km2)
     sites_for_capacity = CEIL(zone.user_allocation / users_per_site)
     
     zone.required_cell_sites = MAX(sites_for_coverage, sites_for_capacity)
     
     IF zone.priority == "high":
       zone.required_cell_sites *= 1.2  // 20% headroom
```

**Coverage Formulas (reference values):**

| Service Type | Terrain | Coverage per Site |
|--------------|---------|------------------|
| eMBB | urban-dense | 0.5 km² |
| eMBB | urban | 1.0 km² |
| eMBB | suburban | 2.0 km² |
| URLLC | urban-dense | 0.3 km² |
| mMTC | suburban | 5.0 km² |

| Service Type | Users per Site |
|--------------|----------------|
| eMBB | 250 |
| URLLC | 100 |
| mMTC | 1000 |

### Phase 3: Transport Intent Generation

```
1. Identify PE routers for backhaul:
   
   pe_endpoints = []
   
   // Core network PE
   core_region = coverageArea.region
   core_pe = QUERY_INFRASTRUCTURE(region=core_region, role="PE", purpose="core")
   pe_endpoints.append({
     peRouter: core_pe.name,
     peInterface: core_pe.customer_interface,
     customerRouter: f"ce-core-{region}",
     customerPrefix: ALLOCATE_SUBNET(purpose="core")
   })
   
   // Cell site PEs (one per zone or shared)
   FOR each zone in coverageArea.zones:
     zone_pe = QUERY_INFRASTRUCTURE(zone=zone.name, role="PE", purpose="cell-backhaul")
     pe_endpoints.append({
       peRouter: zone_pe.name,
       peInterface: zone_pe.customer_interface,
       customerRouter: f"ce-cell-{zone.name}",
       customerPrefix: ALLOCATE_SUBNET(purpose=f"cell-{zone.name}")
     })

2. Calculate backhaul SLA:
   
   backhaul_bandwidth = SUM(zone.throughput_allocation for all zones)
   backhaul_latency = PARSE_LATENCY(sla.latency) / 4  // Allocate 25% of end-to-end latency to backhaul

3. Generate Transport Intent:
   
   transport_intent = {
     "@type": "L3ConnectivityIntent",
     "expectationVerb": "DELIVER",
     "expectationObject": {
       "serviceName": f"{sliceName} Backhaul",
       "topology": "any-to-any",
       "endpoints": pe_endpoints,
       "sla": {
         "bandwidth": f"{backhaul_bandwidth} per endpoint",
         "latency": f"< {backhaul_latency}ms",
         "availability": sla.availability,
         "trafficClass": "business-critical"
       }
     }
   }
```

### Phase 4: Core Intent Generation

```
1. Extract capacity parameters:
   
   core_capacity = {
     "totalSubscribers": capacity.totalSubscribers,
     "initialSubscribers": MIN(1000, capacity.totalSubscribers * 0.2),  // 20% initial
     "peakConcurrentUsers": capacity.peakConcurrentUsers,
     "aggregateThroughput": capacity.aggregateThroughput
   }

2. Extract service level:
   
   service_level = {
     "downloadSpeed": sla.downloadSpeed,
     "uploadSpeed": sla.uploadSpeed OR DERIVE_FROM_SERVICE_TYPE(serviceType),
     "latency": sla.latency,
     "reliability": sla.availability
   }

3. Generate Core Intent:
   
   core_intent = {
     "@type": "MobileServiceIntent",
     "expectationVerb": "DELIVER",
     "expectationObject": {
       "serviceName": f"{sliceName} Core",
       "operator": operator,
       "region": coverageArea.region,
       "serviceType": serviceType,
       "capacity": core_capacity,
       "serviceLevel": service_level
     }
   }
```

### Phase 5: Radio Intent Generation

```
1. Generate one CellSiteIntent per zone:
   
   radio_intents = []
   
   FOR each zone in coverageArea.zones:
     
     // Determine gNodeB deployment
     deployment = {
       "gNodeBCount": zone.required_cell_sites,
       "sectorization": "3-sector"  // Default
     }
     
     // Calculate zone capacity
     zone_capacity = {
       "peakUsers": zone.user_allocation,
       "throughput": zone.throughput_allocation
     }
     
     // Service linkage
     service_link = {
       "mobileService": f"{sliceName} Core"  // References Core intent name
     }
     
     radio_intent = {
       "@type": "CellSiteIntent",
       "expectationVerb": "DELIVER",
       "expectationObject": {
         "siteName": f"{sliceName} - {zone.name}",
         "coverageZone": zone.name,
         "deployment": deployment,
         "capacity": zone_capacity,
         "serviceLink": service_link
       }
     }
     
     radio_intents.append(radio_intent)
```

### Phase 6: Intent Submission

```
1. Submit intents in dependency order:
   
   // Step 1: Transport (backhaul infrastructure)
   transport_response = POST(f"{TRANSPORT_API}/intent", transport_intent)
   IF transport_response.status != 202:
     RAISE OrchestratorError("Transport intent failed", transport_response)
   
   transport_intent_id = transport_response.body.id
   
   // Step 2: Wait for Transport to reach Ready (or timeout after 5 minutes)
   WAIT_FOR_STATE(TRANSPORT_API, transport_intent_id, "Completed", timeout=300)
   
   // Step 3: Core (5G core network)
   core_response = POST(f"{CORE_API}/intent", core_intent)
   IF core_response.status != 202:
     ROLLBACK_TRANSPORT(transport_intent_id)
     RAISE OrchestratorError("Core intent failed", core_response)
   
   core_intent_id = core_response.body.id
   
   // Step 4: Wait for Core to reach Ready
   WAIT_FOR_STATE(CORE_API, core_intent_id, "Completed", timeout=600)
   
   // Step 5: Radio (cell sites) - can be submitted in parallel
   radio_intent_ids = []
   FOR each radio_intent in radio_intents:
     radio_response = POST(f"{RADIO_API}/intent", radio_intent)
     IF radio_response.status != 202:
       // Continue with other zones even if one fails
       LOG_WARNING(f"Radio intent failed for zone {radio_intent.siteName}")
     ELSE:
       radio_intent_ids.append(radio_response.body.id)
   
   IF LEN(radio_intent_ids) == 0:
     // All radio intents failed
     ROLLBACK_CORE(core_intent_id)
     ROLLBACK_TRANSPORT(transport_intent_id)
     RAISE OrchestratorError("All radio intents failed")

2. Store intent mappings:
   
   STORE_MAPPING(slice_intent_id, {
     "transport": transport_intent_id,
     "core": core_intent_id,
     "radio": radio_intent_ids,
     "zones": [zone.name for zone in coverageArea.zones]
   })
```

---

## State Management

### Intent Mapping Storage

```
{
  "slice-intent-001": {
    "sliceName": "London Enhanced Mobile Broadband",
    "transport": {
      "intentId": "transport-001",
      "serviceName": "London Enhanced Mobile Broadband Backhaul"
    },
    "core": {
      "intentId": "core-001",
      "serviceName": "London Enhanced Mobile Broadband Core"
    },
    "radio": [
      {"intentId": "radio-001", "zone": "Central London"},
      {"intentId": "radio-002", "zone": "West London"},
      {"intentId": "radio-003", "zone": "East London"}
    ],
    "createdAt": "2026-08-12T10:00:00Z",
    "state": "InProgress"
  }
}
```

### State Aggregation

```
FUNCTION aggregate_state(slice_intent_id):
  mapping = GET_MAPPING(slice_intent_id)
  
  // Query all domain intent states
  transport_state = GET_INTENT_STATE(TRANSPORT_API, mapping.transport.intentId)
  core_state = GET_INTENT_STATE(CORE_API, mapping.core.intentId)
  radio_states = [GET_INTENT_STATE(RADIO_API, r.intentId) for r in mapping.radio]
  
  all_states = [transport_state, core_state] + radio_states
  
  // Aggregate according to rules
  IF ANY(state == "Rejected" for state in all_states):
    RETURN "Rejected"
  ELSE IF ANY(state == "InProgress" for state in all_states):
    RETURN "InProgress"
  ELSE IF ALL(state == "Completed" for state in all_states):
    RETURN "Completed"
  ELSE:
    RETURN "Acknowledged"
```

---

## Error Handling

### Validation Errors (400 Bad Request)

| Error Condition | Error Message |
|-----------------|---------------|
| Missing required field | `"Missing required field: {field_name}"` |
| Invalid service type | `"Invalid serviceType: must be eMBB, URLLC, or mMTC"` |
| Zone not found | `"Coverage zone '{zone_name}' not found in zone registry"` |
| Invalid capacity | `"peakConcurrentUsers ({value}) exceeds totalSubscribers ({total})"` |

### Conflict Errors (409 Conflict)

| Error Condition | Error Message |
|-----------------|---------------|
| Duplicate slice name | `"Network slice '{sliceName}' already exists"` |

### Dependency Errors (424 Failed Dependency)

| Error Condition | Error Message | Rollback Action |
|-----------------|---------------|-----------------|
| Transport API unavailable | `"Transport API unavailable"` | None |
| Core API unavailable | `"Core API unavailable"` | Delete transport intent |
| Radio API unavailable | `"Radio API unavailable"` | Delete core and transport intents |
| Transport intent failed | `"Transport backhaul provisioning failed: {reason}"` | None (already failed) |
| Core intent failed | `"Core service provisioning failed: {reason}"` | Delete transport intent |
| All radio intents failed | `"All cell site deployments failed"` | Delete core and transport intents |

### Rollback Procedures

```
FUNCTION rollback_transport(intent_id):
  DELETE(f"{TRANSPORT_API}/intent/{intent_id}")
  LOG("Rolled back transport intent {intent_id}")

FUNCTION rollback_core(intent_id):
  DELETE(f"{CORE_API}/intent/{intent_id}")
  LOG("Rolled back core intent {intent_id}")

FUNCTION rollback_radio(intent_ids):
  FOR intent_id in intent_ids:
    DELETE(f"{RADIO_API}/intent/{intent_id}")
  LOG(f"Rolled back {LEN(intent_ids)} radio intents")

FUNCTION rollback_all(mapping):
  rollback_radio(mapping.radio.intentIds)
  rollback_core(mapping.core.intentId)
  rollback_transport(mapping.transport.intentId)
```

---

## Example Transformation

### Input: NetworkSliceIntent

```json
{
  "sliceName": "London Enhanced Mobile Broadband",
  "operator": "TelecomCo UK",
  "serviceType": "eMBB",
  "coverageArea": {
    "region": "Greater London",
    "zones": [
      {"name": "Central London", "priority": "high"},
      {"name": "West London", "priority": "medium"},
      {"name": "East London", "priority": "medium"}
    ],
    "totalArea": "1600 sq km"
  },
  "capacity": {
    "totalSubscribers": 50000,
    "peakConcurrentUsers": 5000,
    "aggregateThroughput": "10 Gbps"
  },
  "sla": {
    "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
    "uploadSpeed": "50 Mbps typical",
    "latency": "< 20ms",
    "availability": "99.99%"
  }
}
```

### Output: Multi-Domain Intents

**Transport Intent:**
```json
{
  "@type": "L3ConnectivityIntent",
  "expectationObject": {
    "serviceName": "London Enhanced Mobile Broadband Backhaul",
    "topology": "any-to-any",
    "endpoints": [
      {"peRouter": "pe1", "peInterface": "eth2", "customerRouter": "ce-core-london"},
      {"peRouter": "pe2", "peInterface": "eth3", "customerRouter": "ce-cell-central"},
      {"peRouter": "pe3", "peInterface": "eth2", "customerRouter": "ce-cell-west"},
      {"peRouter": "pe4", "peInterface": "eth2", "customerRouter": "ce-cell-east"}
    ],
    "sla": {
      "bandwidth": "10 Gbps per endpoint",
      "latency": "< 5ms",
      "availability": "99.99%"
    }
  }
}
```

**Core Intent:**
```json
{
  "@type": "MobileServiceIntent",
  "expectationObject": {
    "serviceName": "London Enhanced Mobile Broadband Core",
    "operator": "TelecomCo UK",
    "region": "Greater London",
    "serviceType": "eMBB",
    "capacity": {
      "totalSubscribers": 50000,
      "initialSubscribers": 10000,
      "peakConcurrentUsers": 5000,
      "aggregateThroughput": "10 Gbps"
    },
    "serviceLevel": {
      "downloadSpeed": "100 Mbps typical, 1 Gbps peak",
      "latency": "< 20ms",
      "reliability": "99.99%"
    }
  }
}
```

**Radio Intents (3x):**
```json
[
  {
    "@type": "CellSiteIntent",
    "expectationObject": {
      "siteName": "London Enhanced Mobile Broadband - Central London",
      "coverageZone": "Central London",
      "deployment": {"gNodeBCount": 20},
      "capacity": {"peakUsers": 2142, "throughput": "4.3 Gbps"},
      "serviceLink": {"mobileService": "London Enhanced Mobile Broadband Core"}
    }
  },
  {
    "@type": "CellSiteIntent",
    "expectationObject": {
      "siteName": "London Enhanced Mobile Broadband - West London",
      "coverageZone": "West London",
      "deployment": {"gNodeBCount": 15},
      "capacity": {"peakUsers": 1429, "throughput": "2.85 Gbps"},
      "serviceLink": {"mobileService": "London Enhanced Mobile Broadband Core"}
    }
  },
  {
    "@type": "CellSiteIntent",
    "expectationObject": {
      "siteName": "London Enhanced Mobile Broadband - East London",
      "coverageZone": "East London",
      "deployment": {"gNodeBCount": 15},
      "capacity": {"peakUsers": 1429, "throughput": "2.85 Gbps"},
      "serviceLink": {"mobileService": "London Enhanced Mobile Broadband Core"}
    }
  }
]
```

**Capacity Distribution Calculation:**
- Central London (high priority): weight=3
- West London (medium priority): weight=2
- East London (medium priority): weight=2
- Total weight: 7
- Central: 3/7 = 42.9% → 2142 users, 4.3 Gbps
- West: 2/7 = 28.6% → 1429 users, 2.85 Gbps
- East: 2/7 = 28.6% → 1429 users, 2.85 Gbps
