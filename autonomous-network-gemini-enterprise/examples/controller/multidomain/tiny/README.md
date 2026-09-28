# Tiny Multi-Domain Network

This directory contains configuration files for a minimal end-to-end multi-domain network that combines a transport backbone, a 5G core (free5gc), and a radio access node (UERANSIM). It is designed for rapid testing and local development rather than scale or realism.

## Network Overview

**Total Infrastructure:**
- **1 P router** (p1, London) — single-hop transport backbone
- **2 CE routers** — one facing the 5G core, one facing the radio site
- **2 P2P backbone links** @ 100 Mbps
- **Flat OSPF** (Area 0 only — no MPLS, no L3VPN)
- **10 free5gc network functions** on the core LAN
- **1 gNB + 2 UEs** on the radio LAN (UERANSIM)
- **2 subscribers** (IMSI 208930000000001, 208930000000002), PLMN 208/93, SST-1 slice

## Multi-Domain Architecture

```
┌────────────────────────── Transport Domain ───────────────────────────┐
│                                                                        │
│              ┌────┐  172.16.90.0/24   ┌──────────┐                   │
│              │ p1 │◄─────────────────►│ ce-core1 │ Norwich            │
│              │    │  172.16.100.0/24  │          │                   │
│              │    │◄─────────────────►│ ce-cell1 │ Coventry           │
│              └────┘                   └──────────┘                   │
│             London                    OSPF Area 0 (flat)              │
└───────────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┴────────────────────────┐
            │                                                │
┌───────────▼──────── 5G Core Domain ──────────────┐  ┌─────▼────── RAN Domain ────┐
│  ce-core1 LAN: 10.101.2.0/24                     │  │  ce-cell1 LAN: 10.101.1.0/24│
│                                                   │  │                              │
│  NRF  UDR  UDM  AUSF  PCF  NSSF                  │  │  cell1  (UERANSIM gNB + 2UEs)│
│  AMF  SMF  UPF  WebUI  MongoDB                    │  │  IMSI: 208930000000001       │
│  internet (N6 gateway)                            │  │  IMSI: 208930000000002       │
│                                                   │  │  Slice: SST-1 SD-010203      │
│                                                   │  │                              │
│  free5gc  ·  MCC 208  ·  MNC 93                  │  │  gNB ID: 0x1  ·  TAC: 1     │
└───────────────────────────────────────────────────┘  └──────────────────────────────┘
```

**5G User-Plane paths:**
```
Uplink:   cell1 (uesimtun) → gNB → GTP/N3 → UPF → MASQUERADE → eth0 → internet
Downlink: internet → ce-core1 (static route) → UPF eth0 → gtp5g/PFCP → GTP/N3 → gNB → uesimtun → cell1
```

The two directions are asymmetric by design:
- **Uplink** exits the UPF via N6 MASQUERADE — internet sees packets from the UPF's eth0 IP (`10.101.2.13`), not the UE's tunnel address.
- **Downlink** requires internet to send directly to the UE's uesimtun address (`10.60.0.x`). ce-core1 holds a static route `10.60.0.0/16 → 10.101.2.13` so these packets reach the UPF, where gtp5g/PFCP rules encapsulate them into the correct GTP tunnel and forward to the gNB.

Without the static route on ce-core1, downlink packets destined for `10.60.0.x` are dropped at the CE router — they never reach the UPF or GTP tunnel.

## Domain Details

### Transport Domain

A single P router (`p1`) connects both CE routers with basic IP routing — no MPLS, no L3VPN.

| Router | Role | Location | Loopback | Mgmt IP |
|--------|------|----------|----------|---------|
| p1 | P | London | 10.0.0.1 | 192.168.122.11 |
| ce-core1 | CE | Norwich | 10.0.0.2 | 192.168.122.44 |
| ce-cell1 | CE | Coventry | 10.0.0.3 | 192.168.122.45 |

All three routers run flat OSPF Area 0. The CE routers advertise their LAN subnets (`10.101.1.0/24` and `10.101.2.0/24`) into OSPF so the gNB and core NFs can reach each other across `p1`.

### 5G Core Domain (free5gc)

All network functions run as containers on the `ce-core1` LAN segment.

| NF | IP | Role |
|----|-----|------|
| NRF | 10.101.2.10 | Network Repository Function |
| AMF | 10.101.2.11 | Access & Mobility Management |
| SMF | 10.101.2.12 | Session Management |
| UPF | 10.101.2.13 | User Plane (N3/N6 gateway) |
| AUSF | 10.101.2.14 | Authentication |
| UDM | 10.101.2.15 | Unified Data Management |
| UDR | 10.101.2.16 | Unified Data Repository |
| PCF | 10.101.2.17 | Policy Control |
| NSSF | 10.101.2.18 | Network Slice Selection |
| WebUI | 10.101.2.20 | Subscriber management UI |
| MongoDB | 10.101.2.21 | Subscriber database |
| internet | 10.101.2.22 | Internet/DN simulator (N6 side) |

**Slice configuration:**
- SST: 1 (eMBB), SD: `010203`, DNN: `internet`
- UE address pool: `10.60.0.0/16` (dynamic); static pool: `10.60.100.0/24`
- DNS: 8.8.8.8

### Radio Domain (UERANSIM)

A single gNB and UE run on the `ce-cell1` LAN segment, simulating a mobile subscriber.

| Element | Value |
|---------|-------|
| Container | cell1 (10.101.1.10) |
| MCC/MNC | 208 / 93 |
| TAC | 1 |
| gNB ID | 0x1 |
| UE IMSIs | 208930000000001, 208930000000002 |
| Slice | SST-1, SD-010203, DNN: internet |

The operator resolves the UE's `uesimtun` address and passes it as the source bind address for traffic tests, so traffic exercises the full 5G data-plane rather than the bare LAN path.

## Files

1. **[infrastructure.yaml](infrastructure.yaml)**
   - 3 VyOS routers (p1, ce-core1, ce-cell1)
   - 2 P2P links + management + loopback networks
   - 12 device entries (cell1, all free5gc NFs, internet simulator)

2. **[underlay.yaml](underlay.yaml)**
   - Flat OSPF Area 0 on all 3 routers
   - CE routers advertise their LAN subnets into OSPF
   - Static route on ce-core1: `10.60.0.0/16 → 10.101.2.13` (UE pool → UPF, required for downlink)

3. **[core.yaml](core.yaml)**
   - `FiveGCore` CR — orchestrates all free5gc network function containers
   - Defines PLMN, slice, and subscriber

4. **[radio.yaml](radio.yaml)**
   - `CellSite` CR — deploys UERANSIM gNB and UE

5. **[traffic.yaml](traffic.yaml)**
   - Bidirectional TCP `TrafficTest` (cell1 ↔ internet)
   - Multi-sine pattern simulating asymmetric mobile broadband
   - 240× time compression (48 real hours ≈ 1.3 simulated years)

## Traffic Pattern

The traffic test models asymmetric mobile broadband with a bimodal daily cycle:

| Direction | Base | Peaks | Floor |
|-----------|------|-------|-------|
| Uplink (cell1 → internet) | 5 Mbps | ~10 Mbps (08:00 + 20:00 sim) | ~1 Mbps |
| Downlink (internet → cell1) | 15 Mbps | ~31 Mbps (20:00 sim) | ~3 Mbps |

Time compression: **15 real seconds = 1 simulated hour**

## Deployment

### Step 1: Deploy Infrastructure

```bash
kubectl apply -f examples/controller/multidomain/tiny/infrastructure.yaml
```

Wait for routers (~1-2 minutes for 3 routers):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

```bash
kubectl apply -f examples/controller/multidomain/tiny/underlay.yaml
```

Verify OSPF and UE pool route:

```bash
docker exec p1 vtysh -c "show ip ospf neighbor"
# Should show ce-core1 and ce-cell1 as Full neighbors

# Verify ce-core1 has the static route to the UE pool via UPF
docker exec ce-core1 vtysh -c "show ip route 10.60.0.0/16"
# Should show: S>* 10.60.0.0/16 [1/0] via 10.101.2.13
```

### Step 3: Deploy 5G Core

```bash
kubectl apply -f examples/controller/multidomain/tiny/core.yaml
```

Wait for all NFs to start (~2-3 minutes):

```bash
kubectl get networkfunction -n network --watch
```

### Step 4: Deploy Radio Site

```bash
kubectl apply -f examples/controller/multidomain/tiny/radio.yaml
```

Verify UE registration:

```bash
kubectl logs -n network <cell1-pod> | grep "PDU Session Establishment"
# Should show successful PDU session with UPF
```

### Step 5: Run Traffic Test (Optional)

```bash
kubectl apply -f examples/controller/multidomain/tiny/traffic.yaml

kubectl get traffictest -n network --watch
```

## Verification

### Connectivity

```bash
# OSPF reachability — p1 should see both CE loopbacks
docker exec p1 vtysh -c "show ip route ospf"

# Cross-LAN reachability (gNB ↔ AMF — N2 interface)
docker exec cell1 ping 10.101.2.11

# 5G uplink — UE to internet via UPF N6 masquerade
docker exec cell1 ping -I uesimtun0 10.101.2.22

# 5G downlink — internet to UE via UPF GTP tunnel
# internet sends to the uesimtun IP (e.g. 10.60.0.1); ce-core1 static route
# forwards to UPF (10.101.2.13); gtp5g encapsulates and delivers to gNB → UE
docker exec internet ping 10.60.0.1

# Confirm downlink traffic arrives on the tunnel interface, not eth0
docker exec cell1 ip -s link show uesimtun0
# RX bytes should be non-zero during an active TrafficTest
```

### 5G Core Status

```bash
# Check NF registration with NRF
curl http://10.101.2.10:8000/nnrf-nfm/v1/nf-instances

# Check AMF UE context
docker exec amf cat /tmp/amf.log | grep "Ue context"

# Check UPF N3 tunnel
docker exec upf ip xfrm state
```

## IP Addressing

### Loopbacks (10.0.0.0/24)

| Router | Loopback |
|--------|---------|
| p1 | 10.0.0.1 |
| ce-core1 | 10.0.0.2 |
| ce-cell1 | 10.0.0.3 |

### P2P Links

| Link | Subnet | Bandwidth |
|------|--------|-----------|
| p1 ↔ ce-core1 | 172.16.90.0/24 | 100 Mbps |
| p1 ↔ ce-cell1 | 172.16.100.0/24 | 100 Mbps |

### LAN Segments

| Segment | Subnet | Purpose |
|---------|--------|---------|
| lan-core1 | 10.101.2.0/24 | 5G core NFs + internet simulator |
| lan-cell1 | 10.101.1.0/24 | UERANSIM gNB + UE |
| UE pool | 10.60.0.0/16 | PDU session addresses assigned by SMF |

### Management Network

- **Subnet:** 192.168.122.0/24
- p1: .11, ce-core1: .44, ce-cell1: .45
- NFs: .61–.73

## When to Use This Network

**Use tiny for:**
- End-to-end 5G stack smoke tests
- Operator development and unit testing
- CI/CD pipelines (minimal resource footprint)
- First-time setup validation

**Upgrade to a larger topology when you need:**
- Multi-PE transport with MPLS/L3VPN
- Multiple cell sites or subscriber populations
- Chaos/failure testing across transport and radio domains
- GNN training data (topology too small for meaningful graph learning)

## Comparison with Other Deployments

| Aspect | **Tiny** | transport/default | transport/medium |
|--------|----------|-------------------|-----------------|
| Domains | **3 (transport + core + radio)** | 1 (transport only) | 1 (transport only) |
| Backbone routers | **1** | 10 | 18 |
| 5G NFs | **10** | — | — |
| UEs | **2** | — | — |
| MPLS/L3VPN | **No** | Yes | Yes |
| OSPF areas | **1** | 1 | 3 |
| Use case | **Integration testing** | Learning/PoC | Regional lab |

## Cleanup

```bash
kubectl delete traffictest -n network cell1-internet-bidir-tcp cell1-internet-bidir-tcp-ue2
kubectl delete cellsite -n network free5gc-cellsite1
kubectl delete fivegcore -n network free5gc-core
kubectl delete vyosunderlay -n network free5gc-underlay
kubectl delete vyosinfrastructure -n network free5gc-infra
```

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [Operator Configuration](../../../../controller/config/)
- [5G Core CRDs](../../../../controller/config/core/)
- [Radio CRDs](../../../../controller/config/radio/)
- [Transport CRDs](../../../../controller/config/transport/)
- [free5gc](https://free5gc.org)
- [UERANSIM](https://github.com/aligungr/UERANSIM)
