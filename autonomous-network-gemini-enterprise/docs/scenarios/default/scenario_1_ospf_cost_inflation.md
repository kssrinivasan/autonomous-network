# Scenario 1: OSPF Cost Inflation on Core Link (Default Network)

**Fault Type:** `OSPF_COST_INFLATION`

**What to Inject:**
Set OSPF cost to 65535 on the `p1-p2` link (London-Manchester) in the core mesh.

**Why This is Ideal:**
- ✅ **Affects cross-city traffic** (London ↔ Manchester, London ↔ Edinburgh via Manchester)
- ✅ **Forces multi-hop rerouting** through alternate core paths (p1→p3→p2 or p1→p4→p2)
- ✅ **Impacts PE layer traffic** (PE routers must reroute)
- ✅ **Demonstrates cascading impact:** core failure → PE reroute → VPN traffic shift
- ✅ **Measurable congestion** at core tier
- ✅ **4-5 hop paths** instead of 2-3 hops (realistic failure impact)
- ✅ **OSPF adjacency remains Full** — no control-plane alarms, requiring GNN to detect via performance anomalies

**Traffic Impact:**
- London→Manchester traffic (pe1 → pe2): reroutes via p3 or p4 (2-3 extra hops)
- Increased latency: ~10-15ms (additional 2-3 hops through core)
- Potential bottlenecks at p3 or p4 due to concentrated rerouted traffic
- Multiple VPN flows affected: ~3 BLUE VPN spoke-to-hub flows + RED VPN mesh traffic

**Example NetworkFailure YAML:**
```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: ospf-cost-inflation-p1-p2
  namespace: network
spec:
  failureType: OSPF_COST_INFLATION
  infrastructureRef: l3vpn-infra
  target:
    router: p1
    interface: eth1  # p1-p2 link
  parameters:
    ospf_cost: 65535
```

**Impact Assessment Queries Before Remediation:**
1. `query_ospf_paths("pe1", "pe2", "p1-p2")` - Current path London→Manchester with cost inflation
2. `query_ospf_paths("pe1", "pe2", "")` - Post-remediation path
3. `query_link_utilization("p1-p3,p3-p2,p1-p4,p4-p2", 10)` - Alternate core paths
4. `query_affected_flows("p1,p2,p3,p4")` - All flows using these routers
5. `query_router_metadata("p1,p2,p3,p4")` - Identify router roles and tiers
6. `identify_bottlenecks(50.0)` - Find links above 50% utilization
7. `simulate_remediation_impact("restore_ospf_cost", "p1", "p1-p2", "pe1", "pe2")` - Full impact analysis

**Remediation:**
Restore original OSPF cost (1) on `p1-p2` link.

**Why GNN Detects This:**
- Anomalous latency increase on cross-city traffic flows (London ↔ Manchester)
- Path metrics change in embeddings across core and PE tiers
- No BGP or OSPF alarms (adjacency still Full)
- Subtle performance degradation requiring ML-based detection

---

## Testing the Scenario

### 1. Inject the Fault

```bash
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: ospf-cost-inflation-p1-p2
  namespace: network
spec:
  failureType: OSPF_COST_INFLATION
  infrastructureRef: l3vpn-infra
  target:
    router: p1
    interface: eth1  # p1-p2 link
  parameters:
    ospf_cost: 65535
EOF
```

### 2. Monitor Fault Injection

```bash
# Watch the NetworkFailure status
kubectl get networkfailure ospf-cost-inflation-p1-p2 -n network -w

# Check detailed status
kubectl describe networkfailure ospf-cost-inflation-p1-p2 -n network

# Should transition: Injecting → Active
```

### 3. Verify VyOSUnderlay Was Patched

```bash
# Check that interface_costs was added to p1's OSPF config
kubectl get vyosunderlay -n network -o yaml | grep -A 15 "name: p1" | grep -A 5 "interface_costs"

# Expected output:
# interface_costs:
# - interface: eth1
#   cost: 65535
```

### 4. Verify VyOSRouter Received the Config

```bash
# Check p1 router's OSPF interface_costs
kubectl get vyosrouter p1 -n network -o jsonpath='{.spec.protocols.ospf.interface_costs}' | jq

# Expected: [{"interface":"eth1","cost":65535}]
```

### 5. Verify OSPF Cost in VyOS

```bash
# SSH to the network VM and check VyOS config
docker exec p1 vtysh -c "show running-config" | grep -A 2 "interface eth1"

# Should show:
# protocols ospf interface eth1 cost 65535

# Check OSPF interface details
docker exec p1 vtysh -c "show ip ospf interface eth1" | grep -i cost

# Should show: Cost: 65535
```

### 6. Observe Traffic Impact

```bash
# Use MCP tools or query Spanner to see path changes
# Example MCP query:
# query_ospf_paths("pe1", "pe2", "p1-p2")

# Check for increased latency on flows using this path
# query_affected_flows("p1,p2")
```

### 7. Restore (Delete NetworkFailure)

```bash
kubectl delete networkfailure ospf-cost-inflation-p1-p2 -n network

# Watch restoration
kubectl get networkfailure ospf-cost-inflation-p1-p2 -n network -w

# Should transition: Active → Restoring → Restored
```

### 8. Verify Restoration

```bash
# Check that cost was restored to 1 (or removed)
kubectl get vyosrouter p1 -n network -o jsonpath='{.spec.protocols.ospf.interface_costs}' | jq

# Verify in VyOS
docker exec p1 vtysh -c "show ip ospf interface eth1" | grep -i cost

# Should show: Cost: 1 (default)
```

**Note:** OSPF_COST_INFLATION uses operator mode injection, which patches the VyOSUnderlay CRD.
The fault is visible in the CRD diff and tracked in both the NetworkFailure CR and Spanner
FaultEvent table. The resulting configuration change flows through to Spanner via the normal
SCD mechanism.
