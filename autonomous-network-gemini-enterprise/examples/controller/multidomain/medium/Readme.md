# Medium Multi-Domain Telco Lab

A scaled-up multi-domain telco lab with a 3-tier hierarchical MPLS backbone, 5G core, and 5 distributed RAN sites. Designed to model realistic ISP transport topologies with multi-area OSPF, BGP route reflection, and an MPLS L3VPN connecting the core to all RAN access sites.

## Network Overview

**Total Infrastructure:**
- **18 backbone routers** (4 P + 2 RR + 4 PA + 8 PE) in a 3-tier transport hierarchy
- **6 CE routers** (1 core + 5 cell-site) connected via RAN_MESH L3VPN
- **25 gNB cell sites**, 5 per RAN CE router
- **250 UEs** provisioned across all cell sites (10 per gNB)
- **12 free5gc network functions** (NRF, AMF, SMF, UPF, AUSF, UDM, UDR, PCF, NSSF, WebUI, MongoDB, Internet GW)
- **Multi-area OSPF** (Area 0 backbone + Area 1 + Area 2 regional) with MPLS/LDP
- **RAN_MESH any-to-any L3VPN** (RT 65035:3000, table 400) connecting core and all 5 RAN CEs

## Network Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ TRANSPORT DOMAIN — MPLS Backbone (3-tier hierarchy)                     │
│                                                                         │
│  ┌────┐  ┌────┐  ┌────┐  ┌────┐      ┌─────┐  ┌─────┐                │
│  │ p1 │──│ p2 │──│ p3 │──│ p4 │      │ rr1 │  │ rr2 │  (iBGP RR)    │
│  │(LO)│  │(MA)│  │(ED)│  │(BI)│      │(ZH) │  │(BR) │                │
│  └─┬──┘  └─┬──┘  └─┬──┘  └─┬──┘      └─────┘  └─────┘                │
│    │       │       │       │                                           │
│  ┌─▼──┐  ┌─▼──┐  ┌─▼──┐  ┌─▼──┐                                      │
│  │pa1 │  │pa2 │  │pa3 │  │pa4 │   (PA/ABR ring, Area 0 ↔ 1/2)        │
│  │(BS)│  │(LD)│  │(DB)│  │(GL)│                                       │
│  └─┬──┘  └─┬──┘  └─┬──┘  └─┬──┘                                      │
│    │       │       │       │                                           │
│  pe1  pe2  pe3  pe4  pe5  pe6  pe7  pe8                                │
│  (OX)(CA)(BH)(SO)(LV)(NC)(CK)(AB)                                      │
└──┬───┬───┬───┬───┬───┬──────────────────────────────────────────────┘
   │   │   │   │   │   │  RAN_MESH L3VPN (eth3 on pe1–pe6)
   │   │   │   │   │   │
┌──▼─┐┌▼──┐┌▼──┐┌─▼─┐┌▼──┐┌─▼──────────────────────────────────────┐
│CORE││RAN││RAN││RAN││RAN││ RAN                                      │
│    ││S1 ││S2 ││S3 ││S4 ││ S5                                       │
│ce- ││ce- ││ce-││ce-││ce-││ ce-                                     │
│core│site│site│site│site│ site5                                     │
│1   ││1  ││2  ││3  ││4  ││                                          │
│    │└───┘└───┘└───┘└───┘└─────────────────────────────────────────┘
│free│ gNB  gNB  gNB  gNB   gNB
│5gc │ 10UE 10UE 10UE 10UE  10UE
│NFs │
└────┘
```

**Design Characteristics:**
- **3-tier transport:** P core mesh → PA aggregation (ABR) → PE edge; all dual-homed for redundancy
- **Multi-area OSPF:** Area 0 (P/RR/PA), Area 1 (southern PEs), Area 2 (northern PEs)
- **BGP RR pair:** rr1 (Zurich) and rr2 (Brussels); all PEs as RR clients
- **RAN_MESH VPN:** pe1–pe6 each hold a RAN_MESH VRF (table 400, RT 65035:3000) with eBGP to the attached CE
- **5G end-to-end:** gNBs on LAN site1–5 register to AMF; UPF on lan-core1

## Domain Details

### Transport Domain

| Tier | Count | Routers | Links | OSPF |
|------|-------|---------|-------|------|
| Core (P) | 4 | p1 (London), p2 (Manchester), p3 (Edinburgh), p4 (Birmingham) | Full mesh @ 10Gbps | Area 0 |
| Route Reflector | 2 | rr1 (Zurich), rr2 (Brussels) | One link each to 2 P routers | Area 0 |
| Pre-Aggregation (PA) | 4 | pa1 (Bristol), pa2 (Leeds), pa3 (Dublin), pa4 (Glasgow) | Ring + P uplinks @ 10Gbps | Area 0 (ABR) |
| Provider Edge (PE) | 8 | pe1–pe8 (see table below) | Dual-homed to 2 PAs @ 1Gbps | Area 1/2 |

### PE Router Map

| PE | Location | PA uplinks | RAN CE | OSPF Area |
|----|----------|-----------|--------|-----------|
| pe1 | Oxford | pa1, pa2 | ce-site1 | Area 1 |
| pe2 | Cambridge | pa1, pa2 | ce-site2 | Area 1 |
| pe3 | Brighton | pa1, pa3 | ce-site3 | Area 1 |
| pe4 | Southampton | pa2, pa3 | ce-site4 | Area 1 |
| pe5 | Liverpool | pa2, pa4 | ce-site5 | Area 2 |
| pe6 | Newcastle | pa1, pa4 | ce-core1 (5GC) | Area 2 |
| pe7 | Cork | pa3, pa4 | — | Area 2 |
| pe8 | Aberdeen | pa3, pa4 | — | Area 2 |

### Core Domain (pe6 → ce-core1)

- **ce-core1** (Newcastle) terminates the RAN_MESH VPN on pe6 and provides lan-core1 (10.102.1.0/24) for all free5gc NFs
- All NFs reachable at 10.102.1.10–.22; MongoDB at .21

| Device | IP | Mgmt IP |
|--------|----|---------|
| nrf | 10.102.1.10 | 192.168.122.210 |
| amf | 10.102.1.11 | 192.168.122.211 |
| smf | 10.102.1.12 | 192.168.122.212 |
| upf | 10.102.1.13 | 192.168.122.213 |
| ausf | 10.102.1.14 | 192.168.122.214 |
| udm | 10.102.1.15 | 192.168.122.215 |
| udr | 10.102.1.16 | 192.168.122.216 |
| pcf | 10.102.1.17 | 192.168.122.217 |
| nssf | 10.102.1.18 | 192.168.122.218 |
| webui | 10.102.1.20 | 192.168.122.219 |
| mongodb | 10.102.1.21 | 192.168.122.220 |
| internet | 10.102.1.22 | 192.168.122.221 |

### Radio Domain (pe1–pe5 → ce-site1–5)

| CE | PE | Location | PE-CE subnet | LAN | gNB devices (5 per CE) | UEs (IMSI suffix) | CE mgmt |
|----|----|----------|-------------|-----|----------------------|-------------------|---------|
| ce-site1 | pe1 | Oxford | 10.94.1.0/30 | 10.103.1.0/24 | site1a–e (0x1–0x5) | 001–050 | .201 |
| ce-site2 | pe2 | Cambridge | 10.94.2.0/30 | 10.103.2.0/24 | site2a–e (0x6–0xa) | 051–100 | .202 |
| ce-site3 | pe3 | Brighton | 10.94.3.0/30 | 10.103.3.0/24 | site3a–e (0xb–0xf) | 101–150 | .203 |
| ce-site4 | pe4 | Southampton | 10.94.4.0/30 | 10.103.4.0/24 | site4a–e (0x10–0x14) | 151–200 | .204 |
| ce-site5 | pe5 | Liverpool | 10.94.5.0/30 | 10.103.5.0/24 | site5a–e (0x15–0x19) | 201–250 | .205 |

gNB container mgmt IPs: site1a=.222, site1b=.223, … site5e=.246 (sequential from 192.168.122.222)

### RAN_MESH VPN

Any-to-any L3VPN connecting ce-core1 and all 5 cell-site CEs so gNBs can reach core NFs over the MPLS backbone:

| PE | CE | PE-CE subnet | CE router_id | VRF iface |
|----|----|-------------|-------------|-----------|
| pe6 | ce-core1 | 10.94.0.0/30 | 10.0.0.110 | eth3 |
| pe1 | ce-site1 | 10.94.1.0/30 | 10.0.0.111 | eth3 |
| pe2 | ce-site2 | 10.94.2.0/30 | 10.0.0.112 | eth3 |
| pe3 | ce-site3 | 10.94.3.0/30 | 10.0.0.113 | eth3 |
| pe4 | ce-site4 | 10.94.4.0/30 | 10.0.0.114 | eth3 |
| pe5 | ce-site5 | 10.94.5.0/30 | 10.0.0.115 | eth3 |

Route target: `65035:3000` (import/export on all 6 PEs), VRF table 400, BGP AS 65035 on all CEs

## Files

1. **[infrastructure.yaml](infrastructure.yaml)**
   - 18 backbone routers (P/RR/PA/PE) + 6 CE routers with geographic locations
   - 48 P2P backbone links (P-P, P-RR, P-PA, PA-PA, PA-PE) and 6 PE-CE access links
   - 6 LAN segments (1 core + 5 cell sites)
   - All free5gc device entries and 5 cell site devices
   - Management network (192.168.122.0/24)

2. **[underlay.yaml](underlay.yaml)**
   - Multi-area OSPF (Area 0, Area 1, Area 2) with PA routers as ABRs
   - MPLS/LDP on all backbone interfaces
   - BGP route reflector config (rr1: 10.0.0.1, rr2: 10.0.0.2)
   - All 8 PEs as RR clients, iBGP between rr1 and rr2

3. **[vpn.yaml](vpn.yaml)**
   - RAN_MESH any-to-any L3VPN on pe1–pe6
   - eBGP sessions from each PE VRF (eth3) to its CE
   - CE router eBGP config for ce-core1 and ce-site1–5

4. **[core.yaml](core.yaml)**
   - FiveGCore CR `medium-core` (PLMN 208/93, slice SST=1 SD=010203)
   - 10 network functions + WebUI
   - 250 subscribers (IMSIs 208930000000001–208930000000250)

5. **[radio.yaml](radio.yaml)**
   - 25 CellSite CRs (cellsite-site1a–cellsite-site5e), 5 per edge site
   - gnbIds 0x1–0x19, PLMN 208/93, TAC 1
   - 10 UEs per gNB, referencing `medium-core-amf`, `medium-core-upf`, `medium-core-smf`

## Deployment


### Step 1: Deploy Infrastructure

```bash
kubectl apply -f examples/controller/multidomain/medium/infrastructure.yaml
```

Wait for all routers and CE devices to be ready (~10–15 minutes):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

```bash
kubectl apply -f examples/controller/multidomain/medium/underlay.yaml
```

Verify multi-area OSPF convergence:

```bash
kubectl exec -it -n network <p1-pod> -- vtysh -c "show ip ospf neighbor"
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf border-routers"
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
```

### Step 3: Deploy RAN_MESH VPN

```bash
kubectl apply -f examples/controller/multidomain/medium/vpn.yaml
```

Verify VPN configuration:

```bash
# Check VRF is configured on PE
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show vrf"

# Check VPNv4 routes propagating via RRs
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip bgp vpnv4 all"

# Check eBGP to CE is established
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip bgp vrf RAN_MESH summary"
```

### Step 4: Deploy 5G Core

```bash
kubectl apply -f examples/controller/multidomain/medium/core.yaml
```

Wait for all NFs to be running:

```bash
kubectl get networkfunction -n network --watch
```

### Step 5: Deploy Radio (Cell Sites)

```bash
kubectl apply -f examples/controller/multidomain/medium/radio.yaml
```

Monitor gNB registration:

```bash
kubectl get cellsite -n network --watch
```

## Verification

### Transport Layer

```bash
# Multi-area OSPF — PA routers should show inter-area routes
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip route ospf"

# MPLS labels distributed end-to-end
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show mpls ldp binding"

# PE-to-PE reachability across 3 tiers
kubectl exec -it -n network <pe1-pod> -- ping 10.0.0.65 -I 10.0.0.60
# pe1 (10.0.0.60) → pe6 (10.0.0.65)
```

### VPN Layer

```bash
# ce-core1 can reach a cell site LAN
kubectl exec -it -n network <ce-core1-pod> -- ping 10.103.1.1
# core gateway (10.102.1.1) → site1 gateway (10.103.1.1)

# VRF route table on pe6 (core PE)
kubectl exec -it -n network <pe6-pod> -- vtysh -c "show ip route vrf RAN_MESH"
```

### 5G Core

```bash
# All NFs registered with NRF
kubectl exec -it -n network <nrf-pod> -- curl -s http://10.102.1.10:8000/nnrf-nfm/v1/nf-instances | jq .
```

### End-to-End

```bash
# UE status on a gNB
kubectl exec -it -n network <site1-pod> -- nr-cli --all --exec "status"
```

## IP Addressing Scheme

### Loopback Addresses (10.0.0.0/24)

| Role | Routers | Loopback range |
|------|---------|----------------|
| Route Reflector | rr1, rr2 | 10.0.0.1–.2 |
| Core (P) | p1–p4 | 10.0.0.3–.6 |
| Pre-Aggregation (PA) | pa1–pa4 | 10.0.0.20–.23 |
| Provider Edge (PE) | pe1–pe8 | 10.0.0.60–.67 |
| Core CE | ce-core1 | 10.0.0.110 |
| Cell CEs | ce-site1–5 | 10.0.0.111–.115 |

### P2P Link Addressing

| Link type | Subnet range | Bandwidth |
|-----------|-------------|-----------|
| Core mesh (P-P) | 172.16.0.0/30 – 172.16.0.20/30 | 10Gbps |
| Core-RR (P-RR) | 172.16.10.0/30 – 172.16.10.12/30 | 10Gbps |
| Core-PA (P-PA) | 172.16.20.0/30 – 172.16.20.28/30 | 10Gbps |
| PA ring (PA-PA) | 172.16.30.0/30 – 172.16.30.12/30 | 10Gbps |
| PA-PE access | 172.16.40.0/30 – 172.16.40.60/30 | 1Gbps |
| PE-CE (core) | 10.94.0.0/30 | 100Mbps |
| PE-CE (sites 1–5) | 10.94.1.0/30 – 10.94.5.0/30 | 100Mbps |

### LAN Networks

| Segment | Subnet | Gateway | Domain |
|---------|--------|---------|--------|
| lan-core1 | 10.102.1.0/24 | 10.102.1.1 | 5G Core NFs |
| lan-site1 | 10.103.1.0/24 | 10.103.1.1 | Oxford gNB (10 UEs) |
| lan-site2 | 10.103.2.0/24 | 10.103.2.1 | Cambridge gNB (10 UEs) |
| lan-site3 | 10.103.3.0/24 | 10.103.3.1 | Brighton gNB (10 UEs) |
| lan-site4 | 10.103.4.0/24 | 10.103.4.1 | Southampton gNB (10 UEs) |
| lan-site5 | 10.103.5.0/24 | 10.103.5.1 | Liverpool gNB (10 UEs) |

### Management Network (192.168.122.0/24)

| Range | Devices |
|-------|---------|
| .11–.14 | p1–p4 |
| .21–.22 | rr1, rr2 |
| .31–.34 | pa1–pa4 |
| .61–.68 | pe1–pe8 |
| .200 | ce-core1 |
| .201–.205 | ce-site1–5 |
| .210–.221 | free5gc NFs (nrf, amf, smf, upf, ausf, udm, udr, pcf, nssf, webui, mongodb, internet) |
| .222–.246 | site1a–site5e (25 gNB containers, 5 per edge) |

## Geographic Distribution

### Transport Layer

| Site | Router | Role |
|------|--------|------|
| London, UK | p1 | Core P |
| Manchester, UK | p2 | Core P |
| Edinburgh, UK | p3 | Core P |
| Birmingham, UK | p4 | Core P |
| Zurich, CH | rr1 | Route Reflector |
| Brussels, BE | rr2 | Route Reflector |
| Bristol, UK | pa1 | Pre-Aggregation ABR |
| Leeds, UK | pa2 | Pre-Aggregation ABR |
| Dublin, IE | pa3 | Pre-Aggregation ABR |
| Glasgow, UK | pa4 | Pre-Aggregation ABR |
| Oxford, UK | pe1 | PE (RAN site-1) |
| Cambridge, UK | pe2 | PE (RAN site-2) |
| Brighton, UK | pe3 | PE (RAN site-3) |
| Southampton, UK | pe4 | PE (RAN site-4) |
| Liverpool, UK | pe5 | PE (RAN site-5) |
| Newcastle, UK | pe6 | PE (5G Core) |
| Cork, IE | pe7 | PE (transit) |
| Aberdeen, UK | pe8 | PE (transit) |

### Edge Sites

| Site | CE | Devices | Domain |
|------|----|---------|--------|
| Newcastle, UK | ce-core1 | free5gc NFs + MongoDB | 5G Core |
| Oxford, UK | ce-site1 | site1 (10 UEs) | RAN |
| Cambridge, UK | ce-site2 | site2 (10 UEs) | RAN |
| Brighton, UK | ce-site3 | site3 (10 UEs) | RAN |
| Southampton, UK | ce-site4 | site4 (10 UEs) | RAN |
| Liverpool, UK | ce-site5 | site5 (10 UEs) | RAN |

## Troubleshooting

### VPN Routes Not Propagating

```bash
# Check eBGP session PE ↔ CE is established
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip bgp vrf RAN_MESH summary"
# peer 10.94.1.2 should show state Established

# Check CE is advertising its LAN
kubectl exec -it -n network <ce-site1-pod> -- vtysh -c "show ip bgp"
# Should see 10.103.1.0/24 advertised

# Verify inter-area routes are present on PE
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip route vrf RAN_MESH"
```

### gNB Not Registering with AMF

```bash
# Confirm VPN reachability from cell LAN to core LAN
kubectl exec -it -n network <ce-site1-pod> -- ping 10.102.1.11
# ce-site1 → AMF (10.102.1.11)

# Check AMF logs
kubectl logs -n network <amf-pod>
```

### NF Not Reachable

```bash
# Verify MongoDB is up (required for UDR/UDM provisioning)
kubectl exec -it -n network <mongodb-pod> -- mongosh --eval "db.adminCommand('ping')"

# Check NRF has all NFs registered
kubectl exec -it -n network <nrf-pod> -- curl -s http://10.102.1.10:8000/nnrf-nfm/v1/nf-instances
```

### OSPF Inter-Area Issue

```bash
# PA router should be ABR — verify it sees both areas
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf"
# Should show "This router is an ABR"

# Check summary LSAs are being generated
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf database summary"
```

## Cleanup

```bash
# Delete radio (cell sites)
kubectl delete cellsite -n network --all

# Delete 5G core
kubectl delete fivegcore -n network medium-core

# Delete VPN
kubectl delete vyosl3vpn -n network ran-mesh-vpn

# Delete underlay
kubectl delete vyosunderlay -n network medium-network-underlay

# Delete infrastructure (removes all routers and devices)
kubectl delete vyosinfrastructure -n network medium-network-infra
```

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [Agents Documentation](../../../../docs/agents/Readme.md)
- [Transport Scenarios](../../../../docs/scenarios/transport.md)
- [Operator Configuration](../../../../controller/config/)
- [Default Multidomain](../default/Readme.md)
- [Multidomain Index](../../../Readme.md)
