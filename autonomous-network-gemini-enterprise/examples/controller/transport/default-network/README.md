# Default L3VPN Network (Flat Architecture)

This directory contains configuration files for a simple, flat L3VPN network topology. This is the reference implementation for learning MPLS L3VPN fundamentals before moving to hierarchical architectures.

## Network Overview

**Total Infrastructure:**
- **10 backbone routers** in a flat 2-tier design
- **~20 P2P backbone links** @ 1Gbps
- **8 CE routers** (4 BLUE VPN + 4 RED VPN)
- **8 end devices** (4 BLUE VPN + 4 RED VPN)
- **Flat OSPF** (Area 0 only - all routers in backbone area)
- **iBGP with route reflectors** for L3VPN services
- **MPLS/LDP** enabled across all backbone routers

## Network Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: CORE + EDGE (All in OSPF Area 0)                    │
│                                                              │
│         ┌────┐         ┌────┐         ┌────┐               │
│         │ p1 │◄───────►│ p2 │◄───────►│ p4 │               │
│         └─┬──┘         └─┬──┘         └─┬──┘               │
│           │              │              │                   │
│         ┌─▼──┐         ┌─▼──┐         ┌─▼──┐               │
│         │ p3 │         │rr1 │         │rr2 │               │
│         └─┬──┘         └─┬──┘         └─┬──┘               │
│           │       iBGP   │ ▲     iBGP   │                   │
│           │         ┌────┘ └────┐       │                   │
│         ┌─▼──┐    ┌─▼──┐      ┌─▼──┐ ┌─▼──┐               │
│         │pe1 │    │pe2 │      │pe3 │ │pe4 │               │
│         └─┬──┘    └─┬──┘      └─┬──┘ └─┬──┘               │
└───────────┼─────────┼───────────┼──────┼────────────────────┘
            │         │           │      │
         ┌──▼──┐   ┌──▼──┐     ┌──▼──┐ ┌▼───┐
         │ CE  │   │ CE  │     │ CE  │ │ CE │
         └─────┘   └─────┘     └─────┘ └────┘
```

**Design Characteristics:**
- **Flat topology**: All backbone routers in OSPF Area 0 (single area)
- **Direct PE-to-P connections**: No aggregation layer (PA/AR)
- **Simple BGP**: PEs peer directly with RRs (no multi-area complexity)
- **2-tier hierarchy**: Core (P/RR) and Edge (PE)
- **Full mesh core**: P routers fully meshed for redundancy

## OSPF Design (Single Area)

### Area 0 (Backbone - All Routers)
- All P routers (p1, p2, p3, p4)
- Both Route Reflectors (rr1, rr2)
- All PE routers (pe1, pe2, pe3, pe4)
- **Total: 10 routers**

**No multi-area design** - this is the key difference from medium and complex networks.

## L3VPN Services

### BLUE VPN (Hub-and-Spoke)
- **Hub PE:** pe2 - BLUE_HUB VRF
- **Spoke PEs:** pe1, pe3, pe4 (3 spokes)
- **Topology:** All spokes can reach hub, but spokes cannot communicate directly
- **Route Targets:**
  - Hub: Import [65035:1011, 65035:1030], Export [65035:1030]
  - Spoke: Import [65035:1030], Export [65035:1011]
- **Use case:** Branch-office to headquarters connectivity

### RED VPN (Any-to-Any Mesh)
- **All 4 PEs:** pe1, pe2, pe3, pe4 participate in RED_MESH VRF
- **Topology:** Full mesh - every site can reach every other site
- **Route Targets:** All PEs use RT 65035:2000 (import/export)
- **Use case:** Multi-site data center interconnect

## Files

1. **[infrastructure.yaml](infrastructure.yaml)** (~600 lines)
   - All 10 backbone routers with geographic locations
   - ~20 P2P links with IP addressing
   - 8 CE routers (4 BLUE + 4 RED)
   - 8 end devices
   - Management network
   - Loopback network

2. **[underlay.yaml](underlay.yaml)** (~150 lines)
   - Flat OSPF configuration (Area 0 only)
   - MPLS/LDP on all backbone links
   - BGP route reflector configuration (rr1, rr2)
   - iBGP peerings from all PEs to both RRs

3. **[blue-vpn.yaml](blue-vpn.yaml)** (~80 lines)
   - BLUE_HUB VRF on pe2
   - BLUE_SPOKE VRF on pe1, pe3, pe4
   - eBGP to 4 CE routers
   - Hub-spoke route target configuration

4. **[red-vpn.yaml](red-vpn.yaml)** (~70 lines)
   - RED_MESH VRF on pe1-pe4
   - eBGP to 4 CE routers
   - Mesh route target configuration

5. **[l3vpn-blue-test.yaml](l3vpn-blue-test.yaml)**
   - Traffic test for BLUE VPN hub-spoke topology
   - Spokes to hub traffic pattern
   - Expected path validation

6. **[l3vpn-red-test.yaml](l3vpn-red-test.yaml)**
   - Traffic test for RED VPN any-to-any mesh
   - Cross-site mesh connectivity
   - Expected path validation

## Deployment

### Step 1: Deploy Infrastructure

Apply the infrastructure configuration to create all routers, links, and devices:

```bash
kubectl apply -f examples/controller/transport/default-network/infrastructure.yaml
```

**Wait for routers to be created** (~3-5 minutes for 18 routers):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

Apply the underlay configuration to set up OSPF, BGP, and MPLS:

```bash
kubectl apply -f examples/controller/transport/default-network/underlay.yaml
```

**Verify OSPF convergence:**

```bash
# Log into a P router and check OSPF neighbors
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show ip ospf neighbor"

# Check BGP sessions on route reflectors
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
```

### Step 3: Deploy L3VPN Services

Apply both VPN configurations:

```bash
kubectl apply -f examples/controller/transport/default-network/blue-vpn.yaml
kubectl apply -f examples/controller/transport/default-network/red-vpn.yaml
```

**Verify VPN configuration:**

```bash
# Check VRFs on PE routers
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show vrf"

# Check BGP VPNv4 routes
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp vpnv4 all"
```

### Step 4: Run Traffic Tests (Optional)

Test the flat topology with traffic tests:

```bash
# BLUE VPN hub-spoke test
kubectl apply -f examples/controller/transport/default-network/l3vpn-blue-test.yaml

# RED VPN any-to-any mesh test
kubectl apply -f examples/controller/transport/default-network/l3vpn-red-test.yaml

# Monitor test status
kubectl get traffictest -n network --watch
```

## Verification

### 1. Topology Verification

```bash
# Check all routers are deployed
kubectl get vyosrouter -n network | grep l3vpn | wc -l
# Expected: 18 (10 backbone + 8 CE)

# Verify all routers in Area 0
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show ip ospf interface"
# All interfaces should show "Area 0.0.0.0"

# Check MPLS/LDP neighbors
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show mpls ldp neighbor"
```

### 2. Connectivity Testing

```bash
# Test PE-to-PE reachability via loopbacks
kubectl exec -it -n network <pe1-pod> -- ping 10.0.0.11 -I 10.0.0.7
# Source: pe1 (10.0.0.7) -> Destination: pe4 (10.0.0.11)

# Verify MPLS LSP paths
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show mpls ldp binding"

# Test L3VPN connectivity
kubectl exec -it -n network <device-pod> -- ping <remote-device-ip>
```

### 3. BGP Verification

```bash
# Check RR has all PE clients
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
# Should show 4 PE neighbors + 1 RR peer (rr2) = 5 total

# Verify RR-to-RR peering
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp neighbors 10.0.0.2"
# Should show rr2 as established neighbor
```

## IP Addressing Scheme

### Loopback Addresses (10.0.0.0/24)

| Router Type | Range | Example |
|-------------|-------|---------|
| Route Reflectors | 10.0.0.1-2 | rr1: 10.0.0.1, rr2: 10.0.0.2 |
| Core (P) | 10.0.0.3-6 | p1: 10.0.0.3, p2: 10.0.0.4, p3: 10.0.0.5, p4: 10.0.0.6 |
| Provider Edge (PE) | 10.0.0.7-11 | pe1: 10.0.0.7, pe2: 10.0.0.8, pe3: 10.0.0.10, pe4: 10.0.0.11 |
| Customer Edge (CE) | 10.0.0.80-100 | ce1-spoke: 10.0.0.80, ce1-hub: 10.0.0.100, etc. |

### P2P Link Addressing

| Link Type | Subnet Range | Bandwidth |
|-----------|--------------|-----------|
| Core mesh (P-P) | 172.16.30-50.0/24 | 1Gbps |
| Core-RR | 172.16.10-20.0/24 | 1Gbps |
| P-PE | 172.16.90-170.0/24 | 1Gbps |
| PE-CE (BLUE) | 10.50-90.x.0/24 | 100Mbps |
| PE-CE (RED) | 10.51-91.x.0/24 | 100Mbps |

### Management Network

- **Subnet:** 192.168.122.0/24
- **Gateway:** 192.168.122.1
- **Range:** .11-.16 (P), .21-.22 (RR), .61-.64 (PE), .80-.100 (CE)

## Geographic Distribution

The network spans **UK cities only** for simplicity:

### Core Sites (Tier 1)
- 🇬🇧 London (p1)
- 🇬🇧 Manchester (p2)
- 🇬🇧 Edinburgh (p3)
- 🇬🇧 Glasgow (p4)

### Route Reflector Sites
- 🇨🇭 Zurich, Switzerland (rr1)
- 🇧🇪 Brussels, Belgium (rr2)

### Provider Edge Sites (Tier 2)
- 🇬🇧 London (pe1)
- 🇬🇧 Manchester (pe2)
- 🇬🇧 Edinburgh (pe3)
- 🇬🇧 Glasgow (pe4)

## Key Learning Objectives

This default network is designed to teach fundamental L3VPN concepts:

1. **Flat OSPF Design:** All routers in a single OSPF area (Area 0)
2. **MPLS/LDP Basics:** Label distribution without multi-area complexity
3. **Route Reflector Architecture:** iBGP scaling with RR-to-RR peering
4. **VRF Configuration:** Hub-spoke and mesh VPN topologies
5. **Route Target Policy:** Import/export filtering for VPN isolation
6. **Basic Dual-Homing:** PE routers connected to multiple P routers

## When to Use This Network

**Use default-network for:**
- ✅ Learning L3VPN fundamentals
- ✅ Testing basic MPLS configurations
- ✅ Small lab environments (<20 routers)
- ✅ Single geographic region deployments
- ✅ Proof-of-concept VPN services

**Upgrade to medium-network when you need:**
- Multi-area OSPF with ABRs
- 3-tier hierarchical architecture
- Regional OSPF area segmentation
- 18-30 routers

**Upgrade to complex-network when you need:**
- 4-tier telco architecture with AR tier
- Multi-region design (4+ OSPF areas)
- Large-scale deployment (40+ routers)
- Production-grade redundancy patterns

## Comparison with Other Networks

| Aspect | **Default** | Medium | Complex |
|--------|-------------|--------|---------|
| Backbone routers | **10** | 18 | 44 |
| Total with CE | **18** | 30 | 68 |
| Tiers | **2** | 3 | 4 |
| OSPF areas | **1 (Area 0)** | 3 | 5 |
| ABRs | **None** | 4 PA | 8 PA |
| Hierarchy | **Flat** | Medium | High |
| Use case | **Learning** | Regional | Multi-region |
| Complexity | **Low** | Medium | High |

## Troubleshooting

### Common Issues

**1. All routers must be in Area 0:**
```bash
# Verify all routers see Area 0 neighbors
kubectl exec -it -n network <router-pod> -- vtysh -c "show ip ospf neighbor"
# All neighbors should be in Area 0.0.0.0
```

**2. RR-to-RR peering is critical:**
```bash
# Check both RRs peer with each other
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp neighbors 10.0.0.2"
# Should show rr2 as established peer
```

**3. PE routers must peer with both RRs:**
```bash
# Check PE has 2 BGP neighbors (both RRs)
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp summary"
# Should show connections to 10.0.0.1 and 10.0.0.2
```

**4. VPNv4 routes not propagating:**
```bash
# Check VPNv4 routes on RR
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp vpnv4 all"

# Check VRF routing tables on PE
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip route vrf BLUE_SPOKE"
```

## Cleanup

To remove the default network:

```bash
# Delete traffic tests
kubectl delete traffictest -n network blue-vpn-test
kubectl delete traffictest -n network red-vpn-test

# Delete VPN services
kubectl delete vyosl3vpn -n network blue-vpn-service
kubectl delete vyosl3vpn -n network red-vpn-service

# Delete underlay
kubectl delete vyosunderlay -n network l3vpn-underlay

# Delete infrastructure (this will remove all routers and devices)
kubectl delete vyosinfrastructure -n network l3vpn-infra
```

## Next Steps

After mastering the default network, progress to:

1. **Medium-Network:** Learn multi-area OSPF with 3 areas and ABR configuration
2. **Complex-Network:** Master 4-tier architecture with AR routers and 5 OSPF areas
3. **Custom Topologies:** Design your own network using the patterns learned

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [L3VPN Model Documentation](../../../../docs/network/l3vpn.md)
- [Network Layers](../../../../docs/network/layers.md)
- [Operator Configuration](../../../../controller/config/)
- [Design Documentation](../../../../domains/transport/knowledge/designdoc.md)
- [Medium Network (3-tier)](../medium-network/README.md)
- [Complex Network (4-tier)](../large-network/README.md)
