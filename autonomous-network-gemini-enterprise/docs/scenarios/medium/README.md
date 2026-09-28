# L3VPN Fault Injection Scenario with Impact Assessment (Medium Network)

This directory describes how to demonstrate the full autonomous network lifecycle for the **medium L3VPN network** (3-tier hierarchical architecture with multi-area OSPF), including fault injection, GNN-based detection, root cause analysis, and proactive impact assessment before remediation.

## Overview

The demonstration workflow:
1. **Inject a fault** into the network
2. **Detect the fault** using GNN embeddings that identify anomalies
3. **Identify root cause** via embeddings
4. **Suggest remediation** based on the identified fault
5. **Before applying remediation**: Perform an **impact assessment** to understand how traffic will be rerouted and what congestion effects will occur
6. **Query traffic rerouting** and assess congestion impact before actually fixing the problem

The key requirement is that the remediation must have a **measurable traffic impact** that can be assessed via queries about path changes and congestion.

---

## Network Topology Summary

Based on [examples/controller/transport/medium-network/README.md](../../../examples/controller/transport/medium-network/README.md), the network consists of:

**Physical Infrastructure (3-Tier Hierarchical Design):**
- **Tier 1 - Core:** 4 P routers (p1-p4) + 2 RRs (rr1, rr2) in full mesh @ 10Gbps
  - Cities: London, Manchester, Edinburgh, Birmingham (UK), Zurich (RR), Brussels (RR)
- **Tier 2 - Pre-Aggregation:** 4 PA routers (pa1-pa4) dual-homed to core @ 10Gbps
  - Cities: Bristol, Leeds, Dublin, Glasgow
  - **PA routers are Area Border Routers (ABRs)** connecting regional areas to backbone
- **Tier 3 - Provider Edge:** 8 PE routers (pe1-pe8) @ 1Gbps
  - Area 1 (UK): pe1, pe2, pe3, pe4, pe5, pe6 (6 PEs)
  - Area 2 (Ireland/Scotland): pe7, pe8 (2 PEs)
- **Total:** 18 backbone routers

**L3VPN Services:**
- **BLUE VPN** (hub-and-spoke): Hub at `pe2` (Cambridge, UK), Spokes at pe1, pe3, pe5, pe7, pe8 (5 spokes)
- **RED VPN** (any-to-any mesh): pe1-pe6 participate (6 PEs)

**Underlay Protocols:**
- **Multi-area OSPF:** Area 0 (backbone) + Area 1 (UK) + Area 2 (Ireland/Scotland)
- MPLS with LDP across all backbone routers
- iBGP with route reflectors (rr1, rr2)

---

## Recommended Failure Scenarios

Based on the network topology and available fault types from [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml), the failure scenarios have been split into individual detailed guides:

1. **[Scenario 1: OSPF Cost Inflation on Core Link](scenario_1_ospf_cost_inflation.md) ⭐ RECOMMENDED**
   - **Fault Type:** `OSPF_COST_INFLATION`
   - **Target:** `p1` eth1 (p1-p2 link)
   - **Demonstrates:** Inter-region impact, multi-hop rerouting through core mesh, multi-tier cascade, and multi-area OSPF ABR path recalculation.

2. **[Scenario 2: Pre-Aggregation ABR to Core Link Failure](scenario_2_abr_core_link_failure.md)**
   - **Fault Type:** `LINK_DOWN`
   - **Target:** `pa1` to `p1` link
   - **Demonstrates:** Regional impact (Area 1 only), dual-homed ABR resilience, and OSPF inter-area route updates.

3. **[Scenario 3: Area Border Router Failure](scenario_3_abr_router_failure.md) ⭐ NEW FOR MEDIUM NETWORK**
   - **Fault Type:** `ROUTER_DOWN`
   - **Target:** ABR `pa3` (Dublin)
   - **Demonstrates:** Area isolation, failover to remaining ABR `pa4`, cross-tier cascade (PA→PE), surviving ABR overload, and capacity planning.

4. **[Scenario 4: VRF Route Target Misconfiguration](scenario_4_vrf_rt_misconfig.md)**
   - **Fault Type:** `VRF_RT_MISCONFIGURATION`
   - **Target:** `pe7` BLUE_SPOKE VRF
   - **Demonstrates:** Completely silent cross-area reachability loss (Area 2 spoke isolated from Area 1 hub).

5. **[Scenario 5: Traffic Planning and Capacity Management](scenario_5_traffic_capacity_planning.md) ⭐ RECOMMENDED FOR CAPACITY DEMO**
   - **Workflow:** Proactive traffic planning when adding a new spoke site in Area 2 (`ce9-spoke` @ 180Mbps) to hub in Area 1, predicting ABR uplink capacity bottleneck, and recommending / implementing parallel links via designer agent.

---

## Final Recommendation

**Use Scenario 1: OSPF Cost Inflation on `p1-p2` link OR Scenario 3: ABR Router Down**

**Rationale for Scenario 1:**
1. **Best for impact assessment** — forces traffic rerouting with measurable congestion effects
2. **Silent failure** — no control-plane alarms, requires GNN to detect via performance anomalies
3. **Realistic** — OSPF cost misconfigurations happen in production (typo during maintenance)
4. **Query-friendly** — you can easily query OSPF LSDB, simulate new paths, and assess congestion before remediation
5. **Demonstrates full workflow** — fault injection → GNN detection → root cause → remediation suggestion → **impact assessment** → safe remediation
6. **Multi-area complexity** — shows ABR path recalculation across areas

**Rationale for Scenario 3:**
1. **Unique to medium/complex networks** — requires ABR architecture
2. **Regional isolation** — demonstrates Area 2 dependency on dual-ABR design
3. **Capacity planning** — shows bottleneck at surviving ABR pa4
4. **Real-world scenario** — ABR failures are critical in production networks
5. **Multi-agent coordination** — can trigger capacity upgrade recommendations

---

## Implementation Steps

### Phase 1: Setup Baseline Traffic

**Critical Files:**
- [examples/controller/transport/medium-network/l3vpn-blue-test.yaml](../../../examples/controller/transport/medium-network/l3vpn-blue-test.yaml) — sample BLUE VPN traffic test
- [examples/controller/transport/medium-network/l3vpn-red-test.yaml](../../../examples/controller/transport/medium-network/l3vpn-red-test.yaml) — sample RED VPN traffic test

**Setup Steps:**
1. Deploy the L3VPN medium network (BLUE hub-and-spoke, RED mesh)
2. Create TrafficTest resources to generate baseline traffic:
   - Multiple spoke-to-hub flows across areas (Area 1 spokes → Area 1 hub, Area 2 spokes → Area 1 hub)
   - Use `pattern_type: multi_sine` with `time_reference: wall_clock` for realistic time-of-day variation
   - Enable `bidirectional: true` to simulate realistic asymmetric traffic
   - Duration: Long-running (3600+ seconds) for continuous monitoring

**Example TrafficTest YAML (Cross-Area):**
```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: blue-vpn-baseline-spoke7-area2
  namespace: network
spec:
  vpnRef: blue-vpn-service
  source_devices:
    - ce7-spoke
  destination_device: ce1-hub
  protocol: TCP
  bandwidth: 60Mbps
  duration: 7200
  pattern_type: multi_sine
  bidirectional: true
  reverse_bandwidth: 10Mbps
  pattern_config:
    base_rate: 50Mbps
    time_reference: wall_clock
    components:
      - period: 3600
        amplitude: 25Mbps
        phase_offset: 0
    noise_stddev_pct: 5.0
    min_rate: 20Mbps
    max_rate: 90Mbps
```

3. Wait for traffic to stabilize (5-10 minutes)
4. Verify metrics are flowing to Cloud Monitoring via Ops Agent

### Phase 2: Inject Fault

**Critical Files:**
- [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml) — NetworkFailure CRD definition
- [examples/controller/transport/medium-network/infrastructure.yaml](../../../examples/controller/transport/medium-network/infrastructure.yaml) — infrastructure descriptor

**Injection Steps (Scenario 1):**
1. Create NetworkFailure resource for OSPF cost inflation on `p1-p2`:
```bash
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: ospf-cost-inflation-p1-p2-inter-region
  namespace: network
spec:
  failureType: OSPF_COST_INFLATION
  infrastructureRef: medium-network-infra
  target:
    router: p1
    interface: eth1
  parameters:
    ospf_cost: 65535
EOF
```

**Injection Steps (Scenario 3 - ABR Down):**
1. Create NetworkFailure resource for ABR router down:
```bash
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: router-down-pa3-abr
  namespace: network
spec:
  failureType: ROUTER_DOWN
  infrastructureRef: medium-network-infra
  target:
    router: pa3
EOF
```

2. Monitor NetworkFailure status:
```bash
kubectl get networkfailure -n network -w
```

3. Wait for `status.phase: Active`

4. Observe traffic metrics:
   - Latency increase on affected flows
   - Path changes in OSPF LSDB
   - ABR summary LSA updates
   - Potential packet loss during reconvergence
   - Congestion on alternate paths

### Phase 3: GNN Detection & Root Cause Analysis

**Critical Files:**
- [domains/transport/gnn/](../../../domains/transport/gnn/) — GNN training and serving code
- [docs/gnn/Readme.md](../../gnn/Readme.md) — GNN documentation

**Detection Steps:**
1. Wait for GNN to process updated metrics from Cloud Monitoring
2. Query GNN embeddings for anomalies:
   - Router `p1` and `p2` (or `pa3`) should show anomalous latency patterns
   - Link `p1-p2` should show reduced utilization (traffic rerouted)
   - Alternate paths (`p1-p3-p2`, `p1-p4-p2`) or surviving ABR should show increased utilization
   - ABRs show inter-area route changes
3. GNN identifies root cause: OSPF cost anomaly on `p1-p2` or ABR `pa3` down
4. GNN suggests remediation: "Restore OSPF cost to 1 on p1 eth1" or "Restore router pa3"

### Phase 4: Impact Assessment (Query-Based)

**Before Applying Remediation:**

**Query 1: Current OSPF Paths with ABR Awareness**
```bash
# SSH to PA router and query OSPF routes
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf route"
kubectl exec -it -n network <pa1-pod> -- vtysh -c "show ip ospf border-routers"
```
Expected: Routes show alternate paths via remaining ABRs or alternate core paths

**Query 2: Current Link Utilization (Multi-Tier)**
```bash
# Query Cloud Monitoring for interface metrics across tiers
gcloud monitoring time-series list \
  --filter='metric.type="agent.googleapis.com/interface/traffic" AND resource.labels.instance_id="network-vm"' \
  --format=json
```
Expected: High utilization on surviving ABR uplinks or alternate core paths

**Query 3: Simulate Remediation Impact with ABR Context**
- **Question**: "If we restore cost to 1 (or restore pa3), what paths will change across areas?"
- **Query OSPF LSDB** including inter-area summary LSAs
- **Identify affected flows**: All traffic crossing Area 0 that was rerouted

**Query 4: Assess Congestion Risk at ABR Tier**
- **Calculate rerouted bandwidth** at ABR uplinks
- **Check current utilization on affected ABR links**
- **Check capacity** of ABR links (typically 10Gbps)
- **Prediction**: Restoring will shift traffic back to balanced ABR distribution

**Query 5: Latency Impact Prediction (Cross-Area)**
- **Current latency** on Area 2 → Area 1 path via alternate route
- **Predicted latency** after restoration via optimal ABR path
- **Expected improvement**: measured in ms

### Phase 5: Apply Remediation

**Remediation Steps:**
1. Delete the NetworkFailure resource:
```bash
kubectl delete networkfailure <failure-name> -n network
```

2. Monitor status: `phase: Restoring → Restored`

3. Verify restoration:
```bash
kubectl exec -it -n network <affected-router-pod> -- vtysh -c "show ip ospf interface"
# or
kubectl exec -it -n network <affected-router-pod> -- vtysh -c "show ip ospf neighbor"
```

4. Observe traffic metrics:
   - Latency decreases on affected flows
   - Traffic returns to optimal paths through restored element
   - Utilization on alternate paths decreases
   - ABR summary LSAs updated

5. Verify GNN detects restoration and anomaly resolution

### Phase 6: Validation

**Verification Steps:**
1. Compare pre-fault and post-remediation metrics:
   - Latency should return to baseline
   - Packet loss should be zero (except brief reconvergence)
   - Link utilization should match original distribution
   - ABR load balanced across multiple ABRs
2. Verify GNN embeddings show "healthy" state
3. Confirm all VPN flows are operational across areas
4. Check ABR summary LSA propagation is correct
5. Verify no unexpected side effects

---

## Critical Files Reference

**Network Configuration:**
- [examples/controller/transport/medium-network/infrastructure.yaml](../../../examples/controller/transport/medium-network/infrastructure.yaml) — physical topology
- [examples/controller/transport/medium-network/underlay.yaml](../../../examples/controller/transport/medium-network/underlay.yaml) — multi-area OSPF, BGP, MPLS config
- [examples/controller/transport/medium-network/blue-vpn.yaml](../../../examples/controller/transport/medium-network/blue-vpn.yaml) — BLUE VPN VRF config
- [examples/controller/transport/medium-network/red-vpn.yaml](../../../examples/controller/transport/medium-network/red-vpn.yaml) — RED VPN VRF config

**Fault Injection:**
- [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml) — NetworkFailure CRD
- [controller/src/networkfailure/](../../../controller/src/networkfailure/) — NetworkFailure operator code

**Traffic Testing:**
- [controller/config/traffic.yaml](../../../controller/config/infrastructure/traffic.yaml) — TrafficTest CRD
- [traffic-agent/](../../../traffic-agent/) — Go traffic generator
- [traffic-agent/internal/patterns/](../../../traffic-agent/internal/patterns/) — traffic pattern implementations

**Documentation:**
- [examples/controller/transport/medium-network/README.md](../../../examples/controller/transport/medium-network/README.md) — network topology details
- [docs/scenarios/transport.md](../transport.md) — existing transport RCA scenario
- [docs/network/metrics.md](../../network/metrics.md) — metrics collection details

---

## Summary

### Scenario Comparison

**Scenario 1: OSPF Cost Inflation** ⭐ **RECOMMENDED FOR REMEDIATION DEMO**
- ✅ Silent failure requiring GNN detection
- ✅ Forces measurable traffic rerouting across areas
- ✅ Clear congestion impact on alternate paths
- ✅ Perfect for impact assessment queries before remediation
- ✅ Demonstrates: Fault → Detection → Root Cause → Impact Assessment → Remediation
- ✅ Multi-area OSPF complexity with ABR path recalculation

**Scenario 3: ABR Router Down** ⭐ **RECOMMENDED FOR ABR RESILIENCE DEMO**
- ✅ Unique to hierarchical networks with ABRs
- ✅ Regional isolation impact (Area 2 affected, Area 1 unaffected)
- ✅ Capacity bottleneck at surviving ABR
- ✅ Real-world critical failure scenario
- ✅ Demonstrates: ABR Failure → Regional Impact → Capacity Planning → Restoration

**Scenario 5: Traffic Planning & Capacity** ⭐ **RECOMMENDED FOR CAPACITY DEMO**
- ✅ Proactive capacity planning before deployment
- ✅ Multi-agent coordination (Impact → Designer → Test)
- ✅ Risk mitigation through pre-assessment
- ✅ Demonstrates: Planning → Assessment → Upgrade → Validation
- ✅ Cross-area traffic planning with ABR uplink capacity

---

## Network Characteristics Specific to Medium Network

**Advantages for Learning:**
- ✅ **3-tier hierarchical topology** - Realistic telco architecture
- ✅ **Multi-area OSPF** - Area 0, Area 1, Area 2 with ABRs
- ✅ **ABR concepts** - Pre-aggregation routers as Area Border Routers
- ✅ **Regional isolation** - Area 1 (UK) vs Area 2 (Ireland/Scotland)
- ✅ **Moderate scale** - 18 backbone routers, good for GNN training
- ✅ **Inter-area routing** - Summary LSAs and ABR path calculation

**Advantages over Default Network:**
- ✅ **Multi-area failures** - Can demonstrate area isolation
- ✅ **ABR redundancy** - Dual-ABR design per region
- ✅ **Realistic paths** - 5-7 hops vs 2-4 hops in default
- ✅ **Better GNN training** - More complex topology and failure modes

**Limitations vs Complex Network:**
- ⚠️ **No AR tier** - Cannot demonstrate 4-tier hierarchy
- ⚠️ **Only 3 areas** - Limited multi-region complexity
- ⚠️ **Fewer PEs** - 8 PEs vs 12 in complex network

**When to Use Medium Network:**
- Learning multi-area OSPF and ABR concepts
- Demonstrating regional network isolation
- Testing ABR failover and redundancy
- Training GNN models with moderate complexity
- Scenarios requiring cross-area VPN traffic

**When to Upgrade:**
- Move to **complex-network** for 4-tier architecture with AR routers
- Need 5+ OSPF areas for multi-region design
- Require 40+ routers for large-scale GNN training
- Production-grade redundancy patterns needed
