# Scenario 5: Traffic Planning and Capacity Management (Medium Network)

This scenario demonstrates the Impact Agent's traffic planning and capacity management capabilities, coordinating with Designer and Test agents.

## Use Case

Adding a new VPN spoke site in Area 2 that requires capacity upgrades before deployment.

## Workflow

### Phase 1: Traffic Impact Assessment

**User Request:** *"I want to add a new spoke site ce9-spoke in Area 2 with 180Mbps traffic to ce1-hub in Area 1. Can we handle it?"*

**Impact Agent Actions:**
1. Calls `simulate_traffic_addition`:
   - `source_device`: "ce9-spoke"
   - `destination_device`: "ce1-hub"
   - `bandwidth_bps`: 180000000 (180Mbps)
   - `protocol`: "TCP"

2. Receives path and impact analysis:
```json
{
  "routing": {
    "source_pe": "pe8",
    "destination_pe": "pe2",
    "path": ["pe8", "pa4", "p2", "pa1", "pe2"],
    "areas": ["Area 2", "Area 0", "Area 1"],
    "abrs": ["pa4", "pa1"],
    "links": ["pe8-pa4", "pa4-p2", "p2-pa1", "pa1-pe2"]
  },
  "link_impacts": [
    {
      "link": "pe8-pa4",
      "current_utilization_pct": 30.0,
      "predicted_utilization_pct": 48.0,
      "risk": "low"
    },
    {
      "link": "pa4-p2",
      "current_utilization_pct": 65.0,
      "predicted_utilization_pct": 83.0,
      "risk": "high"
    },
    {
      "link": "p2-pa1",
      "current_utilization_pct": 45.0,
      "predicted_utilization_pct": 63.0,
      "risk": "medium"
    },
    {
      "link": "pa1-pe2",
      "current_utilization_pct": 40.0,
      "predicted_utilization_pct": 58.0,
      "risk": "medium"
    }
  ],
  "overall_risk": "high",
  "recommendation": "CAPACITY_UPGRADE_NEEDED",
  "message": "Link pa4-p2 will reach 83% utilization. Capacity upgrade recommended before adding traffic."
}
```

**Impact Agent Response to User:**
```
⚠️ CAPACITY UPGRADE NEEDED

Adding 180Mbps traffic from ce9-spoke (Area 2) to ce1-hub (Area 1) will cause congestion:

Path: pe8 → pa4 (ABR) → p2 → pa1 (ABR) → pe2
Cross-area path: Area 2 → Area 0 → Area 1

Link Impacts:
- pe8-pa4: 30% → 48% utilization (✅ LOW RISK)
- pa4-p2: 65% → 83% utilization (❌ HIGH RISK - ABR uplink)
- p2-pa1: 45% → 63% utilization (⚠️ MEDIUM RISK)
- pa1-pe2: 40% → 58% utilization (⚠️ MEDIUM RISK)

Recommendation: Upgrade pa4-p2 ABR uplink capacity before adding traffic.
```

### Phase 2: Capacity Recommendation

**User Request:** *"What capacity upgrades do we need?"*

**Impact Agent Actions:**
1. Calls `recommend_capacity_upgrades(target_utilization_pct=50.0)`

2. Receives recommendations:
```json
{
  "recommendations": [
    {
      "link": "pa4-p2",
      "current_capacity_str": "10Gbps",
      "current_utilization_pct": 65.0,
      "recommended_upgrade": {
        "type": "add_parallel_link",
        "parallel_capacity_str": "10Gbps",
        "total_capacity_str": "20Gbps",
        "post_addition_utilization_pct": 41.5,
        "benefit": "Provides redundancy and load balancing for Area 2 ABR",
        "cost_estimate": "high"
      },
      "alternative": {
        "type": "upgrade_link",
        "new_capacity_str": "100Gbps",
        "post_upgrade_utilization_pct": 8.3,
        "cost_estimate": "very_high"
      }
    }
  ]
}
```

**Impact Agent Response:**
```
Capacity Upgrade Recommendations:

Link: pa4-p2 (Area 2 ABR to Core)
Current: 10Gbps @ 65% utilization

Option 1: Add Parallel Link (Recommended)
- Add: 10Gbps parallel link pa4-p2-backup
- Total capacity: 20Gbps
- Post-addition utilization: 41.5%
- Cost: High
- Benefits: Provides redundancy and load balancing for Area 2 ABR uplink

Option 2: Upgrade Link
- New capacity: 100Gbps
- Post-upgrade utilization: 8.3%
- Cost: Very High
- Benefits: Significant headroom for future growth

Recommendation: Add parallel link for cost-effective ABR redundancy.
```

### Phase 3: Capacity Implementation via Designer Agent

**User Request:** *"Add parallel link for pa4-p2"*

**Impact Agent → Designer Agent Handoff:**
```json
{
  "task": "add_parallel_link",
  "link": "pa4-p2",
  "current_capacity": "10Gbps",
  "parallel_capacity": "10Gbps",
  "reason": "Support new ce9-spoke traffic (180Mbps) from Area 2 without congestion on ABR uplink"
}
```

**Designer Agent Actions:**
1. Updates `infrastructure.yaml`:
   - Add new interface `pa4` eth-p2-backup to p2 @ 10Gbps
   - Add new interface `p2` eth-pa4-backup to pa4 @ 10Gbps
   - Configure OSPF Area 0 on new interfaces
2. Commits changes to Git
3. Triggers operator to apply configuration

### Phase 4: Validation

**User Request:** *"Verify the parallel ABR uplink is active"*

**Impact Agent Actions:**
1. Calls `query_link_utilization("pa4-p2", 10)`
2. Verifies new capacity:
```json
{
  "links": [
    {
      "name": "pa4-p2",
      "capacity_bps": 10000000000,
      "capacity_str": "10Gbps",
      "current_utilization_pct": 32.5,
      "available_bps": 6750000000
    },
    {
      "name": "pa4-p2-backup",
      "capacity_bps": 10000000000,
      "capacity_str": "10Gbps",
      "current_utilization_pct": 32.5,
      "available_bps": 6750000000
    }
  ],
  "total_capacity": "20Gbps",
  "aggregate_utilization_pct": 32.5
}
```

**Impact Agent Response:**
```
✅ Parallel ABR Uplink Active

Links pa4-p2 (ABR to Core):
- Primary link: 10Gbps @ 32.5% utilization
- Backup link: 10Gbps @ 32.5% utilization
- Total capacity: 20Gbps
- Aggregate utilization: 32.5%
- Available bandwidth: 13.5Gbps

Safe to proceed with Area 2 traffic addition.
```

### Phase 5: Traffic Addition via Test Agent

**User Request:** *"Add the ce9-spoke traffic now"*

**Impact Agent → Test Agent Handoff:**
```json
{
  "task": "add_traffic_flow",
  "source_device": "ce9-spoke",
  "destination_device": "ce1-hub",
  "bandwidth": "180Mbps",
  "protocol": "TCP",
  "duration": 3600,
  "pattern_type": "multi_sine"
}
```

**Test Agent Actions:**
1. Creates TrafficTest resource:
```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: ce9-spoke-to-hub-area2
  namespace: network
spec:
  vpnRef: blue-vpn-service
  source_devices: ["ce9-spoke"]
  destination_device: ce1-hub
  protocol: TCP
  bandwidth: 180Mbps
  duration: 3600
  pattern_type: multi_sine
  bidirectional: true
```
2. Starts traffic generator
3. Reports flow metrics

### Phase 6: Final Verification

**User Request:** *"Verify cross-area traffic is flowing correctly"*

**Impact Agent Actions:**
1. Calls `query_affected_flows(router_names="pe8,pa4,p2,pa1,pe2")`
2. Calls `query_link_utilization("pa4-p2,pa4-p2-backup")`
3. Verifies flow and link metrics

**Impact Agent Response:**
```
✅ Cross-Area Traffic Verified

Flow: ce9-spoke (Area 2) → ce1-hub (Area 1)
- Status: Running
- Throughput: 178Mbps (target: 180Mbps)
- Latency: 8.5ms
- Packet loss: 0%
- Crosses ABRs: pa4 (Area 2→0), pa1 (Area 0→1)

Path: pe8 → pa4 (ABR) → p2 → pa1 (ABR) → pe2

ABR Uplink Utilization (pa4-p2):
- Primary: 40% (safe)
- Backup: 42% (safe)
- Aggregate: 41%
- Headroom: 11.8Gbps available
```

### Key Takeaways

This scenario demonstrates:
- ✅ **Proactive capacity planning** - Identify ABR uplink upgrade needs before deployment
- ✅ **Multi-agent coordination** - Impact → Designer → Test workflow
- ✅ **Risk mitigation** - Prevent congestion through pre-assessment
- ✅ **Validation at each step** - Verify capacity, verify traffic, verify performance
- ✅ **Alternative recommendations** - Compare upgrade vs parallel link options
- ✅ **Cross-area traffic** - Demonstrates Area 2 → Area 0 → Area 1 path with ABR transitions
- ✅ **ABR-specific planning** - Focuses on critical ABR uplink capacity

### Required Components

1. **Impact Agent**: Traffic simulation and capacity recommendations
2. **Designer Agent**: Infrastructure changes (link capacity upgrades, parallel links)
3. **Test Agent**: Traffic generation and flow validation
4. **Operator**: Apply configuration changes to routers
