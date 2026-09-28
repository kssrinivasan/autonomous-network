# Core Domain

**Source:** `controller/src/core/`  
**CRDs:** `controller/config/core/`

The Core domain manages 5G core network functions using the free5gc open-source implementation. It provides lifecycle management for the full 5G Service-Based Architecture (SBA) stack.

## Domain Overview

The Core domain provides:
- **5G core orchestration** via `FiveGCore` (parent resource that spawns all NFs)
- **Individual NF lifecycle** via `NetworkFunction` (AMF, SMF, UPF, NRF, NSSF, etc.)

This domain runs 5G NFs as Kubernetes pods in GKE, with service mesh networking and persistent configuration.

### 5G Core Architecture

```
FiveGCore (parent CRD)
  ├── NetworkFunction: AMF (Access and Mobility Management)
  ├── NetworkFunction: SMF (Session Management)
  ├── NetworkFunction: UPF (User Plane Function)
  ├── NetworkFunction: NRF (NF Repository Function)
  ├── NetworkFunction: NSSF (Network Slice Selection Function)
  ├── NetworkFunction: AUSF (Authentication Server Function)
  ├── NetworkFunction: UDM (Unified Data Management)
  ├── NetworkFunction: UDR (Unified Data Repository)
  ├── NetworkFunction: PCF (Policy Control Function)
  └── NetworkFunction: WebUI (Management interface)
```

The `FiveGCore` resource orchestrates the full stack as child `NetworkFunction` resources, managing startup order, configuration injection, and service dependencies.

---

## FiveGCore

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get core` or `kubectl get fivegcores`

### Summary

The `FiveGCore` resource declares a complete 5G core network instance. When created, the operator:

1. Validates the core configuration (MCC/MNC, slicing, network instances).
2. Generates one `NetworkFunction` child resource per NF (AMF, SMF, UPF, etc.).
3. Injects configuration into each NF (connection strings, service endpoints, slicing policies).
4. Waits for NFs to reach `Ready` in dependency order (NRF first, then control plane, then user plane).

On deletion, child `NetworkFunction` resources are garbage-collected automatically.

### Key spec fields

| Field | Description |
|---|---|
| `spec.plmn` | Public Land Mobile Network identity: `mcc` (e.g. "999"), `mnc` (e.g. "70"). |
| `spec.network_name` | Network name (e.g. "free5GC"). |
| `spec.network_instances[]` | Data Network Names (DNNs): `name` (e.g. "internet"), `cidr` (UE IP pool). |
| `spec.slices[]` | Network slicing configuration: `sst` (Slice/Service Type), `sd` (Slice Differentiator), `dnn[]` (list of DNNs for this slice). |
| `spec.nfs` | Per-NF configuration overrides (image, replicas, resources). |

### Lifecycle phases

`Pending` → `Creating` → `Running` → `Ready` / `Failed`

### Example

```yaml
apiVersion: google.dev/v1
kind: FiveGCore
metadata:
  name: free5gc-1
  namespace: network
spec:
  plmn:
    mcc: "999"
    mnc: "70"
  network_name: "free5GC"
  network_instances:
    - name: "internet"
      cidr: "10.60.0.0/16"
  slices:
    - sst: 1  # eMBB
      sd: "010203"
      dnn: ["internet"]
  nfs:
    amf:
      replicas: 1
    smf:
      replicas: 1
    upf:
      replicas: 1
```

### Lifecycle Handler

**Path:** `controller/src/core/core/lifecycle.py`

Key behaviors:
- **CREATE:** Validates spec → generates `NetworkFunction` children with injected config → monitors readiness
- **UPDATE:** Regenerates children on spec changes (e.g., adding a slice)
- **DELETE:** Garbage-collects children (via `kopf.adopt()`)

---

## NetworkFunction

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get nf` or `kubectl get networkfunctions`

### Summary

Represents a single 5G network function (AMF, SMF, UPF, NRF, NSSF, AUSF, UDM, UDR, PCF, or WebUI) running as a Kubernetes pod. `NetworkFunction` resources are usually created automatically by `FiveGCore`, but can be created directly for standalone NF testing.

The operator:
- Waits for dependencies (e.g., SMF waits for NRF + UDM).
- Creates a Kubernetes Deployment + Service for the NF.
- Injects configuration from the parent `FiveGCore` (PLMN, slicing, DNN, service endpoints).
- Monitors pod health and updates status.
- Syncs NF state to Cloud Spanner for graph analysis.

### Key spec fields

| Field | Description |
|---|---|
| `spec.nf_type` | Network function type: `AMF`, `SMF`, `UPF`, `NRF`, `NSSF`, `AUSF`, `UDM`, `UDR`, `PCF`, `WEBUI`. |
| `spec.image` | Container image (default: `free5gc/<nf>:latest`). |
| `spec.replicas` | Number of replicas (default 1, typically 1 for control plane NFs). |
| `spec.config` | NF-specific configuration (injected from parent `FiveGCore` or overridden here). |
| `spec.dependencies[]` | List of other `NetworkFunction` names that must be `Ready` before this NF starts. |
| `spec.resources` | Kubernetes resource requests/limits (CPU, memory). |

### Status phases

`Pending` → `WaitingForDependencies` → `Creating` → `Running` / `Failed`

The status exposes:
- `pod_name` — Kubernetes pod name
- `service_ip` — Cluster IP of the Kubernetes service
- `ready_replicas` — Number of ready replicas
- `endpoints` — Service endpoints (e.g., SBI URL for control plane NFs)

### Example

```yaml
apiVersion: google.dev/v1
kind: NetworkFunction
metadata:
  name: amf-1
  namespace: network
spec:
  nf_type: AMF
  image: "free5gc/amf:v3.3.0"
  replicas: 1
  dependencies:
    - nrf-1
  config:
    plmn:
      mcc: "999"
      mnc: "70"
    sbi:
      scheme: http
      registerIPv4: amf-1
      bindingIPv4: 0.0.0.0
      port: 8000
    ngap:
      bindingIPv4: 0.0.0.0
      port: 38412
```

### Lifecycle Handler

**Path:** `controller/src/core/xnf/lifecycle.py`

Key behaviors:
- **CREATE:** Waits for dependencies → creates Deployment + Service → injects config via ConfigMap → monitors pod readiness
- **UPDATE:** Rolling update on config or image changes
- **DELETE:** Deletes Deployment + Service
- **TIMER (60s):** Checks pod health, updates status, syncs to Spanner on changes

---

## Network Function Dependencies

The operator enforces these dependencies automatically:

| NF | Depends On |
|----|------------|
| AMF | NRF |
| SMF | NRF, UDM, PCF |
| UPF | NRF (registers with NRF) |
| AUSF | NRF, UDM |
| UDM | NRF, UDR |
| UDR | NRF |
| PCF | NRF, UDR |
| NSSF | NRF |
| WebUI | NRF, UDR |

NRF (NF Repository Function) must be created and `Ready` first, as all other NFs register with it via the Service-Based Interface (SBI).

---

## Integration with Radio Domain

The Core domain integrates with the Radio domain (`CellSite` CRDs) via the AMF:

```
CellSite (Radio domain, UERANSIM gNodeB)
  └── NGAP connection to AMF
        └── NetworkFunction: AMF (Core domain)
              └── NAS signaling with UEs
```

When a `CellSite` is created, it discovers the AMF endpoint via Kubernetes service DNS and establishes an NG-AP connection.

---

## Testing

```bash
# Deploy a 5G core instance
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: FiveGCore
metadata:
  name: free5gc-1
  namespace: network
spec:
  plmn:
    mcc: "999"
    mnc: "70"
  network_name: "free5GC"
  network_instances:
    - name: "internet"
      cidr: "10.60.0.0/16"
  slices:
    - sst: 1
      sd: "010203"
      dnn: ["internet"]
EOF

# Wait for core to be ready
kubectl wait --for=condition=ready core/free5gc-1 --timeout=300s

# Check all NFs
kubectl get nf

# Check a specific NF's logs
kubectl logs -l app=amf-1

# Access the WebUI
kubectl port-forward svc/webui-1 5000:5000
# Open http://localhost:5000
```

## Common Patterns

### Multi-Slice Core

```yaml
apiVersion: google.dev/v1
kind: FiveGCore
metadata:
  name: free5gc-multislice
spec:
  plmn:
    mcc: "999"
    mnc: "70"
  network_instances:
    - name: "internet"
      cidr: "10.60.0.0/16"
    - name: "iot"
      cidr: "10.61.0.0/16"
  slices:
    - sst: 1  # eMBB (enhanced Mobile Broadband)
      sd: "010203"
      dnn: ["internet"]
    - sst: 2  # URLLC (Ultra-Reliable Low Latency)
      sd: "112233"
      dnn: ["internet"]
    - sst: 3  # mMTC (massive Machine Type Communications)
      sd: "334455"
      dnn: ["iot"]
```

### Edge UPF Deployment

For edge computing scenarios, deploy multiple UPFs:

```yaml
nfs:
  upf:
    replicas: 1  # Central UPF
  upf-edge-1:
    nf_type: UPF
    replicas: 1
    config:
      dnn: "internet"
      cidr: "10.60.0.0/16"
      location: "edge-site-1"
```

## Configuration Reference

Each NF's configuration follows the free5gc schema. See:
- **free5gc documentation:** https://free5gc.org/
- **Sample configs:** `examples/controller/multidomain/default/`
- **Container images:** `ghcr.io/free5gc/<nf>:v3.3.0` (or custom builds)
