# Scenario 4: Traffic Planning and Capacity Management (Large Network)

This scenario demonstrates the Impact Agent's traffic planning and capacity management capabilities, coordinating with Designer and Test agents.

## Use Case

Adding a new VPN spoke site that requires capacity upgrades before deployment.

## Workflow

### Phase 1: Traffic Impact Assessment

**User Request:** *"I want to add a new spoke site ce4-spoke with 200Mbps traffic to ce1-hub. Can we handle it?"*

**Impact Agent Actions:**
1. Calls `simulate_traffic_addition`:
   - `source_device`: "ce4-spoke"
   - `destination_device`: "ce1-hub"
   - `bandwidth_bps`: 200000000 (200Mbps)
   - `protocol`: "TCP"

2. Receives path and impact analysis:
```json
{
  "routing": {
    "source_pe": "pe4",
    "destination_pe": "pe2",
    "path": ["pe4", "p2", "pe2"],
    "links": ["pe4-p2", "p2-pe2"]
  },
  "link_impacts": [
    {
      "link": "pe4-p2",
      "current_utilization_pct": 45.0,
      "predicted_utilization_pct": 65.0,
      "risk": "medium"
    },
    {
      "link": "p2-pe2",
      "current_utilization_pct": 72.0,
      "predicted_utilization_pct": 92.0,
      "risk": "high"
    }
  ],
  "overall_risk": "high",
  "recommendation": "CAPACITY_UPGRADE_NEEDED",
  "message": "Link p2-pe2 will reach 92% utilization. Capacity upgrade recommended."
}
```

**Impact Agent Response to User:**
```
⚠️ CAPACITY UPGRADE NEEDED

Adding 200Mbps traffic from ce4-spoke to ce1-hub will cause congestion:

Path: pe4 → p2 → pe2

Link Impacts:
- pe4-p2: 45% → 65% utilization (⚠️ MEDIUM RISK)
- p2-pe2: 72% → 92% utilization (❌ HIGH RISK)

Recommendation: Upgrade p2-pe2 capacity before adding traffic.
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
      "link": "p2-pe2",
      "current_capacity_str": "1Gbps",
      "current_utilization_pct": 72.0,
      "recommended_upgrade": {
        "type": "upgrade_link",
        "new_capacity_str": "10Gbps",
        "post_upgrade_utilization_pct": 9.2,
        "cost_estimate": "medium"
      },
      "alternative": {
        "type": "add_parallel_link",
        "parallel_capacity_str": "1Gbps",
        "total_capacity_str": "2Gbps",
        "post_addition_utilization_pct": 46.0,
        "benefit": "Provides redundancy and load balancing",
        "cost_estimate": "medium"
      }
    }
  ]
}
```

**Impact Agent Response:**
```
Capacity Upgrade Recommendations:

Link: p2-pe2
Current: 1Gbps @ 72% utilization

Option 1: Upgrade Link (Recommended)
- New capacity: 10Gbps
- Post-upgrade utilization: 9.2%
- Cost: Medium
- Benefits: Significant headroom for future growth

Option 2: Add Parallel Link
- Add: 1Gbps parallel link p2-pe2-backup
- Total capacity: 2Gbps
- Post-addition utilization: 46%
- Cost: Medium
- Benefits: Provides redundancy and load balancing

Recommendation: Upgrade to 10Gbps for long-term capacity.
```

### Phase 3: Capacity Implementation via Designer Agent

**User Request:** *"Upgrade p2-pe2 to 10Gbps"*

**Impact Agent → Designer Agent Handoff:**
```json
{
  "task": "upgrade_link_capacity",
  "link": "p2-pe2",
  "current_capacity": "1Gbps",
  "new_capacity": "10Gbps",
  "reason": "Support new ce4-spoke traffic (200Mbps) without congestion"
}
```

**Designer Agent Actions:**
1. Updates `infrastructure.yaml`:
   - Modify `p2` interface `eth-pe2` speed to 10Gbps
   - Modify `pe2` interface `eth-p2` speed to 10Gbps
2. Commits changes to Git
3. Triggers operator to apply configuration

### Phase 4: Validation

**User Request:** *"Verify the upgrade completed"*

**Impact Agent Actions:**
1. Calls `query_link_utilization("p2-pe2", 10)`
2. Verifies new capacity:
```json
{
  "links": [
    {
      "name": "p2-pe2",
      "capacity_bps": 10000000000,
      "capacity_str": "10Gbps",
      "current_utilization_pct": 7.2,
      "available_bps": 9280000000
    }
  ]
}
```

**Impact Agent Response:**
```
✅ Upgrade Verified

Link p2-pe2:
- Capacity: 10Gbps (upgraded from 1Gbps)
- Current utilization: 7.2%
- Available bandwidth: 9.28Gbps

Safe to proceed with traffic addition.
```

### Phase 5: Traffic Addition via Test Agent

**User Request:** *"Add the ce4-spoke traffic now"*

**Impact Agent → Test Agent Handoff:**
```json
{
  "task": "add_traffic_flow",
  "source_device": "ce4-spoke",
  "destination_device": "ce1-hub",
  "bandwidth": "200Mbps",
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
  name: ce4-spoke-to-hub
spec:
  vpnRef: l3vpn-blue-service
  source_devices: ["ce4-spoke"]
  destination_device: ce1-hub
  protocol: TCP
  bandwidth: 200Mbps
  duration: 3600
  pattern_type: multi_sine
  bidirectional: true
```
2. Starts traffic generator
3. Reports flow metrics

### Phase 6: Final Verification

**User Request:** *"Verify traffic is flowing correctly"*

**Impact Agent Actions:**
1. Calls `query_affected_flows(router_names="pe4,p2,pe2")`
2. Calls `query_link_utilization("p2-pe2")`
3. Verifies flow and link metrics

**Impact Agent Response:**
```
✅ Traffic Verified

Flow: ce4-spoke → ce1-hub
- Status: Running
- Throughput: 198Mbps (target: 200Mbps)
- Latency: 4.2ms
- Packet loss: 0%

Path: pe4 → p2 → pe2

Link p2-pe2:
- Utilization: 12.1% (safe)
- Headroom: 8.79Gbps available
```

### Key Takeaways

This scenario demonstrates:
- ✅ **Proactive capacity planning** - Identify upgrade needs before deployment
- ✅ **Multi-agent coordination** - Impact → Designer → Test workflow
- ✅ **Risk mitigation** - Prevent congestion through pre-deployment assessment
- ✅ **Validation at each step** - Verify capacity, verify traffic, verify performance
- ✅ **Alternative recommendations** - Compare upgrade vs parallel link options

### Required Components

1. **Impact Agent**: Traffic simulation and capacity recommendations
2. **Designer Agent**: Infrastructure changes (link capacity upgrades)
3. **Test Agent**: Traffic generation and flow validation
4. **Operator**: Apply configuration changes to routers
