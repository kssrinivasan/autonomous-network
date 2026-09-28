# Medium 3-Tier Regional Transport Network

This directory contains configuration files for a medium-complexity 3-tier hierarchical telco transport network with multi-area OSPF.

## Network Overview

**Total Infrastructure:**
- **18 backbone routers** across 3 hierarchical tiers
- **~50 P2P backbone links** (10Gbps core, 1Gbps aggregation, 100Mbps access)
- **12 CE routers** (6 BLUE VPN + 6 RED VPN)
- **12 end devices** (6 BLUE VPN + 6 RED VPN)
- **Multi-area OSPF** (Area 0 + 2 regional areas)
- **iBGP with route reflectors** for L3VPN services
- **MPLS/LDP** enabled across all backbone routers

## Network Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: CORE LAYER (Area 0)                                │
│   4 P routers: London, Manchester, Edinburgh, Birmingham    │
│   2 Route Reflectors: Zurich, Brussels                      │
│   Full mesh topology @ 10Gbps                               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: PRE-AGGREGATION LAYER (ABRs - Area 0 + Areas 1-2)  │
│   4 PA routers: Bristol, Leeds, Dublin, Glasgow            │
│   Dual-homed to core @ 10Gbps                               │
│   Act as ABRs between Area 0 and regional areas            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: PROVIDER EDGE LAYER (Areas 1-2)                    │
│   8 PE routers hosting L3VPN services                       │
│   Area 1: Oxford, Cambridge, Brighton, Southampton,        │
│           Liverpool, Newcastle (6 PEs)                      │
│   Area 2: Cork, Aberdeen (2 PEs)                            │
│   Dual-homed to PA routers @ 1Gbps                          │
│   iBGP to route reflectors for VPN routes                   │
└─────────────────────────────────────────────────────────────┘
```

## OSPF Multi-Area Design

### Area 0 (Backbone)
- All P routers (p1-p4)
- Both Route Reflectors (rr1, rr2)
- All Pre-Aggregation routers (pa1-pa4) in ABR role
- **Total: 10 routers**

### Area 1 (Primary Region - UK)
- Access routers: N/A (3-tier design, no AR layer)
- PE routers: pe1, pe2, pe3, pe4, pe5, pe6
- ABRs: pa1, pa2, pa3, pa4
- **Total: 10 routers (6 PE + 4 ABRs)**

### Area 2 (Secondary Region - Ireland/Scotland)
- Access routers: N/A
- PE routers: pe7, pe8
- ABRs: pa3, pa4
- **Total: 4 routers (2 PE + 2 ABRs)**

## L3VPN Services

### BLUE VPN (Hub-and-Spoke)
- **Hub PE:** pe2 (Cambridge, UK) - BLUE_HUB VRF
- **Spoke PEs:** pe1, pe3, pe5, pe7, pe8 (5 spokes)
- **Topology:** All spokes can reach hub, but spokes cannot communicate directly
- **Route Targets:**
  - Hub: Import [65035:1011, 65035:1030], Export [65035:1030]
  - Spoke: Import [65035:1030], Export [65035:1011]
- **Use case:** Branch-office to headquarters connectivity

### RED VPN (Any-to-Any Mesh)
- **All 6 PEs:** pe1, pe2, pe3, pe4, pe5, pe6 participate in RED_MESH VRF
- **Topology:** Full mesh - every site can reach every other site
- **Route Targets:** All PEs use RT 65035:2000 (import/export)
- **Use case:** Multi-site data center interconnect

## Files

1. **[infrastructure.yaml](infrastructure.yaml)** (~1500 lines)
   - All 18 backbone routers with geographic locations
   - ~50 P2P links with IP addressing
   - 12 CE routers (6 BLUE + 6 RED)
   - 12 end devices
   - Management network
   - Loopback network

2. **[underlay.yaml](underlay.yaml)** (~300 lines)
   - Multi-area OSPF configuration (Areas 0, 1, 2)
   - ABR configuration for PA routers
   - MPLS/LDP on all backbone links
   - BGP route reflector configuration (rr1, rr2)
   - iBGP peerings from all PEs to both RRs

3. **[blue-vpn.yaml](blue-vpn.yaml)** (~120 lines)
   - BLUE_HUB VRF on pe2
   - BLUE_SPOKE VRF on pe1, pe3, pe5, pe7, pe8
   - eBGP to 6 CE routers
   - Hub-spoke route target configuration

4. **[red-vpn.yaml](red-vpn.yaml)** (~100 lines)
   - RED_MESH VRF on pe1-pe6
   - eBGP to 6 CE routers
   - Mesh route target configuration

5. **[l3vpn-blue-test.yaml](l3vpn-blue-test.yaml)**
   - Traffic test for BLUE VPN hub-spoke topology
   - Spokes to hub traffic pattern
   - Constant or multi_sine pattern

6. **[l3vpn-red-test.yaml](l3vpn-red-test.yaml)**
   - Traffic test for RED VPN any-to-any mesh
   - Cross-site mesh connectivity
   - Schedule-based traffic pattern

## Deployment


### Step 1: Deploy Infrastructure

Apply the infrastructure configuration to create all routers, links, and devices:

```bash
kubectl apply -f examples/controller/transport/medium-network/infrastructure.yaml
```

**Wait for routers to be created** (~5-8 minutes for 30 routers):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

Apply the underlay configuration to set up OSPF, BGP, and MPLS:

```bash
kubectl apply -f examples/controller/transport/medium-network/underlay.yaml
```

**Verify OSPF convergence:**

```bash
# Log into a PA router and check OSPF neighbors in both Area 0 and Area N
kubectl exec -it -n network <pa-router-pod> -- vtysh -c "show ip ospf neighbor"

# Check ABR status on PA router
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf border-routers"

# Check BGP sessions on route reflectors
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
```

### Step 3: Deploy L3VPN Services

Apply both VPN configurations:

```bash
kubectl apply -f examples/controller/transport/medium-network/blue-vpn.yaml
kubectl apply -f examples/controller/transport/medium-network/red-vpn.yaml
```

**Verify VPN configuration:**

```bash
# Check VRFs on PE routers
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show vrf"

# Check BGP VPNv4 routes
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp vpnv4 all"
```

### Step 4: Run Traffic Tests (Optional)

Test the 3-tier topology with traffic tests:

```bash
# BLUE VPN hub-spoke test
kubectl apply -f examples/controller/transport/medium-network/l3vpn-blue-test.yaml

# RED VPN any-to-any mesh test
kubectl apply -f examples/controller/transport/medium-network/l3vpn-red-test.yaml

# Monitor test status
kubectl get traffictest -n network --watch
```

## Verification

### 1. Topology Verification

```bash
# Check all routers are deployed
kubectl get vyosrouter -n network | grep medium-network | wc -l
# Expected: 30 (18 backbone + 12 CE)

# Verify OSPF multi-area on PA routers
kubectl exec -it -n network <pa-router-pod> -- vtysh -c "show ip ospf interface"

# Check MPLS/LDP neighbors
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show mpls ldp neighbor"
```

### 2. Connectivity Testing

```bash
# Test PE-to-PE reachability via loopbacks
kubectl exec -it -n network <pe1-pod> -- ping 10.0.0.67 -I 10.0.0.60
# Source: pe1 (10.0.0.60) -> Destination: pe8 (10.0.0.67)

# Verify MPLS LSP paths
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show mpls ldp binding"

# Test L3VPN connectivity
kubectl exec -it -n network <device-pod> -- ping <remote-device-ip>
```

### 3. ABR Verification

```bash
# Check PA router sees routes from both Area 0 and Area N
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf database"

# Verify inter-area routes
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show ip route ospf"
```

## IP Addressing Scheme

### Loopback Addresses (10.0.0.0/24)

| Router Type | Range | Example |
|-------------|-------|---------|
| Route Reflectors | 10.0.0.1-2 | rr1: 10.0.0.1, rr2: 10.0.0.2 |
| Core (P) | 10.0.0.3-6 | p1: 10.0.0.3, ..., p4: 10.0.0.6 |
| Pre-Aggregation (PA) | 10.0.0.20-23 | pa1: 10.0.0.20, ..., pa4: 10.0.0.23 |
| Provider Edge (PE) | 10.0.0.60-67 | pe1: 10.0.0.60, ..., pe8: 10.0.0.67 |
| Customer Edge (CE) | 10.0.0.80-91 | ce1-spoke: 10.0.0.80, ... |

### P2P Link Addressing

| Link Type | Subnet Range | Bandwidth |
|-----------|--------------|-----------|
| Core mesh | 172.16.0.0/24 | 10Gbps |
| Core-RR | 172.16.10.0/24 | 10Gbps |
| Core-PA | 172.16.20.0/22 | 10Gbps |
| PA rings | 172.16.30.0/24 | 10Gbps |
| PA-PE | 172.16.40.0/21 | 1Gbps |
| PE-CE | 10.50.0.0/16 | 100Mbps |

### Management Network

- **Subnet:** 192.168.122.0/24
- **Gateway:** 192.168.122.1
- **Range:** .11-.14 (P), .21-.22 (RR), .31-.34 (PA), .61-.68 (PE), .80-.91 (CE)

## Geographic Distribution

The network spans **3 countries** and **16 cities** across UK, Ireland, and international sites:

### Core Sites (Tier 1)
- 🇬🇧 London, Manchester, Edinburgh, Birmingham (UK)
- 🇨🇭 Zurich, Switzerland (RR)
- 🇧🇪 Brussels, Belgium (RR)

### Regional Metro Sites (Tier 2)
- 🇬🇧 Bristol, Leeds (UK - Area 1)
- 🇮🇪 Dublin (Ireland - Area 2)
- 🇬🇧 Glasgow (Scotland - Area 2)

### Edge Sites (Tier 3)
- 🇬🇧 Oxford, Cambridge, Brighton, Southampton, Liverpool, Newcastle (UK - Area 1)
- 🇮🇪 Cork (Ireland - Area 2)
- 🇬🇧 Aberdeen (Scotland - Area 2)

## Benefits for Learning and GNN Training

This medium-complexity topology provides advantages over the default-network:

1. **Hierarchical Architecture:** Introduces PA tier and multi-area OSPF
2. **ABR Concepts:** Learn Area Border Router configuration
3. **Realistic Paths:** 4-6 hop paths vs 2-3 hops (more realistic failure scenarios)
4. **Multi-Area OSPF:** 3 areas provide route summarization opportunities
5. **Scalability:** Can expand to complex-network by adding AR tier and more areas
6. **GNN Training:** More complex than default for better model training
7. **Regional Design:** 2 regions show area boundary behavior

## Comparison with Other Networks

| Aspect | Default | **Medium** | Complex |
|--------|---------|------------|---------|
| Backbone routers | 10 | **18** | 44 |
| Total with CE | 18 | **30** | 68 |
| Tiers | 2 | **3** | 4 |
| OSPF areas | 1 | **3** | 5 |
| ABRs | 0 | **4 PA** | 8 PA |
| Hierarchy | Flat | **Medium** | High |
| Use case | Learning | **Regional** | Multi-region |

## Troubleshooting

### Common Issues

**1. PA Router ABR Issues:**
```bash
# Verify PA router has interfaces in multiple areas
kubectl exec -it -n network <pa-router-pod> -- vtysh -c "show ip ospf interface"

# Check ABR status
kubectl exec -it -n network <pa-router-pod> -- vtysh -c "show ip ospf border-routers"
```

**2. PE Routers Not in Area 0:**
```bash
# Verify PE routers are in regional Area 1 or Area 2 (NOT Area 0)
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip ospf interface"
# Should show Area 1 or Area 2, NOT Area 0
```

**3. BGP sessions down:**
```bash
# Check BGP neighbors
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp neighbors"

# Verify reachability to RRs
kubectl exec -it -n network <pe-router-pod> -- ping 10.0.0.1
```

## Cleanup

To remove the medium-network:

```bash
# Delete traffic tests
kubectl delete traffictest -n network blue-vpn-test
kubectl delete traffictest -n network red-vpn-test

# Delete VPN services
kubectl delete vyosl3vpn -n network blue-vpn-service
kubectl delete vyosl3vpn -n network red-vpn-service

# Delete underlay
kubectl delete vyosunderlay -n network medium-network-underlay

# Delete infrastructure (this will remove all routers and devices)
kubectl delete vyosinfrastructure -n network medium-network-infra
```

## Next Steps

1. **Expand to 4-tier:** Add AR routers between PA and PE layers
2. **Add more areas:** Expand Area 2 and add Area 3 for additional regions
3. **OSPF summarization:** Configure area ranges on PA routers
4. **Traffic Engineering:** Implement RSVP-TE tunnels
5. **Chaos Testing:** Use NetworkFailure CRDs to test resilience
6. **GNN Training:** Collect topology and metrics data for model training

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [L3VPN Model Documentation](../../../../docs/network/l3vpn.md)
- [Network Layers](../../../../docs/network/layers.md)
- [Operator Configuration](../../../../controller/config/)
- [Design Documentation (Updated with PA/AR)](../../../../domains/transport/knowledge/designdoc.md)
