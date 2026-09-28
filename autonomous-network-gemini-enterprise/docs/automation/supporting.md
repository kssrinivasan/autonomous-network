# Supporting Resources

**Source:** `controller/src/` (multiple subdirectories)  
**CRDs:** `controller/config/` (root level)

The operator manages several supporting resources that enable the core network domains. These include VM provisioning, fault injection for chaos testing, and observability infrastructure.

---

## VyOSVM

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get vyosvm`

**Source:** `controller/src/vyosvm/`  
**CRD:** `controller/config/vyosvm.yaml`

### Summary

Provisions the GCP Compute Engine VM that acts as the **Network Automation host**. This VM runs all VyOS router containers as Docker containers and is the target of all Ansible playbooks from the operator.

The operator:
1. Allocates an external IP address (or uses an existing one).
2. Creates an `e2-standard-16` Ubuntu 22.04 GCP VM with the external IP attached.
3. Runs the VyOS VM install playbook to:
   - Install Docker and Docker Compose
   - Install FRRouting (FRR) Prometheus exporters
   - Install and configure GCP Ops Agent for metrics and log collection
   - Configure SSH keys for Ansible access

This is typically a **singleton resource** created once during initial environment setup (`./install.sh`).

### Key spec fields

| Field | Description |
|---|---|
| `spec.machine_type` | GCP machine type (default `e2-standard-16`). |
| `spec.zone` | GCP zone (e.g. `us-central1-a`). |
| `spec.image` | Boot disk image (default Ubuntu 22.04 LTS). |
| `spec.disk_size_gb` | Boot disk size in GB (default 100). |
| `spec.network` | VPC network (default `default`). |
| `spec.external_ip` | External IP address (allocated if not provided). |

### Status phases

`Pending` → `AllocatingIP` → `CreatingVM` → `Installing` → `Ready` / `Failed`

The status exposes:
- `vm_name` — GCP VM instance name
- `external_ip` — Allocated external IP address
- `internal_ip` — VPC internal IP address
- `ssh_command` — SSH command to access the VM (e.g. `ssh -i /path/to/key user@<external_ip>`)

### Example

```yaml
apiVersion: google.dev/v1
kind: VyOSVM
metadata:
  name: network-vm
  namespace: network
spec:
  machine_type: "e2-standard-16"
  zone: "us-central1-a"
  disk_size_gb: 200
```

### Lifecycle Handler

**Path:** `controller/src/vyosvm/lifecycle.py`  
**Playbooks:** `controller/src/vyosvm/playbooks/`

Key behaviors:
- **CREATE:** Allocates IP → creates VM → runs install playbook
- **UPDATE:** Recreates VM on machine type / disk size changes (destructive!)
- **DELETE:** Deletes VM, optionally releases external IP

**Warning:** Deleting a `VyOSVM` resource will destroy all VyOS router containers running on it. Always delete dependent resources first (or use the `./install.sh -k` cleanup script).

---

## NetworkFailure

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get networkfailure`

**Source:** `controller/src/networkfailure/`  
**CRD:** `controller/config/networkfailure.yaml`

### Summary

Injects network faults for chaos engineering and resilience testing. The operator applies faults to VyOS routers, Linux bridges, or 5G network functions by:

- **Link failures** — Bringing down router interfaces or bridge ports
- **Packet loss** — Injecting random packet drops via `tc netem`
- **Latency** — Adding artificial delay via `tc netem`
- **Bandwidth throttling** — Reducing interface bandwidth via `tc tbf`
- **Container crashes** — Stopping/killing Docker containers or Kubernetes pods

Faults are **time-limited** and **automatically reverted** when the `NetworkFailure` resource is deleted or the fault duration expires.

### Key spec fields

| Field | Description |
|---|---|
| `spec.target_type` | Target resource type: `VyOSRouter`, `LinuxNetwork`, `NetworkFunction`, or `Device`. |
| `spec.target_name` | Name of the target resource. |
| `spec.fault_type` | Fault type: `link_down`, `packet_loss`, `latency`, `bandwidth`, or `container_crash`. |
| `spec.fault_config` | Fault-specific configuration (interface name, loss percentage, delay, bandwidth cap, etc.). |
| `spec.duration_seconds` | Fault duration in seconds (auto-reverts after this time). |

### Status phases

`Pending` → `Injecting` → `Active` → `Reverting` → `Completed` / `Failed`

### Example: Link Failure

```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: pe1-eth2-down
  namespace: network
spec:
  target_type: VyOSRouter
  target_name: pe1
  fault_type: link_down
  fault_config:
    interface: "eth2"
  duration_seconds: 300  # 5 minutes
```

### Example: Packet Loss

```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: p1-p2-loss
  namespace: network
spec:
  target_type: LinuxNetwork
  target_name: p1-p2
  fault_type: packet_loss
  fault_config:
    loss_percent: 10  # 10% random packet loss
  duration_seconds: 600
```

### Example: Container Crash

```yaml
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: amf-crash
  namespace: network
spec:
  target_type: NetworkFunction
  target_name: amf-1
  fault_type: container_crash
  duration_seconds: 60  # Crashes immediately, recovers after 60s
```

### Lifecycle Handler

**Path:** `controller/src/networkfailure/lifecycle.py`  
**Playbooks:** `controller/src/networkfailure/playbooks/`

Key behaviors:
- **CREATE:** Validates target → injects fault via Ansible or Kubernetes API → starts revert timer
- **UPDATE:** Re-injects fault on spec changes
- **DELETE:** Immediately reverts fault
- **TIMER (duration_seconds):** Auto-reverts fault and marks resource as `Completed`

---

## Gitea

**Not a CRD.** There used to be a `Gitea` kind with a `gitea_lifecycle` handler in the controller.
Both are gone: Gitea is now a plain GKE `Deployment` installed by a shell script, because a CRD
that wraps a single fixed Deployment buys nothing and the reconcile loop kept fighting the
persistent volume. See [services/gitea/Readme.md](../../services/gitea/Readme.md) for the
deployment itself.

**Source:** `services/gitea/` (`install.sh`, `deployment.tpl.yaml`)

### Summary

A Gitea instance (lightweight Git server) holds the GitOps repositories the design loop writes to:

- **Network-as-Code** — network topology YAML manifests, one CR per file
- **GitOps workflows** — Config Sync applies `root-repo` to the cluster
- **Configuration management** — versioned VyOS configs, NF configs, intents

Shape of the deployment, all of which matters to anything that talks to it:

| Property | Value |
|---|---|
| Workload | `Deployment`, `replicas: 1`, `strategy: Recreate` |
| Storage | `/data` on a PVC with `reclaimPolicy: Retain` — survives a redeploy |
| Database | SQLite on that PVC (no PostgreSQL) |
| Exposure | L4 `Service type=LoadBalancer` on the reserved regional address `gitea-ip` |
| TLS | self-signed — every client passes `verify=False` / `-k` / `noSSLVerify: true` |
| URL | `https://<gitea-ip>` — no hostname; shell callers use `services/gitea/gitea-url.sh`, Python callers read `GITEA_URL` and fall back to the Compute API |

Because `/data` is retained, the admin account, the repositories, the API token and the webhook all
survive `services/gitea/install.sh` being re-run.

### Trust review loop

The webhook that drives design review is a separate Cloud Run FastAPI service —
`services/gitea-webhook/`, not a Cloud Function, and not a controller handler.

The loop:

1. The **designer** agent writes CRs to the `network` repo on the **`update`** branch, never to
   `main` (`agents-in-agent-runtime/designer/app/app_utils/git.py`; `agent_library` cuts the branch
   from `main` on first write).
2. Gitea posts the push to `gitea-webhook` (`POST /webhook/gitea/trust`). Pushes to any other branch
   are ignored, which is what keeps the auto-merge in step 5 from re-triggering the loop.
3. The service fetches the commit diff and the changed-file list from the Gitea API, so the **trust**
   agent reviews only what the commit actually changed.
4. It calls the trust Reasoning Engine's `:query` method, opens (or reuses) a PR from `update` to
   `main`, and posts the verdict as a PR comment.
5. A `**PROCEED**` verdict — and only that; `**DO NOT PROCEED**` and `**PROCEED WITH CAUTION**` are
   excluded — auto-merges the PR. Config Sync then applies `main` to the cluster.

Trust is reached *only* through this path. It is not in the supervisor's `AGENT_RUNTIME_MAP`, so
there is no chat route to it — the commit is the trigger.

**Wiring:** `services/gitea-webhook/install.sh` owns both halves — the Gitea API token the service
authenticates back with, and the push hook itself. It has to run after `deploy_agents.py` (it reads
the trust engine id from `trust/deployment_metadata.json`) and after Gitea exists. `install.sh
--agents` calls it in that position, and `install.sh --webhook` re-runs just this step, which is
what you want after rebuilding Gitea — dropping the Gitea database drops the hook and the token
with it. Both steps are idempotent: the hook is deleted and recreated rather than duplicated.

---

## Graph & Observability

**Source:** `controller/src/graph/`, `controller/src/opsagent/`

### Graph Sync

The operator maintains a **real-time network topology graph** in Cloud Spanner. Every CRD lifecycle handler syncs state changes to Spanner tables:

- **VyOSRouter** → `routers` table (router ID, location, role, interfaces)
- **LinuxNetwork** → `networks` table (network ID, subnet, type, connected routers)
- **Device** → `devices` table (device ID, network, IP address)
- **TrafficTest** → `traffic_tests` table (test ID, sources, destination, metrics)
- **NetworkFunction** → `network_functions` table (NF ID, type, status, endpoints)
- **CellSite** → `cell_sites` table (site ID, location, gNodeB ID, UE count)

**Graph queries** are used by:
- **Network agents** (supervisor, logs, tester) for topology-aware analysis
- **Dashboard** for visualization
- **GNN models** for anomaly detection and root cause analysis

### Spanner Schema

See [Spanner Schema Documentation](../spanner/Readme.md) for full table definitions.

**Key tables:**
- `routers` — Router inventory with SCD Type 2 (Slowly Changing Dimension) for state history
- `networks` — Network segments with SCD Type 2
- `links` — Router-to-network connections (interfaces)
- `traffic_metrics` — Time-series metrics from `TrafficTest` resources
- `nf_status` — 5G NF health and endpoint tracking

### OpsAgent Configuration

**Path:** `controller/src/opsagent/lifecycle.py`

The operator dynamically updates the **GCP Ops Agent** configuration on the Network VM to scrape:
- **FRRouting metrics** from VyOS routers (port 9342 per router)
- **Container metrics** from Docker
- **System metrics** from the VM

When a `VyOSInfrastructure` is created, the operator:
1. Reads the router list from the spec.
2. Generates a Prometheus scrape config for each router's FRR exporter.
3. Updates `/etc/google-cloud-ops-agent/config.yaml` on the Network VM.
4. Restarts the Ops Agent.

On deletion, the scrape config is cleared. On operator restart, the config is re-synced via a `resume` handler.

**Metrics collected:**
- `frr_bgp_peer_up` — BGP neighbour state
- `frr_ospf_neighbor_state` — OSPF neighbour state
- `frr_ldp_peer_up` — LDP peer state
- `frr_route_count` — Routing table size
- Custom VyOS metrics (interface counters, QoS stats, firewall hits)

**Logs collected:**
- VyOS syslog (via Docker logs)
- Kernel logs (network stack, bridge, iptables)
- Ansible playbook execution logs

---

## Testing

### VyOSVM

```bash
# Deploy the network VM
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: VyOSVM
metadata:
  name: network-vm
spec:
  machine_type: "e2-standard-16"
  zone: "us-central1-a"
EOF

# Wait for VM to be ready
kubectl wait --for=condition=ready vyosvm/network-vm --timeout=600s

# Get SSH command
kubectl get vyosvm network-vm -o jsonpath='{.status.ssh_command}'

# SSH into the VM
ssh -i /path/to/key user@<external_ip>
```

### NetworkFailure

```bash
# Inject a link failure
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: pe1-eth2-down
spec:
  target_type: VyOSRouter
  target_name: pe1
  fault_type: link_down
  fault_config:
    interface: "eth2"
  duration_seconds: 300
EOF

# Watch fault status
kubectl get networkfailure pe1-eth2-down -w

# Verify fault (SSH into VM)
docker exec -it pe1 ip link show eth2
# Should show state DOWN

# Revert fault early
kubectl delete networkfailure pe1-eth2-down

# Verify recovery
docker exec -it pe1 ip link show eth2
# Should show state UP
```

### Gitea

```bash
source ./config.sh

# Deploy (or redeploy — /data is on a Retain PVC, so nothing is lost)
cd services/gitea && ./install.sh

# Wait for the pod
kubectl rollout status deployment/gitea -n "${GOOGLE_NAMESPACE}"

# Resolve the URL — the reserved address is the identity, there is no hostname
bash services/gitea/gitea-url.sh

# Reach it (self-signed certificate, hence -k)
curl -k "$(bash services/gitea/gitea-url.sh)/api/healthz"
```

---

## Common Patterns

### Chaos Testing Scenario

```yaml
# Scenario: Inject multiple faults to test network resilience

# 1. Link failure on a PE router
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: pe1-link-down
spec:
  target_type: VyOSRouter
  target_name: pe1
  fault_type: link_down
  fault_config:
    interface: "eth2"
  duration_seconds: 600
---
# 2. AMF crash
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: amf-crash
spec:
  target_type: NetworkFunction
  target_name: amf-1
  fault_type: container_crash
  duration_seconds: 120
---
# 3. Packet loss on core link
apiVersion: google.dev/v1
kind: NetworkFailure
metadata:
  name: p1-p2-loss
spec:
  target_type: LinuxNetwork
  target_name: p1-p2
  fault_type: packet_loss
  fault_config:
    loss_percent: 20
  duration_seconds: 600
```

Monitor the impact:
- **Graph changes** — Check Spanner tables for state transitions
- **Agent responses** — Supervisor agent should detect and escalate incidents
- **Traffic metrics** — TrafficTest resources should show increased latency / loss
- **Recovery time** — Measure time to restore connectivity after fault injection

### GitOps Workflow

1. **Create a Git repository in Gitea** with network topology YAML manifests.
2. **Configure a webhook** to trigger the operator on push events.
3. **Commit changes** to the repository (e.g., add a new router to `VyOSInfrastructure`).
4. **Webhook fires** → operator applies the updated manifest → network topology changes automatically.

See [services/gitea/Readme.md](../../services/gitea/Readme.md) and
[services/gitea-webhook/Readme.md](../../services/gitea-webhook/Readme.md) for the full GitOps workflow.
