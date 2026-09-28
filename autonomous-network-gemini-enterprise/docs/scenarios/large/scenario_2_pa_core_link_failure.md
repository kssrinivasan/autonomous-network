# Scenario 2: Pre-Aggregation to Core Link Failure (Large Network)

**Fault Type:** `LINK_DOWN`

**What to Inject:**
Bring down `pa1-p1` link (Manchester PA to London Core).

**Why This is Good:**
- ✅ **Affects one region** (UK) but not others
- ✅ **PA router pa1 has redundancy** via pa1-p2 link
- ✅ **Tests dual-homing resilience** at PA tier
- ✅ **UK traffic** (pe1, pe2, pe3, pe4) reroutes through p2
- ✅ **Clear before/after metrics** at regional scope
- ✅ **Demonstrates tier-2 impact** (pre-aggregation layer)

**Traffic Impact:**
- All UK PE traffic via pa1 shifts to pa1-p2 path (Manchester → Frankfurt → destination)
- Increased latency: ~5-8ms (UK → Germany → destination vs direct UK → destination)
- Load increase on pa1-p2 link (dual-homed backup)
- Minimal impact on other regions (Germany, France, Iberia)

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: link-down-pa1-p1
  namespace: network
spec:
  failureType: LINK_DOWN
  infrastructureRef: complex-network-infra
  target:
    router: pa1
    peer_router: p1
```

**Impact Assessment Queries Before Remediation:**
1. `query_router_metadata("pa1,p1")` - Verify pa1 is ABR for Area 1 (UK)
2. `query_ospf_paths("pe1", "pe5", "")` - Current UK→France path via pa1-p2
3. `query_link_utilization("pa1-p2,pa1-p1", 10)` - Check dual-homed link utilization
4. `query_affected_flows("pa1,ar1,ar2,pe1,pe2")` - All UK regional flows
5. `simulate_remediation_impact("bring_link_up", "pa1", "pa1-p1", "pe1", "pe5")` - Restoration impact

**Remediation:**
Restore the link (delete the NetworkFailure resource).

**Why GNN Detects This:**
- OSPF adjacency down alarm (easier to detect than cost inflation)
- UK regional traffic shows path changes
- Load shift to pa1-p2 link visible in metrics
- Regional isolation pattern in embeddings
