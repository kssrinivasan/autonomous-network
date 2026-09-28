# Scenario 4: Traffic Planning and Capacity Management (Default Network)

This scenario demonstrates the Impact Agent's traffic planning and capacity management capabilities, coordinating with Designer and Test agents.

## Use Case

Adding a new VPN spoke site that requires capacity upgrades before deployment.

## Workflow

### Phase 1: Traffic Impact Assessment

**User Request:** *"I want to add a new spoke site ce5-spoke with 150Mbps traffic to ce1-hub. Can we handle it?"*

**Impact Agent Actions:**
1. Calls `simulate_traffic_addition`:
   - `source_device`: "ce5-spoke"
   - `destination_device`: "ce1-hub"
   - `bandwidth_bps`: 150000000 (150Mbps)
   - `protocol`: "TCP"

2. Receives path and impact analysis:
```json
{
  "routing": {
    "source_pe": "pe1",
    "destination_pe": "pe2",
    "path": ["pe1", "p1", "p2", "pe2"],
    "links": ["pe1-p1", "p1-p2", "p2-pe2"]
  },
  "link_impacts": [
    {
      "link": "pe1-p1",
      "current_utilization_pct": 35.0,
      "predicted_utilization_pct": 50.0,
      "risk": "low"
    },
    {
      "link": "p1-p2",
      "current_utilization_pct": 68.0,
      "predicted_utilization_pct": 83.0,
      "risk": "high"
    },
    {
      "link": "p2-pe2",
      "current_utilization_pct": 45.0,
      "predicted_utilization_pct": 60.0,
      "risk": "medium"
    }
  ],
  "overall_risk": "high",
  "recommendation": "CAPACITY_UPGRADE_NEEDED",
  "message": "Link p1-p2 will reach 83% utilization. Capacity upgrade recommended."
}
```

**Impact Agent Response to User:**
```
⚠️ CAPACITY UPGRADE NEEDED

Adding 150Mbps traffic from ce5-spoke to ce1-hub will cause congestion:

Path: pe1 → p1 → p2 → pe2

Link Impacts:
- pe1-p1: 35% → 50% utilization (✅ LOW RISK)
- p1-p2: 68% → 83% utilization (❌ HIGH RISK)
- p2-pe2: 45% → 60% utilization (⚠️ MEDIUM RISK)

Recommendation: Upgrade p1-p2 capacity before adding traffic.
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
      "link": "p1-p2",
      "current_capacity_str": "1Gbps",
      "current_utilization_pct": 68.0,
      "recommended_upgrade": {
        "type": "upgrade_link",
        "new_capacity_str": "10Gbps",
        "post_upgrade_utilization_pct": 8.3,
        "cost_estimate": "high"
      },
      "alternative": {
        "type": "add_parallel_link",
        "parallel_capacity_str": "1Gbps",
        "total_capacity_str": "2Gbps",
        "post_addition_utilization_pct": 41.5,
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

Link: p1-p2
Current: 1Gbps @ 68% utilization

Option 1: Add Parallel Link (Recommended for Default Network)
- Add: 1Gbps parallel link p1-p2-backup
- Total capacity: 2Gbps
- Post-addition utilization: 41.5%
- Cost: Medium
- Benefits: Provides redundancy and load balancing

Option 2: Upgrade Link
- New capacity: 10Gbps
- Post-upgrade utilization: 8.3%
- Cost: High
- Benefits: Significant headroom for future growth

Recommendation: Add parallel link for cost-effective redundancy.
```

### Phase 3: Capacity Implementation via Designer Agent

**User Request:** *"Add parallel link for p1-p2"*

**Impact Agent → Designer Agent Handoff:**
```json
{
  "task": "add_parallel_link",
  "link": "p1-p2",
  "current_capacity": "1Gbps",
  "parallel_capacity": "1Gbps",
  "reason": "Support new ce5-spoke traffic (150Mbps) without congestion"
}
```

**Designer Agent Actions:**
1. Updates `infrastructure.yaml`:
   - Add new interface `p1` eth-p2-backup to p2 @ 1Gbps
   - Add new interface `p2` eth-p1-backup to p1 @ 1Gbps
   - Configure OSPF on new interfaces
2. Commits changes to Git
3. Triggers operator to apply configuration

### Phase 4: Validation

**User Request:** *"Verify the parallel link is active"*

**Impact Agent Actions:**
1. Calls `query_link_utilization("p1-p2", 10)`
2. Verifies new capacity:
```json
{
  "links": [
    {
      "name": "p1-p2",
      "capacity_bps": 1000000000,
      "capacity_str": "1Gbps",
      "current_utilization_pct": 34.0,
      "available_bps": 660000000
    },
    {
      "name": "p1-p2-backup",
      "capacity_bps": 1000000000,
      "capacity_str": "1Gbps",
      "current_utilization_pct": 34.0,
      "available_bps": 660000000
    }
  ],
  "total_capacity": "2Gbps",
  "aggregate_utilization_pct": 34.0
}
```

**Impact Agent Response:**
```
✅ Parallel Link Active

Links p1-p2:
- Primary link: 1Gbps @ 34% utilization
- Backup link: 1Gbps @ 34% utilization
- Total capacity: 2Gbps
- Aggregate utilization: 34%
- Available bandwidth: 1.32Gbps

Safe to proceed with traffic addition.
```

### Phase 5: Traffic Addition via Test Agent

**User Request:** *"Add the ce5-spoke traffic now"*

**Impact Agent → Test Agent Handoff:**
```json
{
  "task": "add_traffic_flow",
  "source_device": "ce5-spoke",
  "destination_device": "ce1-hub",
  "bandwidth": "150Mbps",
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
  name: ce5-spoke-to-hub
spec:
  vpnRef: l3vpn-blue-service
  source_devices: ["ce5-spoke"]
  destination_device: ce1-hub
  protocol: TCP
  bandwidth: 150Mbps
  duration: 3600
  pattern_type: multi_sine
  bidirectional: true
```
2. Starts traffic generator
3. Reports flow metrics

### Phase 6: Final Verification

**User Request:** *"Verify traffic is flowing correctly"*

**Impact Agent Actions:**
1. Calls `query_affected_flows(router_names="pe1,p1,p2,pe2")`
2. Calls `query_link_utilization("p1-p2,p1-p2-backup")`
3. Verifies flow and link metrics

**Impact Agent Response:**
```
✅ Traffic Verified

Flow: ce5-spoke → ce1-hub
- Status: Running
- Throughput: 148Mbps (target: 150Mbps)
- Latency: 3.8ms
- Packet loss: 0%

Path: pe1 → p1 → p2 → pe2

Link Utilization:
- p1-p2: 42% (safe)
- p1-p2-backup: 41% (safe)
- Aggregate: 41.5%
- Headroom: 1.17Gbps available
```

### Key Takeaways

This scenario demonstrates:
- ✅ **Proactive capacity planning** - Identify upgrade needs before deployment
- ✅ **Multi-agent coordination** - Impact → Designer → Test workflow
- ✅ **Risk mitigation** - Prevent congestion through pre-deployment assessment
- ✅ **Validation at each step** - Verify capacity, verify traffic, verify performance
- ✅ **Alternative recommendations** - Compare upgrade vs parallel link options
- ✅ **Flat topology simplicity** - Straightforward paths (PE→P→P→PE) for easy analysis

### Required Components

1. **Impact Agent**: Traffic simulation and capacity recommendations
2. **Designer Agent**: Infrastructure changes (link capacity upgrades, parallel links)
3. **Test Agent**: Traffic generation and flow validation
4. **Operator**: Apply configuration changes to routers
