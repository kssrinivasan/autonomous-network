# Scenario 4: VRF Route Target Misconfiguration (Medium Network)

**Fault Type:** `VRF_RT_MISCONFIGURATION`

**What to Inject:**
Change the import Route Target on a spoke PE router VRF (e.g., `pe7` BLUE_SPOKE VRF in Area 2) to a non-matching community.

**Why This is Interesting:**
- ✅ **Completely silent failure** — BGP sessions remain Established, zero alarms
- ✅ **Spoke site isolation** — the affected spoke can no longer reach the hub, but control plane looks healthy
- ✅ **Remediation is simple** — restore correct RT import policy
- ✅ **Traffic impact is dramatic** — complete blackout for the affected spoke
- ✅ **Cross-area failure** — demonstrates Area 2 spoke isolation from Area 1 hub
- ⚠️ **Less ideal for "rerouting" assessment** — traffic doesn't reroute, it just fails
- ⚠️ **No congestion analysis** — traffic simply disappears rather than shifting to alternate paths

**Traffic Impact:**
- `ce7-spoke` (connected to `pe7` in Area 2) loses all connectivity to `ce1-hub` (Area 1)
- BGP sessions show Established but routes are not imported into VRF
- Zero alarms in traditional monitoring — requires GNN to detect reachability loss
- Cross-area VPN failure (Area 2 → Area 1)

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: vrf-rt-misconfig-pe7
  namespace: network
spec:
  failureType: VRF_RT_MISCONFIGURATION
  target:
    router: pe7
    vrf: BLUE_SPOKE
  parameters:
    wrong_rt: "65035:9999"
    correct_rt: "65035:1030"
```

**Impact Assessment Queries Before Remediation:**
1. Query VRF routing tables to verify routes are missing
2. Verify BGP VPNv4 routes are being advertised by hub but not imported by spoke
3. Simulate restoration and verify hub routes will be imported
4. Check for potential route loops or black holes
5. Assess whether any routing policies will block restored routes

**Remediation:**
Restore correct RT import community (`65035:1030`) on `pe7` BLUE_SPOKE VRF.

**Why GNN Detects This:**
- Traffic flows from `ce7-spoke` to `ce1-hub` drop to zero
- No BGP or OSPF alarms (all sessions Established)
- Reachability loss detectable only via flow metrics and GNN topology analysis
- Embeddings show VRF isolation without obvious control-plane cause
- Cross-area VPN failure pattern
