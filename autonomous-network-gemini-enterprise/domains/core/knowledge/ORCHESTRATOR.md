# Core Domain Orchestrator - Decomposition Rules

## Overview

**Purpose:** Decompose high-level MobileServiceIntent into FiveGCore Kubernetes CR with auto-generated PLMN, network slices, NF deployment plan, and subscriber provisioning.

**Input:** MobileServiceIntent (TMF921)
- Service type (eMBB, URLLC, mMTC)
- Region and operator
- Capacity requirements (subscribers, throughput, concurrent users)
- Service level (speeds, latency, reliability)

**Output:** FiveGCore CR
- PLMN allocation (MCC/MNC)
- Network slice configuration (SST/SD, DNN, UE IP pools)
- Network function deployment plan (AMF, SMF, UPF, etc.)
- Subscriber provisioning (initial credentials)
- TAC assignment

**External Dependencies:**
- PLMN registry: operator + region → MCC/MNC
- TAC registry: region/zone → TAC values
- Device availability: query Ready Device containers in region
- MongoDB URI: core network database
- Resource pools: PLMN, slice SD, UE IP addresses, IMSI sequences

---

## Input Intent Schema

### Required Fields

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| `serviceName` | string | Service identifier | Unique |
| `operator` | string | Network operator name | Must exist in operator registry |
| `region` | string | Geographic region | Must exist in region registry |
| `serviceType` | enum | eMBB, URLLC, mMTC | |
| `capacity.totalSubscribers` | integer | Total subscriber capacity | > 0 |
| `capacity.peakConcurrentUsers` | integer | Peak concurrent users | ≤ totalSubscribers |
| `capacity.aggregateThroughput` | string | Total throughput | Parseable bandwidth string |
| `serviceLevel.downloadSpeed` | string | Download speed requirement | |
| `serviceLevel.latency` | string | Maximum latency | |
| `serviceLevel.reliability` | string | Availability SLA | e.g., "99.99%" |

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `capacity.initialSubscribers` | min(1000, totalSubscribers * 0.2) | Initial subscriber count |
| `capacity.growthRate` | null | Expected monthly growth rate |
| `serviceLevel.uploadSpeed` | Derived from serviceType | Upload speed requirement |
| `launchDate` | now() | Service launch date |

---

## Decomposition Algorithm

### Phase 1: Validation

```
1. Validate MobileServiceIntent schema
2. Validate operator exists in operator registry
3. Validate region exists in region registry
4. Validate serviceType is valid enum (eMBB, URLLC, mMTC)
5. Validate capacity constraints:
   - peakConcurrentUsers ≤ totalSubscribers
   - initialSubscribers ≤ totalSubscribers
```

### Phase 2: PLMN Allocation

```
FUNCTION allocate_plmn(operator, region):
  // Query PLMN registry for existing allocation
  plmn_entry = QUERY_PLMN_REGISTRY(operator=operator, region=region)
  
  IF plmn_entry:
    // Reuse existing PLMN
    LOG(f"Reusing PLMN {plmn_entry.mcc}/{plmn_entry.mnc} for {operator} in {region}")
    RETURN {
      "mcc": plmn_entry.mcc,
      "mnc": plmn_entry.mnc
    }
  
  ELSE:
    // Allocate new PLMN
    country = GET_COUNTRY_FROM_REGION(region)
    mcc = GET_MCC_FOR_COUNTRY(country)  // e.g., UK → 234, France → 208
    
    // Allocate MNC from country pool
    operator_id = GET_OPERATOR_ID(operator)
    existing_mncs = QUERY_PLMN_REGISTRY(mcc=mcc, operator=operator)
    
    IF existing_mncs:
      // Reuse operator's existing MNC in this country
      mnc = existing_mncs[0].mnc
    ELSE:
      // Allocate new MNC for operator
      mnc = ALLOCATE_MNC(mcc=mcc, operator=operator)
    
    // Register new PLMN allocation
    REGISTER_PLMN(operator, region, mcc, mnc)
    
    LOG(f"Allocated new PLMN {mcc}/{mnc} for {operator} in {region}")
    RETURN {
      "mcc": mcc,
      "mnc": mnc
    }
```

**PLMN Registry Example:**
```
{
  "TelecomCo UK": {
    "Greater London": {"mcc": "234", "mnc": "15"},
    "Scotland": {"mcc": "234", "mnc": "15"}  // Same MNC, different region
  },
  "MobileNet France": {
    "Île-de-France": {"mcc": "208", "mnc": "10"}
  }
}
```

### Phase 3: Slice Configuration

```
FUNCTION generate_slice_config(serviceType, capacity, serviceLevel):
  // Map service type to SST
  sst_mapping = {
    "eMBB": 1,   // Enhanced Mobile Broadband
    "URLLC": 2,  // Ultra-Reliable Low-Latency Communications
    "mMTC": 3    // Massive Machine-Type Communications
  }
  
  sst = sst_mapping[serviceType]
  
  // Allocate SD (Slice Differentiator)
  sd = ALLOCATE_SD()  // From global pool, 24-bit hex
  
  // DNN (Data Network Name) - default to "internet"
  dnn = "internet"
  
  // DNS configuration
  dns = {
    "ipv4": "8.8.8.8",  // Default public DNS
    "ipv6": null
  }
  
  // UE IP pool allocation
  pool_size = CALCULATE_POOL_SIZE(capacity.totalSubscribers)
  ue_pool = ALLOCATE_UE_IP_POOL(region, pool_size)
  
  // Static pool (10% of total)
  static_pool_size = pool_size / 10
  static_pool = ALLOCATE_UE_IP_POOL(region, static_pool_size, static=true)
  
  slice = {
    "sst": sst,
    "sd": sd,
    "dnnInfos": [{
      "dnn": dnn,
      "dns": dns,
      "pools": [{"cidr": ue_pool}],
      "staticPools": [{"cidr": static_pool}]
    }]
  }
  
  RETURN [slice]  // Return as array (support multi-slice in future)
```

**SD Allocation:**
```
FUNCTION allocate_sd():
  // SD is 24-bit value (000000-FFFFFF in hex)
  // Allocate sequentially from pool
  sd_seq = GET_NEXT_SEQUENCE("sd_seq", scope="global")
  sd = FORMAT_HEX(sd_seq, width=6)  // e.g., "010203"
  RETURN sd
```

**UE IP Pool Sizing:**
```
FUNCTION calculate_pool_size(total_subscribers):
  // Add 20% headroom
  required = total_subscribers * 1.2
  
  // Round up to next CIDR boundary
  IF required <= 256:
    RETURN "/24"  // 256 addresses
  ELSE IF required <= 65536:
    RETURN "/16"  // 65,536 addresses
  ELSE:
    RETURN "/8"   // 16,777,216 addresses
```

### Phase 4: Network Function Deployment Planning

```
FUNCTION plan_nf_deployment(serviceType, capacity, region):
  // Calculate required NF instances based on capacity
  
  // AMF (Access and Mobility Management Function)
  // Scaling: 1 AMF per 10,000 subscribers
  amf_count = MAX(1, CEIL(capacity.totalSubscribers / 10000))
  
  // SMF (Session Management Function)
  // Scaling: 1 SMF per 5,000 concurrent sessions
  smf_count = MAX(1, CEIL(capacity.peakConcurrentUsers / 5000))
  
  // UPF (User Plane Function)
  // Scaling: 1 UPF per 10 Gbps throughput
  throughput_gbps = PARSE_BANDWIDTH_TO_GBPS(capacity.aggregateThroughput)
  upf_count = MAX(1, CEIL(throughput_gbps / 10))
  
  // Control plane NFs (typically 1 instance each)
  control_plane_nfs = ["NRF", "UDR", "UDM", "AUSF", "PCF", "NSSF"]
  
  // Query available Device containers in region
  available_devices = QUERY_K8S(
    kind="Device",
    namespace="network",
    labelSelector=f"region={region}",
    fieldSelector="status.phase=Ready"
  )
  
  IF LEN(available_devices) < (amf_count + smf_count + upf_count + LEN(control_plane_nfs)):
    RAISE DependencyError(f"Insufficient Device containers in region {region}")
  
  // Assign containers
  nf_deployments = []
  device_index = 0
  
  // AMF instances
  FOR i IN RANGE(amf_count):
    device = available_devices[device_index]
    nf_deployments.append({
      "type": "AMF",
      "containerName": device.metadata.name,
      "config": {
        "tac": RESOLVE_TAC(region)
      }
    })
    device_index += 1
  
  // SMF instances
  FOR i IN RANGE(smf_count):
    device = available_devices[device_index]
    nf_deployments.append({
      "type": "SMF",
      "containerName": device.metadata.name
    })
    device_index += 1
  
  // UPF instances
  FOR i IN RANGE(upf_count):
    device = available_devices[device_index]
    nf_deployments.append({
      "type": "UPF",
      "containerName": device.metadata.name
    })
    device_index += 1
  
  // Control plane NFs
  FOR nf_type IN control_plane_nfs:
    device = available_devices[device_index]
    nf_deployments.append({
      "type": nf_type,
      "containerName": device.metadata.name
    })
    device_index += 1
  
  // WebUI
  device = available_devices[device_index]
  nf_deployments.append({
    "type": "WebUI",
    "containerName": device.metadata.name
  })
  
  RETURN nf_deployments
```

**TAC Resolution:**
```
FUNCTION resolve_tac(region):
  // Query TAC registry for region
  tac_entry = QUERY_TAC_REGISTRY(region=region)
  
  IF tac_entry:
    RETURN tac_entry.tac
  ELSE:
    // Allocate new TAC for region
    tac = ALLOCATE_TAC(region)
    REGISTER_TAC(region, tac)
    RETURN tac
```

**TAC Allocation:**
- Range: 1-16777215 (24-bit value)
- Typically region-specific (e.g., Greater London → TAC 1, Scotland → TAC 2)

### Phase 5: Subscriber Provisioning

```
FUNCTION generate_initial_subscribers(plmn, capacity, slice):
  subscribers = []
  
  initial_count = capacity.initialSubscribers OR MIN(1000, capacity.totalSubscribers * 0.2)
  
  FOR i IN RANGE(initial_count):
    // Generate IMSI
    imsi = GENERATE_IMSI(plmn.mcc, plmn.mnc, i)
    
    // Generate authentication credentials
    key = GENERATE_RANDOM_HEX(32)  // 128-bit K
    opc = GENERATE_RANDOM_HEX(32)  // 128-bit OPc
    
    // MSISDN (phone number) - optional
    msisdn = GENERATE_MSISDN(plmn.mcc, plmn.mnc, i)
    
    subscriber = {
      "imsi": imsi,
      "msisdn": msisdn,
      "key": key,
      "opc": opc,
      "apn": slice.dnnInfos[0].dnn,  // "internet"
      "sst": slice.sst,
      "sd": slice.sd
    }
    
    subscribers.append(subscriber)
  
  RETURN subscribers
```

**IMSI Generation:**
```
FUNCTION generate_imsi(mcc, mnc, sequence):
  // IMSI format: MCC (3 digits) + MNC (2-3 digits) + MSIN (remaining, up to 15 total)
  // Example: 234 (UK) + 15 (operator) + 0000000001 → 234150000000001
  
  msin_length = 15 - LEN(mcc) - LEN(mnc)
  msin = FORMAT_DECIMAL(sequence, width=msin_length, zero_pad=true)
  
  imsi = mcc + mnc + msin
  
  RETURN imsi
```

**MSISDN Generation:**
```
FUNCTION generate_msisdn(mcc, mnc, sequence):
  // MSISDN format: CC (country code) + NSN (national subscriber number)
  // Example: +44 (UK) 7700 900001
  
  country_code = GET_COUNTRY_CODE(mcc)  // e.g., MCC 234 → CC +44
  
  // Generate sequential NSN
  nsn = FORMAT_DECIMAL(9000000000 + sequence, width=10)
  
  msisdn = country_code + nsn
  
  RETURN msisdn
```

### Phase 6: FiveGCore CR Generation

```
FUNCTION generate_fivegcore_cr(serviceName, plmn, slices, subscribers, 
                                 nf_deployments, region):
  
  // MongoDB URI (from config or region-specific)
  mongodb_uri = GET_MONGODB_URI(region)
  
  cr = {
    "apiVersion": "google.dev/v1",
    "kind": "FiveGCore",
    "metadata": {
      "name": sanitize_cr_name(serviceName),
      "namespace": "network",
      "labels": {
        "region": region,
        "operator": operator
      }
    },
    "spec": {
      "image": "device:latest",  // Default free5gc image
      "mongodbUri": mongodb_uri,
      "plmnList": [{
        "mcc": plmn.mcc,
        "mnc": plmn.mnc
      }],
      "slices": slices,
      "subscribers": subscribers,
      "network_functions": nf_deployments
    }
  }
  
  RETURN cr
```

---

## Resource Pools

### PLMN Pool
```
{
  "allocations": {
    "TelecomCo UK": {
      "mcc": "234",
      "mnc": "15"
    }
  },
  "available_mncs": {
    "234": [10, 11, 12, 13, 14, 16, 17, ...]  // Available MNCs for UK
  }
}
```

### Slice SD Pool
```
{
  "current": 66051,  // Decimal value
  "current_hex": "010203",
  "range": [1, 16777215],  // 24-bit max
  "range_hex": ["000001", "FFFFFF"]
}
```

### UE IP Pool (per region)
```
{
  "Greater London": {
    "allocated": ["10.60.0.0/16", "10.61.0.0/24"],
    "available": ["10.62.0.0/16", "10.63.0.0/16", ...]
  }
}
```

### IMSI Sequence (per PLMN)
```
{
  "234-15": {
    "current": 1052,
    "range": [0, 9999999999]  // 10-digit MSIN
  }
}
```

### TAC Registry
```
{
  "Greater London": 1,
  "Scotland": 2,
  "Wales": 3,
  "Northern Ireland": 4
}
```

---

## Dependency Resolution

### Device Availability Check

```
FUNCTION query_available_devices(region, required_count):
  devices = QUERY_K8S(
    kind="Device",
    namespace="network",
    labelSelector=f"region={region}",
    fieldSelector="status.phase=Ready"
  )
  
  // Filter by name length ≤ 8 chars (veth naming constraint)
  devices = [d for d in devices if LEN(d.metadata.name) <= 8]
  
  IF LEN(devices) < required_count:
    RAISE DependencyError(
      f"Insufficient Device containers in region {region}: "
      f"required {required_count}, available {LEN(devices)}"
    )
  
  RETURN devices[:required_count]
```

### MongoDB URI Resolution

```
FUNCTION get_mongodb_uri(region):
  // Query MongoDB instance for region
  mongodb_uri = QUERY_CONFIG(f"mongodb.{region}.uri")
  
  IF NOT mongodb_uri:
    // Fallback to default
    mongodb_uri = GET_CONFIG("mongodb.default.uri")
  
  // Validate connectivity (optional)
  IF NOT TEST_MONGODB_CONNECTION(mongodb_uri):
    RAISE DependencyError(f"MongoDB unreachable at {mongodb_uri}")
  
  RETURN mongodb_uri
```

---

## Error Handling

### Validation Errors (400 Bad Request)

| Condition | Error Message |
|-----------|---------------|
| Unknown operator | `"Operator '{operator}' not found in operator registry"` |
| Unknown region | `"Region '{region}' not found in region registry"` |
| Invalid service type | `"serviceType must be eMBB, URLLC, or mMTC"` |
| Invalid capacity | `"peakConcurrentUsers ({value}) exceeds totalSubscribers ({total})"` |

### Dependency Errors (424 Failed Dependency)

| Condition | Error Message |
|-----------|---------------|
| Insufficient devices | `"Insufficient Device containers in region {region}: required {required}, available {available}"` |
| MongoDB unreachable | `"MongoDB unreachable at {uri}"` |
| PLMN pool exhausted | `"No available MNC values for MCC {mcc}"` |

### Resource Exhaustion (507 Insufficient Storage)

| Condition | Error Message |
|-----------|---------------|
| UE IP pool exhausted | `"UE IP address pool exhausted in region {region}"` |
| SD pool exhausted | `"Slice SD pool exhausted (max 16777215)"` |

---

## Example Transformation

### Input: MobileServiceIntent

```json
{
  "serviceName": "London 5G Mobile Broadband",
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
    "uploadSpeed": "50 Mbps typical",
    "latency": "< 20ms",
    "reliability": "99.99%"
  }
}
```

### Output: FiveGCore CR

```yaml
apiVersion: google.dev/v1
kind: FiveGCore
metadata:
  name: london-5g-mobile-broadband
  namespace: network
  labels:
    region: greater-london
    operator: telecomco-uk
spec:
  image: device:latest
  mongodbUri: "mongodb://10.102.1.21:27017"
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
          staticPools:
            - cidr: 10.60.100.0/24
  subscribers:
    - imsi: "234150000000001"
      msisdn: "447700900001"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      apn: internet
      sst: 1
      sd: "010203"
    # ... 9,999 more subscribers
  network_functions:
    - type: NRF
      containerName: nrf
    - type: AMF
      containerName: amf
      config:
        tac: 1
    - type: SMF
      containerName: smf
    - type: UPF
      containerName: upf
    - type: UDR
      containerName: udr
    - type: UDM
      containerName: udm
    - type: AUSF
      containerName: ausf
    - type: PCF
      containerName: pcf
    - type: NSSF
      containerName: nssf
    - type: WebUI
      containerName: webui
```

**Derived Values:**
- PLMN: `234/15` (allocated for TelecomCo UK in Greater London)
- SST: `1` (eMBB → SST 1)
- SD: `010203` (allocated from global SD pool)
- UE IP pool: `10.60.0.0/16` (allocated for 50k subscribers with headroom)
- TAC: `1` (Greater London regional TAC)
- IMSI range: `234150000000001` - `234150000010000` (10k subscribers)
- NF count: 10 (1 of each type, scalable based on capacity in future)
- Container assignment: Queried from available Ready Device CRs in Greater London region
