# Complex Multi-Tier European Transport Network

This directory contains configuration files for a comprehensive 4-tier hierarchical telco transport network spanning multiple European regions.

## Network Overview

**Total Infrastructure:**
- **44 backbone routers** across 4 hierarchical tiers
- **~115 P2P backbone links** (10Gbps core, 1Gbps aggregation, 100Mbps-1Gbps access)
- **24 CE routers** (12 BLUE VPN + 12 RED VPN)
- **23 end devices** (11 BLUE VPN + 12 RED VPN)
- **Multi-area OSPF** (Area 0 + 4 regional areas)
- **iBGP with route reflectors** for L3VPN services
- **MPLS/LDP** enabled across all backbone routers

## Network Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: CORE LAYER (Area 0)                                 │
│   6 P routers: London, Frankfurt, Paris, Amsterdam,         │
│                Madrid, Milan                                 │
│   2 Route Reflectors: Zurich, Brussels                      │
│   Full mesh topology @ 10Gbps                               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: PRE-AGGREGATION LAYER (Area 0)                      │
│   8 PA routers: Manchester, Birmingham, Hamburg, Munich,    │
│                 Lyon, Marseille, Barcelona, Rome            │
│   Dual-homed to core @ 10Gbps                               │
│   Regional rings for redundancy                             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: ACCESS LAYER (Areas 1-4)                            │
│   16 AR routers distributed across regional areas           │
│   Area 1 (UK): 4 routers                                    │
│   Area 2 (Germany): 4 routers                               │
│   Area 3 (France): 4 routers                                │
│   Area 4 (Iberia/Italy): 4 routers                          │
│   Dual-homed to pre-agg @ 1Gbps                             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Tier 4: PROVIDER EDGE LAYER (Areas 1-4)                     │
│   12 PE routers hosting L3VPN services                      │
│   Dual-homed to access routers @ 100Mbps-1Gbps              │
│   iBGP to route reflectors for VPN routes                   │
└─────────────────────────────────────────────────────────────┘
```

## OSPF Multi-Area Design

### Area 0 (Backbone)
- All P routers (p1-p6)
- Both Route Reflectors (rr1, rr2)
- All Pre-Aggregation routers (pa1-pa8)
- **Total: 16 routers**

### Area 1 (UK Region)
- Access routers: ar1, ar2, ar3, ar4
- PE routers: pe1, pe2, pe3, pe4
- ABRs: pa1, pa2
- **Total: 10 routers (4 AR + 4 PE + 2 ABRs)**

### Area 2 (Germany Region)
- Access routers: ar5, ar6, ar7, ar8
- PE routers: pe7, pe8, pe9, pe10
- ABRs: pa3, pa4
- **Total: 10 routers (4 AR + 4 PE + 2 ABRs)**

### Area 3 (France Region)
- Access routers: ar9, ar10, ar11, ar12
- PE routers: pe5, pe6, pe11 (+ cross-region PEs)
- ABRs: pa5, pa6
- **Total: 10 routers (4 AR + 4 PE + 2 ABRs)**

### Area 4 (Iberia/Italy Region)
- Access routers: ar13, ar14, ar15, ar16
- PE routers: pe12 (+ cross-region PEs)
- ABRs: pa7, pa8
- **Total: 8 routers (4 AR + 2 PE + 2 ABRs)**

## L3VPN Services

### BLUE VPN (Hub-and-Spoke)
- **Hub PE:** pe2 (Cambridge, UK) - BLUE_HUB VRF
- **Spoke PEs:** pe1, pe3, pe4, pe5, pe6, pe7, pe8, pe9, pe10, pe11, pe12
- **Topology:** All spokes can reach hub, but spokes cannot communicate directly
- **Route Targets:**
  - Hub: Import [65035:1011, 65035:1030], Export [65035:1030]
  - Spoke: Import [65035:1030], Export [65035:1011]
- **Use case:** Branch-office to headquarters connectivity

### RED VPN (Any-to-Any Mesh)
- **All 12 PEs:** pe1 through pe12 participate in RED_MESH VRF
- **Topology:** Full mesh - every site can reach every other site
- **Route Targets:** All PEs use RT 65035:2000 (import/export)
- **Use case:** Multi-site data center interconnect

## Files

1. **[infrastructure.yaml](infrastructure.yaml)** (~3600 lines)
   - All 44 backbone routers with geographic locations
   - ~115 P2P links with IP addressing
   - 24 CE routers (12 BLUE + 12 RED)
   - 23 end devices
   - Management network
   - Loopback network

2. **[underlay.yaml](underlay.yaml)** (~650 lines)
   - Multi-area OSPF configuration (Areas 0-4)
   - ABR configuration for PA routers
   - MPLS/LDP on all backbone links
   - BGP route reflector configuration (rr1, rr2)
   - iBGP peerings from all PEs to both RRs

3. **[blue-vpn.yaml](blue-vpn.yaml)** (~200 lines)
   - BLUE_HUB VRF on pe2
   - BLUE_SPOKE VRF on pe1, pe3-pe12
   - eBGP to 12 CE routers
   - Hub-spoke route target configuration

4. **[red-vpn.yaml](red-vpn.yaml)** (~200 lines)
   - RED_MESH VRF on all 12 PEs
   - eBGP to 12 CE routers
   - Mesh route target configuration

5. **[l3vpn-blue-test.yaml](l3vpn-blue-test.yaml)**
   - Traffic test for BLUE VPN hub-spoke topology
   - Multi-region sources to hub destination
   - Multi-sine pattern with Gaussian noise
   - Expected path validation

6. **[l3vpn-red-test.yaml](l3vpn-red-test.yaml)**
   - Traffic test for RED VPN any-to-any mesh
   - Cross-region mesh connectivity
   - Schedule-based traffic pattern
   - Full mesh path validation

## Deployment

### Step 1: Deploy Infrastructure

Apply the infrastructure configuration to create all routers, links, and devices:

```bash
kubectl apply -f examples/controller/transport/large-network/infrastructure.yaml
```

**Wait for routers to be created** (~10-15 minutes for 44 routers):

```bash
kubectl get vyosrouter -n network --watch
```

### Step 2: Deploy Underlay

Apply the underlay configuration to set up OSPF, BGP, and MPLS:

```bash
kubectl apply -f examples/controller/transport/large-network/underlay.yaml
```

**Verify OSPF convergence:**

```bash
# Log into a PE router and check OSPF neighbors
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip ospf neighbor"

# Check BGP sessions on route reflectors
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp summary"
```

### Step 3: Deploy L3VPN Services

Apply both VPN configurations:

```bash
kubectl apply -f examples/controller/transport/large-network/blue-vpn.yaml
kubectl apply -f examples/controller/transport/large-network/red-vpn.yaml
```

**Verify VPN configuration:**

```bash
# Check VRFs on PE routers
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show vrf"

# Check BGP VPNv4 routes
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp vpnv4 all"
```

### Step 4: Run Traffic Tests (Optional)

Test the multi-tier topology with traffic tests:

```bash
# BLUE VPN hub-spoke test
kubectl apply -f examples/controller/transport/large-network/l3vpn-blue-test.yaml

# RED VPN any-to-any mesh test
kubectl apply -f examples/controller/transport/large-network/l3vpn-red-test.yaml

# Monitor test status
kubectl get traffictest -n network --watch
```

## Verification

### 1. Topology Verification

```bash
# Check all routers are deployed
kubectl get vyosrouter -n network | wc -l
# Expected: 44 backbone + 24 CE = 68 total

# Verify OSPF areas
kubectl exec -it -n network <pa-router-pod> -- vtysh -c "show ip ospf border-routers"

# Check MPLS/LDP neighbors
kubectl exec -it -n network <p-router-pod> -- vtysh -c "show mpls ldp neighbor"
```

### 2. Connectivity Testing

```bash
# Test PE-to-PE reachability via loopbacks
kubectl exec -it -n network <pe1-pod> -- ping 10.0.0.67 -I 10.0.0.7
# Source: pe1 (10.0.0.7) -> Destination: pe12 (10.0.0.67)

# Verify MPLS LSP paths
kubectl exec -it -n network <pe1-pod> -- vtysh -c "show mpls ldp binding"

# Test L3VPN connectivity
kubectl exec -it -n network <device-pod> -- ping <remote-device-ip>
```

### 3. Path Diversity Testing

```bash
# Use traceroute to verify multi-hop paths through PA and AR layers
kubectl exec -it -n network <pe1-pod> -- traceroute 10.0.0.67 -s 10.0.0.7

# Expected path: pe1 → ar1 → pa1 → p1 → p5 → pa7 → ar10 → pe12
# (~6-10 hops depending on routing)
```

### 4. Failover Testing

```bash
# Simulate link failure
kubectl exec -it -n network <router-pod> -- vtysh -c "conf t" -c "interface eth1" -c "shutdown"

# Verify alternate paths are used
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip route"

# Monitor convergence time with BFD
kubectl exec -it -n network <router-pod> -- vtysh -c "show bfd peers"
```

## IP Addressing Scheme

### Loopback Addresses (10.0.0.0/24)

| Router Type | Range | Example |
|-------------|-------|---------|
| Route Reflectors | 10.0.0.1-2 | rr1: 10.0.0.1 |
| Core (P) | 10.0.0.3-6, 10.0.0.13-14 | p1: 10.0.0.3 |
| Pre-Aggregation (PA) | 10.0.0.20-27 | pa1: 10.0.0.20 |
| Access (AR) | 10.0.0.40-55 | ar1: 10.0.0.40 |
| Provider Edge (PE) | 10.0.0.7-11, 10.0.0.60-67 | pe1: 10.0.0.7 |
| Customer Edge (CE) | 10.0.0.80-112 | ce1-spoke: 10.0.0.80 |

### P2P Link Addressing

| Link Type | Subnet Range | Bandwidth |
|-----------|--------------|-----------|
| Core mesh | 172.16.0.0/22 | 10Gbps |
| Core-RR | 172.16.10.0/24 | 10Gbps |
| Core-PA | 172.16.20.0/22 | 10Gbps |
| PA rings | 172.16.30.0/24 | 10Gbps |
| PA-AR | 172.16.40.0/21 | 1Gbps |
| AR-PE | 172.16.50.0/21 | 100Mbps-1Gbps |

### Management Network

- **Subnet:** 192.168.122.0/24
- **Gateway:** 192.168.122.1
- **Range:** .11-.16 (P), .21-.22 (RR), .31-.38 (PA), .41-.56 (AR), .61-.72 (PE), .80-.104 (CE), .110-.141 (devices)

## Geographic Distribution

The network spans **8 countries** and **38 cities** across Europe:

### Core Sites (Tier 1)
- 🇬🇧 London, UK
- 🇩🇪 Frankfurt, Germany
- 🇫🇷 Paris, France
- 🇳🇱 Amsterdam, Netherlands
- 🇪🇸 Madrid, Spain
- 🇮🇹 Milan, Italy
- 🇨🇭 Zurich, Switzerland (RR)
- 🇧🇪 Brussels, Belgium (RR)

### Regional Metro Sites (Tier 2)
- 🇬🇧 Manchester, Birmingham
- 🇩🇪 Hamburg, Munich
- 🇫🇷 Lyon, Marseille
- 🇪🇸 Barcelona
- 🇮🇹 Rome

### Access Sites (Tier 3)
- 🇬🇧 Leeds, Sheffield, Bristol, Cardiff
- 🇩🇪 Berlin, Dresden, Stuttgart, Nuremberg
- 🇫🇷 Bordeaux, Toulouse, Lille, Nice
- 🇪🇸 Valencia, Seville
- 🇮🇹 Naples, Turin

### Edge Sites (Tier 4)
- 🇬🇧 Oxford, Cambridge, Brighton, Southampton
- 🇩🇪 Cologne, Hanover, Frankfurt-Edge, Leipzig
- 🇫🇷 Nantes, Strasbourg, Montpellier
- 🇪🇸 Bilbao

## Benefits for GNN Training

This complex topology provides significant advantages for GNN-based network analysis:

1. **Large Graph:** ~44 nodes vs 10 in default-network (4.4× increase)
2. **Realistic Paths:** 6-10 hop paths vs 2-3 hops (more realistic failure scenarios)
3. **Multi-Tier Failures:** Can simulate failures at different layers (access, aggregation, core)
4. **Regional Diversity:** 4 OSPF areas enable area-based failure testing
5. **Path Diversity:** Multiple disjoint paths between any PE pair
6. **Hierarchical Features:** Different router roles (P, PA, AR, PE) for node classification
7. **Traffic Patterns:** Cross-region traffic with varying latencies and bandwidths

## Troubleshooting

### Common Issues

**1. Routers not starting:**
```bash
kubectl logs -n network <router-pod> --previous
kubectl describe vyosrouter -n network <router-name>
```

**2. OSPF adjacencies not forming:**
```bash
# Check interface status
kubectl exec -it -n network <router-pod> -- vtysh -c "show ip ospf interface"

# Verify OSPF configuration
kubectl exec -it -n network <router-pod> -- vtysh -c "show run" | grep ospf
```

**3. BGP sessions down:**
```bash
# Check BGP neighbors
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip bgp neighbors"

# Verify reachability to RRs
kubectl exec -it -n network <pe-router-pod> -- ping 10.0.0.1
```

**4. MPLS/LDP issues:**
```bash
# Check LDP sessions
kubectl exec -it -n network <router-pod> -- vtysh -c "show mpls ldp neighbor"

# Verify MPLS forwarding
kubectl exec -it -n network <router-pod> -- vtysh -c "show mpls table"
```

**5. VPN routes not propagating:**
```bash
# Check VRF routing tables
kubectl exec -it -n network <pe-router-pod> -- vtysh -c "show ip route vrf BLUE_SPOKE"

# Verify BGP VPNv4 routes
kubectl exec -it -n network <rr1-pod> -- vtysh -c "show ip bgp vpnv4 all"
```

## Cleanup

To remove the complex network:

```bash
# Delete traffic tests
kubectl delete traffictest -n network blue-vpn-cross-region-test
kubectl delete traffictest -n network red-vpn-any-to-any-test

# Delete VPN services
kubectl delete vyosl3vpn -n network blue-vpn-service
kubectl delete vyosl3vpn -n network red-vpn-service

# Delete underlay
kubectl delete vyosunderlay -n network complex-network-underlay

# Delete infrastructure (this will remove all routers and devices)
kubectl delete vyosinfrastructure -n network complex-network-infra
```

## Next Steps

1. **Enable BFD:** Configure BFD for sub-second failure detection
2. **Add Traffic Engineering:** Implement RSVP-TE tunnels for guaranteed bandwidth
3. **Deploy Chaos Tests:** Use NetworkFailure CRDs to simulate failures
4. **Expand VPNs:** Add more VPN services (ORANGE, GREEN, etc.)
5. **GNN Training:** Collect metrics and topology data for GNN model training
6. **Dashboard Visualization:** View the full topology in the Flutter dashboard

## References

- [Network Simulator Documentation](../../../../docs/network/Readme.md)
- [L3VPN Model Documentation](../../../../docs/network/l3vpn.md)
- [Network Layers](../../../../docs/network/layers.md)
- [Operator Configuration](../../../../controller/config/)
