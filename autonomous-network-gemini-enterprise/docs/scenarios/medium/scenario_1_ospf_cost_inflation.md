# Scenario 1: OSPF Cost Inflation on Core Link (Medium Network)

**Fault Type:** `OSPF_COST_INFLATION`

**What to Inject:**
Set OSPF cost to 65535 on the `p1-p2` link (London-Manchester) in the core mesh.

**Why This is Ideal:**
- ✅ **Affects inter-region traffic** (Area 1 ↔ Area 2, UK ↔ Ireland/Scotland)
- ✅ **Forces multi-hop rerouting** through alternate core paths (p1→p3→p2 or p1→p4→p2)
- ✅ **Impacts multiple tiers** (PA, PE layers must all reroute via ABRs)
- ✅ **Demonstrates cascading impact:** core failure → PA reroute → PE traffic shift across areas
- ✅ **Measurable congestion** at multiple tiers
- ✅ **5-7 hop paths** instead of 4-5 hops (realistic failure impact)
- ✅ **OSPF adjacency remains Full** — no control-plane alarms, requiring GNN to detect via performance anomalies
- ✅ **Multi-area complexity** — demonstrates ABR path recalculation

**Traffic Impact:**
- UK→Ireland traffic (pe1-pe6 → pe7-pe8): reroutes via p3→p2 or p1→p4→p2
- Increased latency: ~15-20ms (additional 2-3 hops through core)
- Potential bottlenecks at pa1, pa2 (UK pre-aggregation ABRs) or pa3, pa4 (Ireland/Scotland ABRs)
- Multiple VPN flows affected: ~5 BLUE VPN spoke-to-hub flows + RED VPN mesh traffic
- Inter-area summary LSAs must be recalculated by ABRs

**Example NetworkFailure YAML:**
```yaml
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
    interface: eth1  # p1-p2 link
  parameters:
    ospf_cost: 65535
```

**Impact Assessment Queries Before Remediation:**
1. `query_ospf_paths("pe1", "pe7", "p1-p2")` - Current path UK→Ireland with cost inflation
2. `query_ospf_paths("pe1", "pe7", "")` - Post-remediation path
3. `query_link_utilization("p1-p3,p3-p2,p1-p4,p4-p2,pa1-p1,pa1-p2", 10)` - Alternate core and PA paths
4. `query_affected_flows("p1,p2,p3,pa1,pa2,pa3,pa4")` - All flows using these routers
5. `query_router_metadata("p1,p2,pa1,pa2,pa3,pa4")` - Identify router roles (core, ABR)
6. `identify_bottlenecks(50.0)` - Find links above 50% utilization
7. `simulate_remediation_impact("restore_ospf_cost", "p1", "p1-p2", "pe1", "pe7")` - Full impact analysis

**Remediation:**
Restore original OSPF cost (1) on `p1-p2` link.

**Why GNN Detects This:**
- Anomalous latency increase on inter-region traffic flows (Area 1 ↔ Area 2)
- Path metrics change in embeddings across all 3 tiers
- No BGP or OSPF alarms (adjacency still Full)
- Subtle multi-tier performance degradation requiring ML-based detection
- ABR recalculation visible in summary LSA updates
