# Scenario 2: Core Router Link Failure (Default Network)

**Fault Type:** `LINK_DOWN`

**What to Inject:**
Bring down `p1-p3` link (London-Edinburgh).

**Why This is Good:**
- ✅ **Affects inter-city traffic** between London and Edinburgh
- ✅ **Core router p1 has redundancy** via p1-p2 and p1-p4 links
- ✅ **Tests core mesh resilience** at Tier 1
- ✅ **London traffic** (pe1) reroutes through p2 or p4
- ✅ **Clear before/after metrics** at core scope
- ✅ **Demonstrates flat topology impact** (all routers in Area 0)

**Traffic Impact:**
- All London-Edinburgh traffic via p1 shifts to p1-p2-p3 or p1-p4-p3 path
- Increased latency: ~5-8ms (London → Manchester/Glasgow → Edinburgh vs direct London → Edinburgh)
- Load increase on p1-p2 or p1-p4 links
- Minimal impact on Manchester-Glasgow traffic (different paths)

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: link-down-p1-p3
  namespace: network
spec:
  failureType: LINK_DOWN
  infrastructureRef: l3vpn-infra
  target:
    router: p1
    peer_router: p3
```

**Impact Assessment Queries Before Remediation:**
1. `query_router_metadata("p1,p3")` - Verify p1 and p3 are core routers
2. `query_ospf_paths("pe1", "pe3", "")` - Current London→Edinburgh path via alternate route
3. `query_link_utilization("p1-p2,p2-p3,p1-p4,p4-p3", 10)` - Check alternate link utilization
4. `query_affected_flows("p1,p3,pe1,pe3")` - All affected flows
5. `simulate_remediation_impact("bring_link_up", "p1", "p1-p3", "pe1", "pe3")` - Restoration impact

**Remediation:**
Restore the link (delete the NetworkFailure resource).

**Why GNN Detects This:**
- OSPF adjacency down alarm (easier to detect than cost inflation)
- London-Edinburgh traffic shows path changes
- Load shift to alternate core links visible in metrics
- Flat topology isolation pattern in embeddings
