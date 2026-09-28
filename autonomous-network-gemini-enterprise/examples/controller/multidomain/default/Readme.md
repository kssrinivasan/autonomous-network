# Default Multi-Domain Telco Lab

This directory contains configuration files for a default multi-domain telco lab that combines a transport MPLS backbone, a 5G core network, and a radio access network (RAN). It is the reference deployment for end-to-end autonomous network management across all three domains.

## Network Overview

**Total Infrastructure:**
- **10 backbone routers** (4 P + 2 RR + 4 PE) in a flat 2-tier transport design
- **4 CE routers** (1 core + 3 cell-site) connected via RED_MESH L3VPN
- **6 gNB cell sites** (2 per cell CE router)
- **30 UEs** provisioned across all cell sites (5 per gNB)
- **11 free5gc network functions** (NRF, AMF, SMF, UPF, AUSF, UDM, UDR, PCF, NSSF, WebUI, MongoDB)
- **Flat OSPF** (Area 0 only) with MPLS/LDP across all backbone routers
- **RED_MESH any-to-any L3VPN** connecting core and all RAN CE routers

## Network Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ TRANSPORT DOMAIN (MPLS Backbone)                                    │
│                                                                     │
│   ┌────┐         ┌────┐         ┌────┐         ┌────┐             │
│   │ p1 │◄───────►│ p2 │◄───────►│ p4 │         │ p3 │             │
│   │(LO)│         │(MA)│         │(LE)│         │(ED)│             │
│   └─┬──┘         └─┬──┘         └─┬──┘         └─┬──┘             │
│     │  iBGP via    │              │              │                 │
│   ┌─▼──┐         ┌─▼──┐          │          ┌───┘                 │
│   │rr1 │◄───────►│rr2 │          │          │                     │
│   │(BI)│         │(BR)│          │          │                     │
│   └────┘         └────┘          │          │                     │
│                                  │          │                     │
│   ┌────┐         ┌────┐         ┌┴───┐     ┌┴───┐               │
│   │pe1 │         │pe2 │         │pe4 │     │pe3 │               │
│   │(OX)│         │(CA)│         │(CF)│     │(BR)│               │
│   └─┬──┘         └─┬──┘         └─┬──┘     └─┬──┘               │
└─────┼──────────────┼──────────────┼───────────┼────────────────────┘
      │ RED_MESH VPN │              │           │
      │              │              │           │
┌─────▼──────┐  ┌────▼──────┐  ┌───▼──────┐ ┌──▼──────┐
│  CORE      │  │  RAN      │  │  RAN     │ │  RAN    │
│  DOMAIN    │  │  DOMAIN   │  │  DOMAIN  │ │  DOMAIN │
│            │  │           │  │          │ │         │
│ ce-core1   │  │ ce-cell1  │  │ ce-cell2 │ │ce-cell3 │
│ (Swindon)  │  │(Bury StEd)│  │(Eastbrne)│ │(Newport)│
│            │  │           │  │          │ │         │
│ free5gc NFs│  │cell1a     │  │cell2a    │ │cell3a   │
│ NRF AMF    │  │cell1b     │  │cell2b    │ │cell3b   │
│ SMF UPF    │  │(5 UEs ea) │  │(5 UE ea) │ │(5 UE ea)│
│ AUSF UDM   │  └───────────┘  └──────────┘ └─────────┘
│ UDR PCF    │
│ NSSF WebUI │
│ MongoDB    │
└────────────┘
```

**Design Characteristics:**
- **Flat transport topology:** All backbone routers in OSPF Area 0 (single area)
- **Multi-domain L3VPN:** RED_MESH VPN spans pe1–pe4, connecting core to all RAN CEs
- **Dual-homed PEs:** Each PE connects to two P routers for transport redundancy
- **5G end-to-end:** gNBs registered to free5gc AMF; UPF handles user-plane traffic via SMF

## Domain Details

### Transport Domain

- **4 P routers** (p1-p4): Full-mesh core @ 1Gbps, OSPF Area 0, MPLS/LDP
- **2 Route Reflectors** (rr1, rr2): iBGP RR pair; each PE peers with both
- **4 PE routers** (pe1-pe4): VPNv4 L3VPN edge, dual-homed to P routers

### Core Domain (pe1 → ce-core1)

- **ce-core1** terminates the RED_MESH VPN on pe1 and provides a LAN (10.102.1.0/24) for all free5gc NFs
- All NFs reachable at 10.102.1.10–.22; MongoDB at 10.102.1.21

### Radio Domain (pe2/pe3/pe4 → ce-cell1/2/3)

| CE | PE | LAN | gNBs | UEs |
|----|----|-----|------|-----|
| ce-cell1 (Bury St Edmunds) | pe2 | 10.103.1.0/24 | cell1a (0x1), cell1b (0x2) | 001–010 |
| ce-cell2 (Eastbourne)      | pe3 | 10.103.2.0/24 | cell2a (0x3), cell2b (0x4) | 011–020 |
| ce-cell3 (Newport)         | pe4 | 10.103.3.0/24 | cell3a (0x5), cell3b (0x6) | 021–030 |

### RED_MESH VPN

Any-to-any L3VPN connecting all 4 CE routers so gNBs can reach core NFs over the MPLS backbone:

| PE | VRF iface | PE-CE subnet | CE router_id |
|----|-----------|-------------|--------------|
| pe1 | eth2 | 10.90.90.0/24 | 10.0.0.110 |
| pe2 | eth3 | 10.91.91.0/24 | 10.0.0.111 |
| pe3 | eth2 | 10.92.92.0/24 | 10.0.0.112 |
| pe4 | eth2 | 10.93.93.0/24 | 10.0.0.113 |

Route target: `65035:2000` (import/export on all PEs)

## Files

1. **[infrastructure.yaml](infrastructure.yaml)**
   - 10 backbone routers + 4 CE routers with geographic locations
   - 20 P2P backbone links and 4 PE-CE access links
   - 4 LAN segments (1 core + 3 cell)
   - All free5gc device entries and 6 cell site devices
   - Management network (192.168.122.0/24)

2. **[underlay.yaml](underlay.yaml)**
   - Flat OSPF (Area 0 only) across all backbone routers
   - MPLS/LDP on all P and PE backbone interfaces
   - BGP route reflector config (rr1: 10.0.0.1, rr2: 10.0.0.2)
   - All 4 PEs as RR clients, iBGP between rr1 and rr2

3. **[vpn.yaml](vpn.yaml)**
   - RED_MESH any-to-any L3VPN on pe1–pe4
   - eBGP sessions from each PE VRF to its CE router
   - CE router eBGP config for ce-core1 and ce-cell1/2/3

4. **[core.yaml](core.yaml)**
   - FiveGCore CR for free5gc (PLMN 208/93, slice SST=1 SD=010203)
   - 10 network functions + WebUI + MongoDB
   - 30 subscribers (IMSIs 208930000000001–208930000000030)

5. **[radio.yaml](radio.yaml)**
   - 6 CellSite CRs, one per gNB device (cell1a–cell3b)
   - Each gNB has unique gnbId (0x1–0x6), PLMN 208/93, TAC 1
   - 5 UEs per gNB, referencing free5gc-core AMF/UPF/SMF

## Deployment


### Step 1: Deploy Infrastructure

```bash
kubectl apply -f examples/controller/multidomain/default/infrastructure.yaml
```

Wait for all routers and CE devices to be created (~5-8 minutes):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

```bash
kubectl apply -f examples/controller/multidomain/default/underlay.yaml
```

Verify OSPF and BGP convergence:

```bash
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show ip ospf neighbor"
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
```

### Step 3: Deploy RED_MESH VPN

```bash
kubectl apply -f examples/controller/multidomain/default/vpn.yaml
```

Verify VPN configuration:

```bash
# Check VRF on PE router
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show vrf"

# Check VPNv4 routes propagating
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip bgp vpnv4 all"

# Check eBGP to CE is up
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip bgp vrf RED_MESH summary"
```

### Step 4: Deploy 5G Core

```bash
kubectl apply -f examples/controller/multidomain/default/core.yaml
```

Wait for all NFs to be running:

```bash
kubectl get networkfunction -n network --watch
```

### Step 5: Deploy Radio (Cell Sites)

```bash
kubectl apply -f examples/controller/multidomain/default/radio.yaml
```

Monitor gNB registration with AMF:

```bash
kubectl get cellsite -n network --watch
```

## Verification

### Transport Layer

```bash
# All 10 backbone routers in Area 0
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show ip ospf neighbor"

# MPLS labels distributed
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show mpls ldp binding"

# PE-to-PE reachability
kubectl exec -it -n network <pe1-pod> -- ping 10.0.0.11 -I 10.0.0.7
# pe1 (10.0.0.7) → pe4 (10.0.0.11)
```

### VPN Layer

```bash
# ce-core1 can reach ce-cell1 LAN
kubectl exec -it -n network <ce-core1-pod> -- ping 10.103.1.1
# ce-core1 gateway (10.102.1.1) → ce-cell1 gateway (10.103.1.1)

# VRF route table on pe1
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip route vrf RED_MESH"
```

### 5G Core

```bash
# Check all NFs registered with NRF
kubectl exec -it -n network <nrf-pod> -- curl -s http://10.102.1.10:8000/nnrf-nfm/v1/nf-instances | jq .
```

### End-to-End (UE Registration)

```bash
# Check UE attached on a gNB
kubectl exec -it -n network <cell1a-pod> -- nr-cli --all --exec "status"
```

## IP Addressing Scheme

### Loopback Addresses (10.0.0.0/24)

| Role | Router | Loopback |
|------|--------|----------|
| Route Reflector | rr1, rr2 | 10.0.0.1, 10.0.0.2 |
| Core (P) | p1, p2, p3, p4 | 10.0.0.3–10.0.0.6 |
| Provider Edge (PE) | pe1, pe2, pe3, pe4 | 10.0.0.7, 10.0.0.8, 10.0.0.10, 10.0.0.11 |
| Core CE | ce-core1 | 10.0.0.110 |
| Cell CEs | ce-cell1, ce-cell2, ce-cell3 | 10.0.0.111–10.0.0.113 |

### P2P Link Addressing

| Link Type | Subnet Range | Bandwidth |
|-----------|-------------|-----------|
| Core mesh (P-P) | 172.16.30–60.0/24 | 1Gbps |
| Core-RR (P-RR) | 172.16.10–20.0/24 | 1Gbps |
| P-PE access | 172.16.90–180.0/24 | 100Mbps |
| PE-CE (core) | 10.90.90.0/24 | 100Mbps |
| PE-CE (cell1/2/3) | 10.91.91–10.93.93.0/24 | 100Mbps |

### LAN Networks

| Segment | Subnet | Gateway | Domain |
|---------|--------|---------|--------|
| lan-core1 | 10.102.1.0/24 | 10.102.1.1 | 5G Core NFs |
| lan-cell1 | 10.103.1.0/24 | 10.103.1.1 | cell1a, cell1b |
| lan-cell2 | 10.103.2.0/24 | 10.103.2.1 | cell2a, cell2b |
| lan-cell3 | 10.103.3.0/24 | 10.103.3.1 | cell3a, cell3b |

### Management Network (192.168.122.0/24)

| Range | Devices |
|-------|---------|
| .11–.14 | p1–p4 |
| .21–.22 | rr1, rr2 |
| .31–.34 | pe1–pe4 |
| .48–.51 | ce-core1, ce-cell1, ce-cell2, ce-cell3 |
| .60–.71 | free5gc NFs (nrf, amf, smf, upf, ausf, udm, udr, pcf, nssf, webui, mongodb, internet) |
| .72–.77 | cell1a, cell1b, cell2a, cell2b, cell3a, cell3b |

## Geographic Distribution

### Transport Layer

| Site | Router | Role |
|------|--------|------|
| London, UK | p1 | Core P |
| Manchester, UK | p2 | Core P |
| Edinburgh, UK | p3 | Core P |
| Leeds, UK | p4 | Core P |
| Birmingham, UK | rr1 | Route Reflector |
| Bristol, UK | rr2 | Route Reflector |
| Oxford, UK | pe1 | PE (5G Core access) |
| Cambridge, UK | pe2 | PE (RAN cell-1 access) |
| Brighton, UK | pe3 | PE (RAN cell-2 access) |
| Cardiff, UK | pe4 | PE (RAN cell-3 access) |

### Edge Sites

| Site | Device | Domain |
|------|--------|--------|
| Swindon, UK | ce-core1 + free5gc NFs | 5G Core |
| Bury St Edmunds, UK | ce-cell1, cell1a, cell1b | RAN |
| Eastbourne, UK | ce-cell2, cell2a, cell2b | RAN |
| Newport, UK | ce-cell3, cell3a, cell3b | RAN |

## Troubleshooting

### VPN Routes Not Propagating

```bash
# Check eBGP session PE ↔ CE is established
kubectl exec -it -n network <pe2-pod> -- vtysh -c "show ip bgp vrf RED_MESH summary"
# peer 10.91.91.2 should show state Established

# Check CE is advertising its LAN
kubectl exec -it -n network <ce-cell1-pod> -- vtysh -c "show ip bgp"
# Should see 10.103.1.0/24 advertised
```

### gNB Not Registering with AMF

```bash
# Confirm VPN reachability from cell LAN to core LAN
kubectl exec -it -n network <ce-cell1-pod> -- ping 10.102.1.11
# ce-cell1 → AMF (10.102.1.11)

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

## Cleanup

```bash
# Delete radio (cell sites)
kubectl delete cellsite -n network -l app=cellsite

# Delete 5G core
kubectl delete fivegcore -n network free5gc-core

# Delete VPN
kubectl delete vyosl3vpn -n network vpn-service

# Delete underlay
kubectl delete vyosunderlay -n network default-multi-underlay

# Delete infrastructure (removes all routers and devices)
kubectl delete vyosinfrastructure -n network default-multi-infra
```

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [Agents Documentation](../../../../docs/agents/Readme.md)
- [Transport Scenarios](../../../../docs/scenarios/transport.md)
- [Operator Configuration](../../../../controller/config/)
- [Transport Default Network](../../transport/default-network/README.md)
- [Multidomain Medium](../medium/Readme.md)
