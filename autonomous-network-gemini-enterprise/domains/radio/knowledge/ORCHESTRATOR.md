# Radio Domain Orchestrator - Decomposition Rules

## Overview

**Purpose:** Decompose high-level CellSiteIntent into CellSite Kubernetes CR with auto-generated gNodeB IDs, TAC resolution, PLMN/slice inheritance from Core service, and test UE provisioning.

**Input:** CellSiteIntent (TMF921)
- Coverage zone and location
- Capacity requirements (users, throughput)
- gNodeB count (optional)
- Service link to Core mobile service

**Output:** CellSite CR(s)
- gNodeB configuration with allocated IDs
- TAC assignment
- PLMN inherited from Core service
- Slices inherited from Core service
- Container assignments
- Test UE credentials

**External Dependencies:**
- Core service (FiveGCore CR) must be Ready
- NetworkFunction CRs (AMF, UPF, SMF) must be Ready
- TAC registry: zone → TAC mapping
- gNodeB ID pool: zone-specific allocation
- Device availability: query Ready Device containers in zone

---

## Input Intent Schema

### Required Fields

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `siteName` | string | Cell site identifier | Unique |
| `coverageZone` | string | Geographic zone name | Must exist in zone registry |
| `capacity.peakUsers` | integer | Peak concurrent users | > 0 |
| `capacity.throughput` | string | Aggregate throughput | Parseable bandwidth string |
| `serviceLink.mobileService` | string | Core service name | FiveGCore CR must exist |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `location.latitude` | null | Latitude coordinate |
| `location.longitude` | null | Longitude coordinate |
| `location.address` | null | Physical address |
| `deployment.gNodeBCount` | Calculated from capacity | Number of gNodeBs to deploy |
| `deployment.sectorization` | "omni" | Antenna pattern (omni, 3-sector, 6-sector) |

---

## Decomposition Algorithm

### Phase 1: Validation

```
1. Validate CellSiteIntent schema
2. Validate coverageZone exists in zone registry
3. Validate serviceLink.mobileService exists (FiveGCore CR)
4. Query referenced Core service:
   - Verify FiveGCore CR status.phase = Ready
   - Extract PLMN (mcc, mnc)
   - Extract slices (sst, sd, dnnInfos)
   - Extract TAC from AMF config
5. Query NetworkFunction CRs:
   - Verify AMF CR exists and status.phase = Ready
   - Verify UPF CR exists and status.phase = Ready
   - Verify SMF CR exists and status.phase = Ready
```

### Phase 2: gNodeB Count Determination

```
FUNCTION determine_gnodeb_count(capacity, deployment):
  // If explicitly specified, use it
  IF deployment.gNodeBCount:
    RETURN deployment.gNodeBCount
  
  // Otherwise, calculate from capacity
  users_per_gnb = 250  // Default capacity per gNodeB (varies by service type)
  
  // Calculate based on peak users
  gnb_count_users = CEIL(capacity.peakUsers / users_per_gnb)
  
  // Calculate based on throughput (1 Gbps per gNodeB typical)
  throughput_gbps = PARSE_BANDWIDTH_TO_GBPS(capacity.throughput)
  throughput_per_gnb_gbps = 1.0  // Default throughput per gNodeB
  gnb_count_throughput = CEIL(throughput_gbps / throughput_per_gnb_gbps)
  
  // Take the maximum (limiting factor)
  gnb_count = MAX(gnb_count_users, gnb_count_throughput)
  
  // Minimum of 1
  gnb_count = MAX(1, gnb_count)
  
  RETURN gnb_count
```

**Calculation Examples:**
```
# Example 1: User-limited
peakUsers=500, throughput="200 Mbps"
→ gnb_count_users = CEIL(500/250) = 2
→ gnb_count_throughput = CEIL(0.2/1.0) = 1
→ gnb_count = MAX(2, 1) = 2

# Example 2: Throughput-limited
peakUsers=100, throughput="3 Gbps"
→ gnb_count_users = CEIL(100/250) = 1
→ gnb_count_throughput = CEIL(3/1.0) = 3
→ gnb_count = MAX(1, 3) = 3
```

### Phase 3: gNodeB ID Allocation

```
FUNCTION allocate_gnodeb_ids(coverageZone, count):
  // Query zone-specific gNodeB ID pool
  pool = QUERY_GNODEB_POOL(zone=coverageZone)
  
  IF pool.available_count < count:
    RAISE DependencyError(
      f"Insufficient gNodeB IDs in zone {coverageZone}: "
      f"required {count}, available {pool.available_count}"
    )
  
  // Allocate sequential IDs
  gnodeb_ids = []
  FOR i IN RANGE(count):
    gnb_id = ALLOCATE_GNODEB_ID(zone=coverageZone)
    gnodeb_ids.append(gnb_id)
  
  RETURN gnodeb_ids
```

**gNodeB ID Pool Structure:**
```
{
  "Greater London": {
    "Central London": {
      "current": 256,        // Decimal value
      "current_hex": "0x100",
      "range": [256, 511],   // Zone-specific range
      "range_hex": ["0x100", "0x1FF"]
    },
    "West London": {
      "current": 512,
      "current_hex": "0x200",
      "range": [512, 767],
      "range_hex": ["0x200", "0x2FF"]
    }
  }
}
```

**gNodeB ID Format:**
- Hex string (e.g., "0x100", "0x101", "0x102")
- Must be unique within PLMN
- Zone-specific ranges prevent collisions

### Phase 4: TAC Resolution

```
FUNCTION resolve_tac(coverageZone, core_service_name):
  // Query TAC registry for zone
  tac_entry = QUERY_TAC_REGISTRY(zone=coverageZone)
  
  IF tac_entry:
    zone_tac = tac_entry.tac
  ELSE:
    RAISE DependencyError(f"No TAC assigned to zone {coverageZone}")
  
  // Cross-validate with Core service
  core_cr = QUERY_K8S(kind="FiveGCore", name=core_service_name)
  amf_name = f"{core_service_name}-amf"
  amf_cr = QUERY_K8S(kind="NetworkFunction", name=amf_name)
  
  core_tac = amf_cr.spec.config.tac
  
  IF zone_tac != core_tac:
    RAISE ValidationError(
      f"TAC mismatch: zone {coverageZone} TAC={zone_tac}, "
      f"Core AMF TAC={core_tac}"
    )
  
  RETURN zone_tac
```

**TAC Registry Example:**
```
{
  "Greater London": {
    "Central London": 1,
    "West London": 1,      // Same TAC for entire region
    "East London": 1
  },
  "Scotland": {
    "Edinburgh": 2,
    "Glasgow": 2
  }
}
```

**Cross-Domain Validation:**
- Zone TAC must match Core AMF TAC
- If mismatch, reject intent with validation error
- TAC consistency ensures proper tracking area management

### Phase 5: PLMN and Slice Inheritance

```
FUNCTION inherit_core_config(core_service_name):
  // Query FiveGCore CR
  core_cr = QUERY_K8S(kind="FiveGCore", name=core_service_name)
  
  IF NOT core_cr:
    RAISE DependencyError(f"Core service {core_service_name} not found")
  
  IF core_cr.status.phase != "Ready":
    RAISE DependencyError(
      f"Core service {core_service_name} not Ready (phase={core_cr.status.phase})"
    )
  
  // Extract PLMN
  plmn = core_cr.spec.plmnList[0]
  mcc = plmn.mcc
  mnc = plmn.mnc
  
  // Extract slices
  slices = []
  FOR core_slice IN core_cr.spec.slices:
    slice = {
      "sst": core_slice.sst,
      "sd": core_slice.sd
    }
    slices.append(slice)
  
  // Extract DNN (from first slice)
  dnn = core_cr.spec.slices[0].dnnInfos[0].dnn
  
  RETURN {
    "mcc": mcc,
    "mnc": mnc,
    "slices": slices,
    "dnn": dnn
  }
```

**PLMN Inheritance:**
- Inherit from Core service's `spec.plmnList[0]`
- All gNodeBs in this cell site use same PLMN
- Ensures UEs can register with Core network

**Slice Inheritance:**
- Copy all slices from Core service
- Format: `[{"sst": 1, "sd": "010203"}, ...]`
- gNodeB advertises these slices to UEs

### Phase 6: Container Assignment

```
FUNCTION assign_containers(coverageZone, gnodeb_count):
  // Query available Device containers in zone
  available_devices = QUERY_K8S(
    kind="Device",
    namespace="network",
    labelSelector=f"zone={coverageZone}",
    fieldSelector="status.phase=Ready"
  )
  
  // Filter by name length (≤8 chars for veth naming constraint)
  available_devices = [d for d in available_devices if LEN(d.metadata.name) <= 8]
  
  IF LEN(available_devices) < gnodeb_count:
    RAISE DependencyError(
      f"Insufficient Device containers in zone {coverageZone}: "
      f"required {gnodeb_count}, available {LEN(available_devices)}"
    )
  
  // Assign containers
  containers = []
  FOR i IN RANGE(gnodeb_count):
    container = available_devices[i].metadata.name
    containers.append(container)
  
  RETURN containers
```

**Container Naming Constraint:**
- Device CR name must be ≤8 characters
- Reason: veth interface naming limit (e.g., `veth-cell1a`)
- Reject containers with longer names

### Phase 7: Test UE Generation

```
FUNCTION generate_test_ues(core_service_name, gnodeb_count, slices, dnn):
  // Query Core service subscribers
  core_cr = QUERY_K8S(kind="FiveGCore", name=core_service_name)
  core_subscribers = core_cr.spec.subscribers
  
  // Generate 10-20 UEs per gNodeB
  ues_per_gnb = 10
  total_ues = gnodeb_count * ues_per_gnb
  
  IF total_ues > LEN(core_subscribers):
    LOG(f"Warning: Requested {total_ues} UEs but only {LEN(core_subscribers)} subscribers available")
    total_ues = LEN(core_subscribers)
  
  // Select subscribers from Core pool (round-robin)
  ues = []
  FOR i IN RANGE(total_ues):
    subscriber = core_subscribers[i % LEN(core_subscribers)]
    
    ue = {
      "imsi": subscriber.imsi,
      "key": subscriber.key,
      "opc": subscriber.opc,
      "apn": dnn,  // Inherited from Core slice
      "sst": slices[0].sst,
      "sd": slices[0].sd
    }
    
    ues.append(ue)
  
  RETURN ues
```

**UE Credential Inheritance:**
- IMSI, K, OPc values from Core subscriber pool
- APN matches Core DNN (e.g., "internet")
- SST/SD from Core slice configuration
- Ensures UEs can authenticate with Core network

**UE Distribution:**
- 10 UEs per gNodeB (default)
- Round-robin assignment to gNodeBs
- Example: 3 gNodeBs → 30 UEs total (10 per gNodeB)

### Phase 8: NetworkFunction Name Resolution

```
FUNCTION resolve_network_functions(core_service_name):
  // Derive NetworkFunction CR names from Core service name
  amf_name = f"{core_service_name}-amf"
  upf_name = f"{core_service_name}-upf"
  smf_name = f"{core_service_name}-smf"
  
  // Validate existence and readiness
  FOR nf_name IN [amf_name, upf_name, smf_name]:
    nf_cr = QUERY_K8S(kind="NetworkFunction", name=nf_name)
    
    IF NOT nf_cr:
      RAISE DependencyError(f"NetworkFunction {nf_name} not found")
    
    IF nf_cr.status.phase != "Ready":
      RAISE DependencyError(
        f"NetworkFunction {nf_name} not Ready (phase={nf_cr.status.phase})"
      )
  
  RETURN {
    "amf_name": amf_name,
    "upf_name": upf_name,
    "smf_name": smf_name
  }
```

**Naming Convention:**
- AMF: `{coreId}-amf` (e.g., "free5gc-core-amf")
- UPF: `{coreId}-upf` (e.g., "free5gc-core-upf")
- SMF: `{coreId}-smf` (e.g., "free5gc-core-smf")

### Phase 9: CellSite CR Generation

```
FUNCTION generate_cellsite_cr(siteName, gnodeb_configs, ues, core_config, nf_names):
  
  crs = []
  
  FOR i, gnb_config IN ENUMERATE(gnodeb_configs):
    // Generate unique CR name for each gNodeB
    cr_name = sanitize_cr_name(f"{siteName}-gnb{i+1}")
    
    // Assign UEs to this gNodeB (round-robin)
    gnb_ues = [ue for idx, ue in ENUMERATE(ues) if idx % LEN(gnodeb_configs) == i]
    
    cr = {
      "apiVersion": "google.dev/v1",
      "kind": "CellSite",
      "metadata": {
        "name": cr_name,
        "namespace": "network",
        "labels": {
          "zone": coverageZone,
          "site": siteName
        }
      },
      "spec": {
        "container_name": gnb_config.container_name,
        "gnb": {
          "mcc": core_config.mcc,
          "mnc": core_config.mnc,
          "tac": gnb_config.tac,
          "gnbId": gnb_config.gnbId,
          "amf_name": nf_names.amf_name,
          "upf_name": nf_names.upf_name,
          "smf_name": nf_names.smf_name,
          "slices": core_config.slices
        },
        "ues": gnb_ues
      }
    }
    
    crs.append(cr)
  
  RETURN crs
```

**Multiple gNodeBs → Multiple CellSite CRs:**
- If intent specifies 3 gNodeBs, create 3 separate CellSite CRs
- Each CR manages one gNodeB and its UEs
- UEs distributed round-robin across gNodeBs

---

## Resource Pools

### gNodeB ID Pool (per zone)
```
{
  "Central London": {
    "current": 256,
    "current_hex": "0x100",
    "range": [256, 511],
    "allocated": ["0x100", "0x101", "0x102"]
  }
}
```

### TAC Registry (per zone)
```
{
  "Central London": 1,
  "West London": 1,
  "East London": 1,
  "Edinburgh": 2
}
```

### Device Container Pool (per zone)
```
{
  "Central London": {
    "available": ["cell1a", "cell1b", "cell1c", "cell2a"],
    "allocated": ["cell1a", "cell1b"]
  }
}
```

---

## Cross-Domain Validation

### PLMN Consistency

```
FUNCTION validate_plmn(cell_site_intent, core_service):
  core_mcc = core_service.spec.plmnList[0].mcc
  core_mnc = core_service.spec.plmnList[0].mnc
  
  // Radio intent doesn't specify PLMN (inherited)
  // Validation happens during inheritance phase
  
  LOG(f"Using PLMN from Core service: {core_mcc}/{core_mnc}")
```

### TAC Consistency

```
FUNCTION validate_tac(zone_tac, core_service):
  amf_name = f"{core_service.metadata.name}-amf"
  amf_cr = QUERY_K8S(kind="NetworkFunction", name=amf_name)
  
  core_tac = amf_cr.spec.config.tac
  
  IF zone_tac != core_tac:
    RAISE ValidationError(
      f"TAC mismatch: zone TAC={zone_tac}, Core AMF TAC={core_tac}. "
      f"Cell sites in this zone cannot connect to this Core service."
    )
```

### Slice Consistency

```
FUNCTION validate_slices(core_slices):
  // All Core slices are inherited
  // No validation needed beyond existence
  
  IF NOT core_slices:
    RAISE ValidationError("Core service has no slices configured")
  
  LOG(f"Inheriting {LEN(core_slices)} slices from Core service")
```

### Subscriber Registration

```
FUNCTION validate_subscribers(ues, core_service):
  core_imsis = SET([sub.imsi for sub in core_service.spec.subscribers])
  
  FOR ue IN ues:
    IF ue.imsi NOT IN core_imsis:
      RAISE ValidationError(
        f"UE IMSI {ue.imsi} not registered in Core service {core_service.metadata.name}"
      )
```

### DNN/APN Consistency

```
FUNCTION validate_dnn(ues, core_slices):
  valid_dnns = SET()
  FOR slice IN core_slices:
    FOR dnn_info IN slice.dnnInfos:
      valid_dnns.add(dnn_info.dnn)
  
  FOR ue IN ues:
    IF ue.apn NOT IN valid_dnns:
      RAISE ValidationError(
        f"UE APN {ue.apn} not found in Core service DNNs {valid_dnns}"
      )
```

---

## Dependency Resolution

### Core Service Dependency Check

```
FUNCTION check_core_service_ready(core_service_name):
  // Check FiveGCore CR
  core_cr = QUERY_K8S(kind="FiveGCore", name=core_service_name)
  
  IF NOT core_cr:
    RAISE DependencyError(f"Core service {core_service_name} not found")
  
  IF core_cr.status.phase != "Ready":
    RAISE DependencyError(
      f"Core service {core_service_name} not Ready "
      f"(current phase: {core_cr.status.phase})"
    )
  
  // Check NetworkFunction CRs
  required_nfs = ["amf", "upf", "smf"]
  FOR nf_type IN required_nfs:
    nf_name = f"{core_service_name}-{nf_type}"
    nf_cr = QUERY_K8S(kind="NetworkFunction", name=nf_name)
    
    IF NOT nf_cr:
      RAISE DependencyError(f"NetworkFunction {nf_name} not found")
    
    IF nf_cr.status.phase != "Ready":
      RAISE DependencyError(
        f"NetworkFunction {nf_name} not Ready "
        f"(current phase: {nf_cr.status.phase})"
      )
```

### Zone Infrastructure Check

```
FUNCTION check_zone_infrastructure(coverageZone, required_containers):
  // Check TAC registry
  tac_entry = QUERY_TAC_REGISTRY(zone=coverageZone)
  IF NOT tac_entry:
    RAISE DependencyError(f"No TAC assigned to zone {coverageZone}")
  
  // Check gNodeB ID pool
  gnb_pool = QUERY_GNODEB_POOL(zone=coverageZone)
  IF gnb_pool.available_count < required_containers:
    RAISE DependencyError(
      f"Insufficient gNodeB IDs in zone {coverageZone}: "
      f"required {required_containers}, available {gnb_pool.available_count}"
    )
  
  // Check Device containers
  available_devices = QUERY_K8S(
    kind="Device",
    labelSelector=f"zone={coverageZone}",
    fieldSelector="status.phase=Ready"
  )
  
  // Filter by name length
  available_devices = [d for d in available_devices if LEN(d.metadata.name) <= 8]
  
  IF LEN(available_devices) < required_containers:
    RAISE DependencyError(
      f"Insufficient Device containers in zone {coverageZone}: "
      f"required {required_containers}, available {LEN(available_devices)}"
    )
```

---

## Error Handling

### Validation Errors (400 Bad Request)

| Condition | Error Message |
|-----------|---------------|
| Unknown coverage zone | `"Coverage zone '{coverageZone}' not found in zone registry"` |
| Invalid capacity | `"peakUsers must be > 0"` |
| Invalid service link | `"serviceLink.mobileService is required"` |

### Dependency Errors (424 Failed Dependency)

| Condition | Error Message |
|-----------|---------------|
| Core service not found | `"Core service '{serviceName}' not found"` |
| Core service not ready | `"Core service '{serviceName}' not Ready (phase={phase})"` |
| NetworkFunction not ready | `"NetworkFunction '{nfName}' not Ready (phase={phase})"` |
| TAC not assigned | `"No TAC assigned to zone '{zone}'"` |
| Insufficient containers | `"Insufficient Device containers in zone '{zone}': required {required}, available {available}"` |
| Insufficient gNodeB IDs | `"Insufficient gNodeB IDs in zone '{zone}': required {required}, available {available}"` |

### Cross-Domain Validation Errors (422 Unprocessable Entity)

| Condition | Error Message |
|-----------|---------------|
| TAC mismatch | `"TAC mismatch: zone TAC={zoneTac}, Core AMF TAC={coreTac}"` |
| IMSI not registered | `"UE IMSI {imsi} not registered in Core service {serviceName}"` |
| APN not configured | `"UE APN {apn} not found in Core service DNNs {validDnns}"` |

---

## Example Transformation

### Input: CellSiteIntent

```json
{
  "siteName": "Central London Cell Site A",
  "coverageZone": "Central London",
  "location": {
    "latitude": 51.5074,
    "longitude": -0.1278,
    "address": "123 High Street, London"
  },
  "capacity": {
    "peakUsers": 500,
    "throughput": "500 Mbps"
  },
  "deployment": {
    "gNodeBCount": 3
  },
  "serviceLink": {
    "mobileService": "london-5g-mobile-broadband"
  }
}
```

### Intermediate: Query Core Service

```yaml
# FiveGCore CR: london-5g-mobile-broadband
apiVersion: google.dev/v1
kind: FiveGCore
metadata:
  name: london-5g-mobile-broadband
  namespace: network
spec:
  plmnList:
    - mcc: "234"
      mnc: "15"
  slices:
    - sst: 1
      sd: "010203"
      dnnInfos:
        - dnn: internet
          dns:
            ipv4: 8.8.8.8
          pools:
            - cidr: 10.60.0.0/16
  subscribers:
    - imsi: "234150000000001"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      # ... 9,999 more subscribers
status:
  phase: Ready

# NetworkFunction CR: london-5g-mobile-broadband-amf
apiVersion: google.dev/v1
kind: NetworkFunction
metadata:
  name: london-5g-mobile-broadband-amf
spec:
  type: AMF
  config:
    tac: 1
status:
  phase: Ready
```

### Output: CellSite CRs (3 total - one per gNodeB)

**CellSite CR 1:**
```yaml
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: central-london-cell-site-a-gnb1
  namespace: network
  labels:
    zone: central-london
    site: central-london-cell-site-a
spec:
  container_name: cell1a
  gnb:
    mcc: "234"
    mnc: "15"
    tac: 1
    gnbId: "0x100"
    amf_name: london-5g-mobile-broadband-amf
    upf_name: london-5g-mobile-broadband-upf
    smf_name: london-5g-mobile-broadband-smf
    slices:
      - sst: 1
        sd: "010203"
  ues:
    - imsi: "234150000000001"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      apn: internet
      sst: 1
      sd: "010203"
    - imsi: "234150000000002"
      key: "8BAF473F2F8FD09487CCCBD7097C6862"
      opc: "8E27B6AF0E692E750F32667A3B14605D"
      apn: internet
      sst: 1
      sd: "010203"
    # ... 8 more UEs (10 total per gNodeB)
```

**CellSite CR 2:**
```yaml
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: central-london-cell-site-a-gnb2
  namespace: network
spec:
  container_name: cell1b
  gnb:
    mcc: "234"
    mnc: "15"
    tac: 1
    gnbId: "0x101"  # Next sequential ID
    amf_name: london-5g-mobile-broadband-amf
    upf_name: london-5g-mobile-broadband-upf
    smf_name: london-5g-mobile-broadband-smf
    slices:
      - sst: 1
        sd: "010203"
  ues:
    - imsi: "234150000000011"
      # ... 10 UEs for this gNodeB
```

**CellSite CR 3:**
```yaml
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: central-london-cell-site-a-gnb3
  namespace: network
spec:
  container_name: cell1c
  gnb:
    mcc: "234"
    mnc: "15"
    tac: 1
    gnbId: "0x102"  # Next sequential ID
    amf_name: london-5g-mobile-broadband-amf
    upf_name: london-5g-mobile-broadband-upf
    smf_name: london-5g-mobile-broadband-smf
    slices:
      - sst: 1
        sd: "010203"
  ues:
    - imsi: "234150000000021"
      # ... 10 UEs for this gNodeB
```

**Derived Values:**
- PLMN: `234/15` (inherited from Core service)
- TAC: `1` (resolved from Central London zone, validated against Core AMF)
- gNodeB IDs: `0x100`, `0x101`, `0x102` (allocated from Central London pool)
- NetworkFunction names: `london-5g-mobile-broadband-{amf,upf,smf}` (derived from Core service name)
- Slices: `[{"sst": 1, "sd": "010203"}]` (inherited from Core service)
- UEs: 30 total (10 per gNodeB, credentials from Core subscriber pool)
- Containers: `cell1a`, `cell1b`, `cell1c` (assigned from available Ready devices in zone)

---

## Complete Transformation Example

### Scenario: Deploy 3 cell sites for London eMBB service

**Prerequisites:**
1. Core service "london-5g-mobile-broadband" is Ready
2. NetworkFunctions (AMF, UPF, SMF) are Ready
3. TAC registry has Central London → TAC 1
4. gNodeB ID pool for Central London has IDs available (0x100-0x1FF)
5. Device containers available: cell1a, cell1b, cell1c, cell2a, cell2b, cell2c, cell3a, cell3b

**Input: 3 CellSiteIntents (one per zone)**

**Intent 1: Central London (high priority)**
```json
{
  "siteName": "Central London Cell Sites",
  "coverageZone": "Central London",
  "capacity": {"peakUsers": 2000, "throughput": "4 Gbps"},
  "deployment": {"gNodeBCount": 3},
  "serviceLink": {"mobileService": "london-5g-mobile-broadband"}
}
```

**Intent 2: West London (medium priority)**
```json
{
  "siteName": "West London Cell Sites",
  "coverageZone": "West London",
  "capacity": {"peakUsers": 1500, "throughput": "3 Gbps"},
  "deployment": {"gNodeBCount": 2},
  "serviceLink": {"mobileService": "london-5g-mobile-broadband"}
}
```

**Intent 3: East London (medium priority)**
```json
{
  "siteName": "East London Cell Sites",
  "coverageZone": "East London",
  "capacity": {"peakUsers": 1500, "throughput": "3 Gbps"},
  "serviceLink": {"mobileService": "london-5g-mobile-broadband"}
  // gNodeBCount not specified - orchestrator calculates
}
```

**Orchestrator Processing:**

1. **Intent 1 - Central London:**
   - gNodeB count: 3 (specified)
   - Allocate gNodeB IDs: 0x100, 0x101, 0x102
   - Assign containers: cell1a, cell1b, cell1c
   - Generate 30 UEs (10 per gNodeB)
   - Create 3 CellSite CRs

2. **Intent 2 - West London:**
   - gNodeB count: 2 (specified)
   - Allocate gNodeB IDs: 0x200, 0x201 (West London zone pool)
   - Assign containers: cell2a, cell2b
   - Generate 20 UEs (10 per gNodeB)
   - Create 2 CellSite CRs

3. **Intent 3 - East London:**
   - gNodeB count: Calculate from capacity
     - peakUsers: 1500 / 250 = 6 gNodeBs
     - throughput: 3 Gbps / 1 Gbps = 3 gNodeBs
     - MAX(6, 3) = 6 gNodeBs
   - Allocate gNodeB IDs: 0x300-0x305 (East London zone pool)
   - Assign containers: cell3a, cell3b, cell3c, cell3d, cell3e, cell3f
   - Generate 60 UEs (10 per gNodeB)
   - Create 6 CellSite CRs

**Total Output:**
- 11 CellSite CRs (3 + 2 + 6)
- 110 test UEs (30 + 20 + 60)
- All inherit PLMN 234/15 from Core service
- All inherit slice SST=1, SD=010203
- All connect to same AMF/UPF/SMF NetworkFunctions
