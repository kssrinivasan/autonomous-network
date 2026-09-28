# Scenario 1: OSPF Cost Inflation on Core Link (Large Network)

**Fault Type:** `OSPF_COST_INFLATION`

**What to Inject:**
Set OSPF cost to 65535 on the `p1-p3` link (London-Paris) in the core mesh.

**Why This is Ideal:**
- ✅ **Affects cross-region traffic** (UK ↔ France, UK ↔ Germany via Paris)
- ✅ **Forces multi-hop rerouting** through alternate core paths (p1→p2→p3 or p1→p6→p5→p3)
- ✅ **Impacts multiple lower tiers** (PA, AR, PE layers must all reroute)
- ✅ **Demonstrates cascading impact:** core failure → PA reroute → AR reroute → PE traffic shift
- ✅ **Measurable congestion** at multiple tiers
- ✅ **6-10 hop paths** instead of 4-6 hops (realistic failure impact)
- ✅ **OSPF adjacency remains Full** — no control-plane alarms, requiring GNN to detect via performance anomalies

**Traffic Impact:**
- UK→France traffic (pe1/pe2 → pe5/pe6/pe11): reroutes via p2→p3 or p1→p4→p3
- Increased latency: ~15-25ms (additional 2-4 hops through core)
- Potential bottlenecks at pa3, pa4 (France pre-aggregation) due to concentrated rerouted traffic
- Multiple VPN flows affected: ~8-12 BLUE VPN spoke-to-hub flows + RED VPN mesh traffic

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: ospf-cost-inflation-p1-p3-cross-region
  namespace: network
spec:
  failureType: OSPF_COST_INFLATION
  infrastructureRef: complex-network-infra
  target:
    router: p1
    interface: eth2  # p1-p3 link
  parameters:
    ospf_cost: 65535
```

**Impact Assessment Queries Before Remediation:**
1. `query_ospf_paths("pe1", "pe5", "p1-p3")` - Current path UK→France with cost inflation
2. `query_ospf_paths("pe1", "pe5", "")` - Post-remediation path
3. `query_link_utilization("p1-p2,p2-p3,p1-p6,p6-p5,p5-p3", 10)` - Alternate core paths
4. `query_affected_flows("p1,p2,p3,pa1,pa2,pa3")` - All flows using these routers
5. `query_router_metadata("p1,p3,pa3,pa4")` - Identify router roles and tiers
6. `identify_bottlenecks(50.0)` - Find links above 50% utilization
7. `simulate_remediation_impact("restore_ospf_cost", "p1", "p1-p3", "pe1", "pe5")` - Full impact analysis

**Remediation:**
Restore original OSPF cost (1) on `p1-p3` link.

**Why GNN Detects This:**
- Anomalous latency increase on cross-region traffic flows (UK ↔ France)
- Path metrics change in embeddings across all 4 tiers
- No BGP or OSPF alarms (adjacency still Full)
- Subtle multi-tier performance degradation requiring ML-based detection
