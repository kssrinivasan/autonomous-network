# Scenario 2: Pre-Aggregation ABR to Core Link Failure (Medium Network)

**Fault Type:** `LINK_DOWN`

**What to Inject:**
Bring down `pa1-p1` link (Bristol PA to London Core).

**Why This is Good:**
- ✅ **Affects one region** (Area 1 - UK) but not Area 2 (Ireland/Scotland)
- ✅ **PA router pa1 has redundancy** via pa1-p2 link (dual-homed ABR)
- ✅ **Tests dual-homing resilience** at PA/ABR tier
- ✅ **UK traffic** (pe1, pe2, pe3, pe4, pe5, pe6) reroutes through alternate ABR path
- ✅ **Clear before/after metrics** at regional scope
- ✅ **Demonstrates tier-2 ABR impact** (pre-aggregation layer)
- ✅ **Multi-area OSPF reconvergence** — ABR must update inter-area routes

**Traffic Impact:**
- All UK PE traffic via pa1 shifts to pa1-p2 path (Bristol → Manchester → destination)
- Increased latency: ~5-10ms (UK → Manchester → destination vs direct UK → London → destination)
- Load increase on pa1-p2 link (dual-homed backup)
- Minimal impact on Ireland/Scotland traffic (Area 2 uses pa3/pa4)
- Inter-area routes from Area 1 to Area 0 recalculated by pa1

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: link-down-pa1-p1-abr
  namespace: network
spec:
  failureType: LINK_DOWN
  infrastructureRef: medium-network-infra
  target:
    router: pa1
    peer_router: p1
```

**Impact Assessment Queries Before Remediation:**
1. `query_router_metadata("pa1,p1")` - Verify pa1 is ABR for Area 1 (UK)
2. `query_ospf_paths("pe1", "pe7", "")` - Current UK→Ireland path via pa1-p2
3. `query_link_utilization("pa1-p2,pa1-p1", 10)` - Check dual-homed ABR link utilization
4. `query_affected_flows("pa1,pe1,pe2,pe3")` - All UK regional flows
5. `simulate_remediation_impact("bring_link_up", "pa1", "pa1-p1", "pe1", "pe7")` - Restoration impact

**Remediation:**
Restore the link (delete the NetworkFailure resource).

**Why GNN Detects This:**
- OSPF adjacency down alarm (easier to detect than cost inflation)
- UK regional traffic (Area 1) shows path changes
- Load shift to pa1-p2 link visible in metrics
- Regional isolation pattern in embeddings
- ABR summary LSA updates visible in Area 0
