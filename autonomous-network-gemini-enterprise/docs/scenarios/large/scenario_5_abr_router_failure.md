# Scenario 5: Area Border Router Failure (Large Network)

**Fault Type:** `ROUTER_DOWN`

**What to Inject:**
Bring down ABR router `pa3` (Hamburg, connects Area 0 to Area 2 - Germany region).

**Why This Demonstrates Complex Network Value:**
- ✅ **Area isolation impact** - Area 2 (Germany) loses one of two ABRs to backbone
- ✅ **Failover to alternate ABR** - Traffic shifts to pa4 (Munich)
- ✅ **Inter-area path changes** - All Germany PE routers (pe7-pe10) must reroute through pa4
- ✅ **Multi-tier cascading** - Core → PA → AR → PE all affected
- ✅ **Regional vs global impact** - Heavy impact in Germany, light impact elsewhere

**Traffic Impact:**
- Germany PEs (pe7, pe8, pe9, pe10) lose path via pa3 to core
- All Germany-to-other-region traffic reroutes through pa4 (Munich ABR)
- pa4 load doubles (handles both pa3 and pa4 traffic)
- Potential bottleneck at pa4-p3, pa4-p2 links
- Minimal impact on UK, France, Iberia traffic (different ABRs)

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: router-down-pa3-abr
  namespace: network
spec:
  failureType: ROUTER_DOWN
  infrastructureRef: complex-network-infra
  target:
    router: pa3
```

**Impact Assessment Workflow:**

1. **Before failure:** Query baseline Germany traffic
   ```bash
   query_affected_flows("pa3,ar5,ar6,pe7,pe8")
   query_link_utilization("pa3-p2,pa3-p3,pa4-p2,pa4-p3", 10)
   ```

2. **Inject failure:** Apply NetworkFailure CRD

3. **Detect via GNN:** Anomaly detection on Germany region metrics

4. **Root cause:** GNN identifies pa3 router down

5. **Impact assessment:**
   ```bash
   query_router_metadata("pa3,pa4")  # Identify pa3 as ABR for Area 2
   query_ospf_paths("pe7", "pe1", "")  # Current path (via pa4 only)
   query_affected_flows("pa4,ar5,ar6,ar7,ar8")  # All Germany flows now via pa4
   identify_bottlenecks(50.0)  # Expect pa4 links to show high utilization
   recommend_capacity_upgrades(50.0)  # May suggest pa4 link upgrades
   ```

6. **Remediation decision:**
   - Option 1: Restore pa3 (fast, clears bottleneck)
   - Option 2: Upgrade pa4 links (long-term, improves resilience)

7. **Simulate restoration:**
   ```bash
   simulate_remediation_impact("restore_router", "pa3", "", "pe7", "pe1")
   ```
   Verify traffic shifts back to balanced load across pa3 and pa4

**Remediation:**
Restore pa3 router (delete the NetworkFailure resource).

**Why GNN Detects This:**
- Router down alarm (control plane)
- Germany regional traffic shows severe path changes
- Load doubling on pa4 visible in metrics
- Area 2 isolation pattern in embeddings
- Cross-tier impact cascade (PA→AR→PE)
