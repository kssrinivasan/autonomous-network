# L3VPN Fault Injection Scenario with Impact Assessment (Default Network)

This directory describes how to demonstrate the full autonomous network lifecycle for the **default L3VPN network** (flat 2-tier architecture), including fault injection, GNN-based detection, root cause analysis, and proactive impact assessment before remediation.

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

Based on [examples/controller/transport/default-network/README.md](../../../examples/controller/transport/default-network/README.md), the network consists of:

**Physical Infrastructure (Flat 2-Tier Design):**
- **Tier 1 - Core:** 4 P routers (p1-p4) in full mesh @ 1Gbps
  - Cities: London, Manchester, Edinburgh, Glasgow (UK)
- **Route Reflectors:** 2 RRs (rr1, rr2) @ 1Gbps
  - Cities: Zurich (Switzerland), Brussels (Belgium)
- **Tier 2 - Provider Edge:** 4 PE routers (pe1-pe4) @ 1Gbps
  - Cities: London, Manchester, Edinburgh, Glasgow (UK)
- **Total:** 10 backbone routers

**L3VPN Services:**
- **BLUE VPN** (hub-and-spoke): Hub at `pe2` (Manchester, UK), Spokes at pe1, pe3, pe4 (3 spokes)
- **RED VPN** (any-to-any mesh): All 4 PEs participate

**Underlay Protocols:**
- **Flat OSPF:** Single Area 0 (all routers in backbone area)
- MPLS with LDP across all backbone routers
- iBGP with route reflectors (rr1, rr2)

---

## Recommended Failure Scenarios

Based on the network topology and available fault types from [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml), the failure scenarios have been split into individual detailed guides:

1. **[Scenario 1: OSPF Cost Inflation on Core Link](scenario_1_ospf_cost_inflation.md) ⭐ RECOMMENDED**
   - **Fault Type:** `OSPF_COST_INFLATION`
   - **Target:** `p1` eth1 (p1-p2 link)
   - **Demonstrates:** CAScading impact, multi-hop rerouting through core mesh, silent performance anomaly detection.

2. **[Scenario 2: Core Router Link Failure](scenario_2_core_link_failure.md)**
   - **Fault Type:** `LINK_DOWN`
   - **Target:** `p1` to `p3` link
   - **Demonstrates:** Core mesh resiliency, OSPF adjacency down alarms, and flat topology impact.

3. **[Scenario 3: VRF Route Target Misconfiguration](scenario_3_vrf_rt_misconfig.md)**
   - **Fault Type:** `VRF_RT_MISCONFIGURATION`
   - **Target:** `pe1` BLUE_SPOKE VRF
   - **Demonstrates:** Completely silent reachability loss, BGP sessions remain established, VRF isolation analysis.

4. **[Scenario 4: Traffic Planning and Capacity Management](scenario_4_traffic_capacity_planning.md) ⭐ RECOMMENDED FOR CAPACITY DEMO**
   - **Workflow:** Proactive traffic planning when adding a new spoke site (`ce5-spoke` @ 150Mbps), link bottleneck prediction, and multi-agent execution (Impact → Designer → Test → Operator).

---

## Final Recommendation

**Use Scenario 1: OSPF Cost Inflation on `p1-p2` link**

**Rationale:**
1. **Best for impact assessment** — forces traffic rerouting with measurable congestion effects
2. **Silent failure** — no control-plane alarms, requires GNN to detect via performance anomalies
3. **Realistic** — OSPF cost misconfigurations happen in production (typo during maintenance)
4. **Query-friendly** — you can easily query OSPF LSDB, simulate new paths, and assess congestion before remediation
5. **Demonstrates full workflow** — fault injection → GNN detection → root cause → remediation suggestion → **impact assessment** → safe remediation
6. **Appropriate for flat topology** — demonstrates core mesh resilience in simple 2-tier design

---

## Implementation Steps

### Phase 1: Setup Baseline Traffic

**Critical Files:**
- [examples/controller/transport/default-network/l3vpn-blue-test.yaml](../../../examples/controller/transport/default-network/l3vpn-blue-test.yaml) — sample BLUE VPN traffic test
- [examples/controller/transport/default-network/l3vpn-red-test.yaml](../../../examples/controller/transport/default-network/l3vpn-red-test.yaml) — sample RED VPN traffic test

**Setup Steps:**
1. Deploy the L3VPN network (BLUE hub-and-spoke)
2. Create TrafficTest resources to generate baseline traffic:
   - Multiple spoke-to-hub flows (`ce1-spoke → ce1-hub`, `ce3-spoke → ce1-hub`, `ce4-spoke → ce1-hub`)
   - Use `pattern_type: multi_sine` with `time_reference: wall_clock` for realistic time-of-day variation
   - Enable `bidirectional: true` to simulate realistic asymmetric traffic (small upload, large download)
   - Duration: Long-running (3600+ seconds) for continuous monitoring

**Example TrafficTest YAML:**
```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: blue-vpn-baseline-spoke1
  namespace: network
spec:
  vpnRef: l3vpn-blue-service
  source_devices:
    - ce1-spoke
  destination_device: ce1-hub
  protocol: TCP
  bandwidth: 40Mbps
  duration: 7200
  pattern_type: multi_sine
  bidirectional: true
  reverse_bandwidth: 5Mbps
  pattern_config:
    base_rate: 30Mbps
    time_reference: wall_clock
    components:
      - period: 3600
        amplitude: 15Mbps
        phase_offset: 0
    noise_stddev_pct: 5.0
    min_rate: 10Mbps
    max_rate: 60Mbps
```

3. Wait for traffic to stabilize (5-10 minutes)
4. Verify metrics are flowing to Cloud Monitoring via Ops Agent

### Phase 2: Inject Fault

**Critical Files:**
- [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml) — NetworkFailure CRD definition
- [examples/controller/transport/default-network/infrastructure.yaml](../../../examples/controller/transport/default-network/infrastructure.yaml) — infrastructure descriptor

**Injection Steps:**
1. Create NetworkFailure resource for OSPF cost inflation on `p1-p2`:
```bash
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: ospf-cost-inflation-p1-p2
  namespace: network
spec:
  failureType: OSPF_COST_INFLATION
  infrastructureRef: l3vpn-infra
  target:
    router: p1
    interface: eth1
  parameters:
    ospf_cost: 65535
EOF
```

2. Monitor NetworkFailure status:
```bash
kubectl get networkfailure ospf-cost-inflation-p1-p2 -n network -w
```

3. Wait for `status.phase: Active`

4. Observe traffic metrics:
   - Latency increase on affected flows
   - Path changes in OSPF LSDB
   - Potential packet loss during reconvergence
   - Congestion on alternate paths

### Phase 3: GNN Detection & Root Cause Analysis

**Critical Files:**
- [domains/transport/gnn/](../../../domains/transport/gnn/) — GNN training and serving code
- [docs/gnn/Readme.md](../../gnn/Readme.md) — GNN documentation

**Detection Steps:**
1. Wait for GNN to process updated metrics from Cloud Monitoring
2. Query GNN embeddings for anomalies:
   - Router `p1` and `p2` should show anomalous latency patterns
   - Link `p1-p2` should show reduced utilization (traffic rerouted)
   - Alternate paths (`p1-p3-p2`, `p1-p4-p2`) should show increased utilization
3. GNN identifies root cause: OSPF cost anomaly on `p1-p2`
4. GNN suggests remediation: "Restore OSPF cost to 1 on p1 eth1"

### Phase 4: Impact Assessment (Query-Based)

**Before Applying Remediation:**

**Query 1: Current OSPF Paths**
```bash
# SSH to p1 and query OSPF routes to pe2 (hub)
kubectl exec -it -n network <p1-pod> -- vtysh -c "show ip ospf route"
```
Expected: Routes to `10.0.0.8` (pe2) go via `p3` or `p4` instead of direct `p2`

**Query 2: Current Link Utilization**
```bash
# Query Cloud Monitoring for interface metrics
gcloud monitoring time-series list \
  --filter='metric.type="agent.googleapis.com/interface/traffic" AND resource.labels.instance_id="network-vm"' \
  --format=json
```
Expected: `p1-eth1` (p1-p2 link) has low utilization, `p1-eth2` (p1-p3 link) or `p1-eth3` (p1-p4 link) has high utilization

**Query 3: Simulate Remediation Impact**
- **Question**: "If we restore cost to 1, what paths will change?"
- **Query OSPF LSDB** to calculate new shortest paths
- **Identify affected flows**: All traffic from `pe1`, `pe2` that was using `p1-p2` before the fault

**Query 4: Assess Congestion Risk**
- **Calculate rerouted bandwidth**: Sum of all flows currently using `p1-p3-p2` or `p1-p4-p2` path
- **Check current utilization on `p1-p2`**: Should be near zero
- **Check capacity**: `p1-p2` link is 1 Gbps
- **Prediction**: Restoring cost will shift ~X Mbps from alternate paths back to `p1-p2`
- **Congestion risk**: Low (link has 1 Gbps capacity and is currently idle)

**Query 5: Latency Impact Prediction**
- **Current latency** on `pe1→pe2` path via `p1→p3→p2`: ~X ms
- **Predicted latency** after restoration via direct `p1→p2`: ~Y ms (should be lower)
- **Expected improvement**: X - Y ms latency reduction

### Phase 5: Apply Remediation

**Remediation Steps:**
1. Delete the NetworkFailure resource:
```bash
kubectl delete networkfailure ospf-cost-inflation-p1-p2 -n network
```

2. Monitor status: `phase: Restoring → Restored`

3. Verify OSPF cost restored to 1:
```bash
kubectl exec -it -n network <p1-pod> -- vtysh -c "show ip ospf interface eth1"
```

4. Observe traffic metrics:
   - Latency decreases on affected flows
   - Traffic returns to `p1-p2` direct path
   - Utilization on alternate paths decreases

5. Verify GNN detects restoration and anomaly resolution

### Phase 6: Validation

**Verification Steps:**
1. Compare pre-fault and post-remediation metrics:
   - Latency should return to baseline
   - Packet loss should be zero (except brief reconvergence)
   - Link utilization should match original distribution
2. Verify GNN embeddings show "healthy" state
3. Confirm all VPN flows are operational
4. Check for any unexpected side effects

---

## Critical Files Reference

**Network Configuration:**
- [examples/controller/transport/default-network/infrastructure.yaml](../../../examples/controller/transport/default-network/infrastructure.yaml) — physical topology
- [examples/controller/transport/default-network/underlay.yaml](../../../examples/controller/transport/default-network/underlay.yaml) — OSPF, BGP, MPLS config
- [examples/controller/transport/default-network/blue-vpn.yaml](../../../examples/controller/transport/default-network/blue-vpn.yaml) — BLUE VPN VRF config
- [examples/controller/transport/default-network/red-vpn.yaml](../../../examples/controller/transport/default-network/red-vpn.yaml) — RED VPN VRF config

**Fault Injection:**
- [controller/config/networkfailure.yaml](../../../controller/config/networkfailure.yaml) — NetworkFailure CRD
- [controller/src/networkfailure/](../../../controller/src/networkfailure/) — NetworkFailure operator code

**Traffic Testing:**
- [controller/config/traffic.yaml](../../../controller/config/infrastructure/traffic.yaml) — TrafficTest CRD
- [traffic-agent/](../../../traffic-agent/) — Go traffic generator
- [traffic-agent/internal/patterns/](../../../traffic-agent/internal/patterns/) — traffic pattern implementations

**Documentation:**
- [examples/controller/transport/default-network/README.md](../../../examples/controller/transport/default-network/README.md) — network topology details
- [docs/scenarios/transport.md](../transport.md) — existing transport RCA scenario
- [docs/network/metrics.md](../../network/metrics.md) — metrics collection details

---

## Summary

### Scenario Comparison

**Scenario 1: OSPF Cost Inflation** ⭐ **RECOMMENDED FOR REMEDIATION DEMO**
- ✅ Silent failure requiring GNN detection
- ✅ Forces measurable traffic rerouting
- ✅ Clear congestion impact on alternate paths
- ✅ Perfect for impact assessment queries before remediation
- ✅ Demonstrates: Fault → Detection → Root Cause → Impact Assessment → Remediation

**Scenario 4: Traffic Planning & Capacity** ⭐ **RECOMMENDED FOR CAPACITY DEMO**
- ✅ Proactive capacity planning before deployment
- ✅ Multi-agent coordination (Impact → Designer → Test)
- ✅ Risk mitigation through pre-assessment
- ✅ Demonstrates: Planning → Assessment → Upgrade → Validation

---

## Network Characteristics Specific to Default Network

**Advantages for Learning:**
- ✅ **Simple 2-tier flat topology** - Easy to understand and visualize
- ✅ **Single OSPF area** - No multi-area complexity
- ✅ **Short paths** - Typically 2-4 hops, easier to trace
- ✅ **Small scale** - 10 backbone routers, manageable for lab environment
- ✅ **Direct PE-P connections** - No aggregation layer complexity

**Limitations:**
- ⚠️ **Less realistic** than medium or complex networks for production scenarios
- ⚠️ **Limited hierarchy** - Cannot demonstrate multi-tier failure cascades
- ⚠️ **Single area OSPF** - Cannot demonstrate ABR or inter-area routing issues
- ⚠️ **Smaller GNN training set** - Fewer nodes and edges for model training

**When to Use Default Network:**
- Learning L3VPN and MPLS fundamentals
- Initial GNN model development and testing
- Quick proof-of-concept demonstrations
- Lab environments with resource constraints
- Teaching basic fault injection concepts

**When to Upgrade:**
- Move to **medium-network** for multi-area OSPF and 3-tier hierarchy
- Move to **complex-network** for production-grade 4-tier architecture with ABRs
