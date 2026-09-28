# Radio Domain

**Source:** `controller/src/radio/`  
**CRDs:** `controller/config/radio/`

The Radio domain manages Radio Access Network (RAN) simulation using UERANSIM, an open-source 5G UE and RAN (gNodeB) simulator. This domain provides realistic 5G radio network testing without physical hardware.

## Domain Overview

The Radio domain provides:
- **gNodeB simulation** (5G base station) with NG-AP and NGAP connections to the 5G core
- **UE simulation** (User Equipment / mobile devices) with NAS signaling and data plane connectivity
- **Per-site lifecycle management** via the `CellSite` CRD

This domain integrates with the Core domain's AMF and UPF network functions to provide end-to-end 5G connectivity.

### RAN Architecture

```
CellSite (Radio domain)
  ├── gNodeB (UERANSIM)
  │     └── NG-AP → NetworkFunction: AMF (Core domain)
  └── UE[] (UERANSIM)
        ├── NAS → AMF (control plane)
        └── GTP-U → UPF (user plane data)
```

---

## CellSite

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get cellsite` or `kubectl get cellsites`

### Summary

The `CellSite` resource represents a single 5G cell site (gNodeB) with one or more attached UEs. When created, the operator:

1. Waits for the referenced `FiveGCore` (Core domain) to reach `Ready` status.
2. Creates a Kubernetes Deployment for the gNodeB, configured to connect to the AMF.
3. Waits for the gNodeB to establish NG-AP connection with the AMF.
4. Creates Kubernetes pods for each UE, which perform:
   - **Registration** with the AMF (NAS signaling)
   - **PDU session establishment** (data plane connection via UPF)
   - **Data connectivity** (UE receives an IP address from the DNN pool)

The operator monitors gNodeB and UE state, exposing connection status and metrics.

### Key spec fields

| Field | Description |
|---|---|
| `spec.gnb_name` | gNodeB name / identifier (e.g. "gnb-1"). |
| `spec.mcc` | Mobile Country Code (must match the 5G core's PLMN). |
| `spec.mnc` | Mobile Network Code (must match the 5G core's PLMN). |
| `spec.tac` | Tracking Area Code (e.g. 1). |
| `spec.gnb_id` | gNodeB ID (unique integer). |
| `spec.nci` | NR Cell Identity (hexadecimal, e.g. "0x000000010"). |
| `spec.amf_endpoint` | AMF service endpoint (Kubernetes service name or IP:port, e.g. "amf-1:38412"). |
| `spec.link_ip` | gNodeB link IP address (used for NG-AP connection). |
| `spec.ngap_ip` | gNodeB NGAP bind IP (typically "0.0.0.0"). |
| `spec.gtp_ip` | gNodeB GTP-U bind IP (for user plane traffic). |
| `spec.ues[]` | List of UE configurations (see below). |
| `spec.location` | Physical location metadata: `latitude`, `longitude`, `city`, `country`, `site`. |

**UE configuration fields** (in `spec.ues[]`):

| Field | Description |
|---|---|
| `imsi` | International Mobile Subscriber Identity (e.g. "imsi-999700000000001"). |
| `supi` | Subscription Permanent Identifier (typically same as IMSI). |
| `key` | UE authentication key (K). |
| `opc` | Operator code (OPc). |
| `slice.sst` | Slice/Service Type (must match a slice in the 5G core). |
| `slice.sd` | Slice Differentiator (optional). |
| `dnn` | Data Network Name (must match a DNN in the 5G core). |

### Status phases

`Pending` → `CreatingGnb` → `GnbRunning` → `AttachingUEs` → `Ready` / `Failed`

The status exposes:
- `gnb_pod_name` — Kubernetes pod name for the gNodeB
- `gnb_connection_status` — NG-AP connection state (`Connected` / `Disconnected`)
- `ue_status[]` — Per-UE state:
  - `imsi` — UE identifier
  - `pod_name` — Kubernetes pod name
  - `registration_status` — NAS registration state (`Registered` / `Deregistered`)
  - `pdu_session_status` — PDU session state (`Established` / `Released`)
  - `ip_address` — UE's assigned IP address from the DNN pool

### Example

```yaml
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: site-1
  namespace: network
spec:
  gnb_name: "gnb-1"
  mcc: "999"
  mnc: "70"
  tac: 1
  gnb_id: 1
  nci: "0x000000010"
  amf_endpoint: "amf-1:38412"
  link_ip: "192.168.1.10"
  ngap_ip: "0.0.0.0"
  gtp_ip: "192.168.1.10"
  location:
    latitude: 37.7749
    longitude: -122.4194
    city: "San Francisco"
    country: "United States"
    site: "Site 1"
  ues:
    - imsi: "imsi-999700000000001"
      supi: "imsi-999700000000001"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      slice:
        sst: 1
        sd: "010203"
      dnn: "internet"
    - imsi: "imsi-999700000000002"
      supi: "imsi-999700000000002"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      slice:
        sst: 1
        sd: "010203"
      dnn: "internet"
```

### Lifecycle Handler

**Path:** `controller/src/radio/lifecycle.py`  
**Playbooks:** `controller/src/radio/playbooks/` (uses Kubernetes manifests, not Ansible)

Key behaviors:
- **CREATE:**
  1. Waits for `FiveGCore` to be `Ready` (discovers AMF endpoint)
  2. Creates gNodeB Deployment with UERANSIM container
  3. Waits for gNodeB to establish NG-AP connection with AMF
  4. Creates one pod per UE with UERANSIM UE simulator
  5. Monitors UE registration and PDU session establishment
- **UPDATE:** Recreates gNodeB/UE pods on spec changes (e.g., adding UEs)
- **DELETE:** Deletes gNodeB + UE pods
- **TIMER (60s):** Checks gNodeB and UE pod health, updates status, syncs to Spanner

---

## Integration with Core Domain

The Radio domain integrates with the Core domain via:

1. **AMF endpoint discovery** — `CellSite` waits for the parent `FiveGCore` to reach `Ready`, then discovers the AMF's Kubernetes service endpoint.
2. **NG-AP connection** — gNodeB establishes NG Setup procedure with the AMF.
3. **UE registration** — Each UE performs NAS registration via the AMF.
4. **PDU session** — UEs request PDU sessions for the specified DNN, allocated by the SMF and anchored by the UPF.

```
CellSite (spec.amf_endpoint: "amf-1:38412")
  └── gNodeB → NetworkFunction: AMF (Core domain)
        └── UE → AMF → SMF → UPF
              └── UE receives IP address (e.g. 10.60.0.1/16 from "internet" DNN)
```

---

## Testing

```bash
# Deploy a 5G core first
kubectl apply -f examples/controller/multidomain/default/core.yaml
kubectl wait --for=condition=ready core/free5gc-1 --timeout=300s

# Deploy a cell site
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: site-1
  namespace: network
spec:
  gnb_name: "gnb-1"
  mcc: "999"
  mnc: "70"
  tac: 1
  gnb_id: 1
  nci: "0x000000010"
  amf_endpoint: "amf-1:38412"
  link_ip: "192.168.1.10"
  ngap_ip: "0.0.0.0"
  gtp_ip: "192.168.1.10"
  ues:
    - imsi: "imsi-999700000000001"
      supi: "imsi-999700000000001"
      key: "465B5CE8B199B49FAA5F0A2EE238A6BC"
      opc: "E8ED289DEBA952E4283B54E88E6183CA"
      slice:
        sst: 1
        sd: "010203"
      dnn: "internet"
EOF

# Wait for cell site to be ready
kubectl wait --for=condition=ready cellsite/site-1 --timeout=300s

# Check cell site status
kubectl get cellsite site-1 -o yaml

# Check gNodeB logs
kubectl logs -l app=gnb-1

# Check UE logs
kubectl logs -l app=ue-imsi-999700000000001

# Test UE connectivity
kubectl exec -it <ue-pod-name> -- ping -c 4 8.8.8.8
```

---

## Common Patterns

### Multi-Site Deployment

Deploy multiple cell sites with different locations:

```yaml
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: site-sf
spec:
  gnb_name: "gnb-sf"
  gnb_id: 1
  location:
    city: "San Francisco"
  # ... other config ...
---
apiVersion: google.dev/v1
kind: CellSite
metadata:
  name: site-la
spec:
  gnb_name: "gnb-la"
  gnb_id: 2
  location:
    city: "Los Angeles"
  # ... other config ...
```

### Multi-Slice UEs

Attach UEs to different network slices:

```yaml
ues:
  - imsi: "imsi-999700000000001"
    slice:
      sst: 1  # eMBB
      sd: "010203"
    dnn: "internet"
  - imsi: "imsi-999700000000002"
    slice:
      sst: 3  # mMTC (IoT)
      sd: "334455"
    dnn: "iot"
```

### UE Mobility Simulation

Create multiple cell sites with overlapping coverage areas, then use UERANSIM's handover features (requires manual scripting or future CRD enhancement).

---

## Configuration Reference

UERANSIM configuration follows the UERANSIM schema. See:
- **UERANSIM documentation:** https://github.com/aligungr/UERANSIM
- **Sample configs:** `examples/controller/multidomain/default/`
- **Container images:** `free5gc/ueransim:latest` (or custom builds)

### Authentication Keys

The default test keys in the example above are from the free5gc test subscriber database. For production testing, generate unique keys per UE and provision them in the UDR (via the WebUI or API).

### PLMN Considerations

- **MCC/MNC** must match between `CellSite`, `FiveGCore`, and UE authentication records in the UDR.
- **TAC** (Tracking Area Code) can be used for mobility management testing.
- **NCI** (NR Cell Identity) must be unique per gNodeB.
