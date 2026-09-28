# Infrastructure Domain

**Source:** `controller/src/infrastructure/`  
**CRDs:** `controller/config/infrastructure/`

The Infrastructure domain manages the base network topology, Linux bridges, simulated end-user devices, and traffic testing. This is the foundation layer that other domains build upon.

## Domain Overview

The Infrastructure domain provides:
- **Topology declaration** via `VyOSInfrastructure` (declarative network-as-code)
- **Linux network segments** via `LinuxNetwork` (bridges, VLANs)
- **End-user device simulation** via `Device` (Alpine containers with traffic-agent)
- **Traffic testing** via `TrafficTest` (multi-pattern traffic generation)

All Infrastructure CRDs sync state to Cloud Spanner for graph analysis and agent queries.

---

## VyOSInfrastructure

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get infra` or `kubectl get vyosinfrastructures`

### Summary

The top-level resource for defining a virtual network topology. A single `VyOSInfrastructure` describes all routers and network segments in a declarative YAML manifest. When created, the operator:

1. Validates the topology for consistency (subnet overlap, interface conflicts, etc.).
2. Generates one `LinuxNetwork` resource per network segment.
3. Generates one `VyOSRouter` resource per router.
4. Updates the GCP Ops Agent Prometheus scrape configuration to include all new routers.

On deletion, child resources are garbage-collected and the scrape configuration is cleared. On operator restart, Ops Agent config is re-synced automatically via a `resume` handler.

### Key spec fields

| Field | Description |
|---|---|
| `spec.networks[]` | List of network segments. Each has `name`, `subnet`, optional `vlan`, `mtu`, `network_type` (`p2p`, `multi-access`, `management`, `loopback`), `bandwidth`, and a `connected_routers` list specifying which router interface connects to it. |
| `spec.routers[]` | List of routers. Each has `name`, `hostname`, `router_id`, `role` (P/PE/CE), `image`, optional `location` (lat/long/city/country/site), and `interfaces` mapping interface names to network segments. |
| `spec.qos` | QoS policy definitions (bandwidth, class, DSCP). |
| `spec.security.firewall` | Firewall policy definitions (rules, actions, protocols). |

### Example

```yaml
apiVersion: google.dev/v1
kind: VyOSInfrastructure
metadata:
  name: l3vpn-hub-spoke-infra
  namespace: network
spec:
  description: "L3VPN Hub-and-Spoke Infrastructure"
  networks:
    - name: p1-p2
      subnet: "172.16.30.0/24"
      vlan: 301
      network_type: "p2p"
      connected_routers:
        - router_name: "p1"
          interface: "eth1"
          ip_address: "172.16.30.1"
        - router_name: "p2"
          interface: "eth1"
          ip_address: "172.16.30.2"
    - name: mgmt
      subnet: "192.168.122.0/24"
      gateway: "192.168.122.1"
      network_type: "management"
      connected_routers:
        - router_name: "p1"
          interface: "eth0"
          ip_address: "192.168.122.11"
  routers:
    - name: "p1"
      hostname: "p1"
      router_id: "10.0.0.3"
      role: "P"
      location:
        latitude: 51.5074
        longitude: -0.1278
        city: "London"
        country: "United Kingdom"
      interfaces:
        - name: "eth0"
          network: "mgmt"
        - name: "eth1"
          network: "p1-p2"
        - name: "lo"
          network: "loopbacks"
```

**Full example:** `examples/controller/transport/default-network/` (12-router MPLS topology)

### Lifecycle Handler

**Path:** `controller/src/infrastructure/vyosinfrastructure/lifecycle.py`

Key behaviors:
- **CREATE:** Validates topology → generates `LinuxNetwork` + `VyOSRouter` children → updates Ops Agent scrape config
- **UPDATE:** Regenerates child resources on spec changes
- **DELETE:** Garbage-collects children (via `kopf.adopt()`), clears Ops Agent config
- **RESUME:** Re-syncs Ops Agent config on operator restart

---

## LinuxNetwork

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get lnet` or `kubectl get linuxnetworks`

### Summary

Represents a Linux bridge or Docker network segment on the Network VM. `LinuxNetwork` resources are usually created by `VyOSInfrastructure` but can be created directly for standalone networks. The operator uses Ansible to create the underlying bridge/network and connect router veth interfaces to it.

For `management` type networks, the status also reports the host interface, interface IP, and default gateway.

The operator monitors bridge state every 120 seconds via timer handlers, updating status on changes and syncing to Spanner (SCD Type 2) when bridge or veth state changes.

### Key spec fields

| Field | Description |
|---|---|
| `spec.name` | Bridge/network name (max 15 chars). |
| `spec.source_network` | Parent `VyOSInfrastructure` name. |
| `spec.network_type` | `p2p`, `multi-access`, `loopback`, or `management`. |
| `spec.bandwidth` | Bandwidth cap (e.g. `1gbit`, `100mbit`). |
| `spec.gateway` | Gateway IP (for management networks). |
| `spec.connected_routers[]` | Routers to connect: `router_name`, `interface`, `ip_address`, optional `mac_address`. |

### Status phases

`Pending` → `Creating` → `Ready` / `Failed`

### Lifecycle Handler

**Path:** `controller/src/infrastructure/linuxnetwork/lifecycle.py`  
**Playbooks:** `controller/src/infrastructure/linuxnetwork/playbooks/`

Key behaviors:
- **CREATE:** Ansible playbook creates Docker network or Linux bridge with tc bandwidth limits
- **UPDATE:** Recreates bridge if spec changes
- **DELETE:** Ansible removes bridge
- **TIMER (120s):** Checks bridge state, updates status, syncs to Spanner on changes

---

## Device

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get dev` or `kubectl get devices`

### Summary

Provisions a simulated end-user device or customer premise equipment (CPE) as an Alpine Linux Docker container on the Network VM. Devices attach to a `LinuxNetwork` bridge. They come pre-installed with networking tools (ping, traceroute, tcpdump, iperf3) and the **traffic-agent** Go binary, and are used as traffic endpoints in `TrafficTest` resources.

The operator waits for the referenced `LinuxNetwork` to be `Ready` before creating the container. Device names must be 8 characters or fewer (Docker container name constraint).

### Key spec fields

| Field | Description |
|---|---|
| `spec.network_name` | Name of the `LinuxNetwork` to attach to. Must be `Ready`. |
| `spec.ip_address` | Static IPv4 address for the device (must be within the network subnet). |
| `spec.gateway` | Default gateway (typically the router interface IP on that network). |
| `spec.vlan` | Optional 802.1Q VLAN ID, must match the network segment. |

### Status phases

`Pending` → `Creating` → `Ready` / `Failed`

### Example

```yaml
apiVersion: google.dev/v1
kind: Device
metadata:
  name: dev1
  namespace: network
spec:
  network_name: "lan-spoke1"
  ip_address: "10.100.1.10"
  gateway: "10.100.1.1"
```

### Lifecycle Handler

**Path:** `controller/src/infrastructure/device/lifecycle.py`  
**Playbooks:** `controller/src/infrastructure/device/playbooks/`

Key behaviors:
- **CREATE:** Waits for `LinuxNetwork` → Ansible creates Alpine container with static IP + gateway
- **UPDATE:** Recreates container on spec changes
- **DELETE:** Ansible removes container

---

## TrafficTest

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get tt` or `kubectl get traffictests`

### Summary

Runs a traffic test between one or more source `Device` resources and a single destination `Device` using the **traffic-agent** Go binary embedded in every device container image. The operator:

1. Validates all source and destination `Device` resources are `Ready`.
2. Assigns each source device a unique port via the `PortAllocator` (auto or manual mode, per-namespace port registry).
3. Uses Ansible to `docker exec` `traffic-agent serve` on the destination and `traffic-agent run` on each source.
4. Tracks per-source and aggregate metrics (throughput, latency, packet loss, jitter, active sessions, TCP retransmissions).
5. Publishes metrics to Cloud Spanner for time-series analysis.

Supports six traffic patterns: `constant`, `periodic` (sine/square/sawtooth), `burst`, `poisson`, `multi_sine` (N superimposed sine waves with optional UTC wall-clock anchor), and `schedule` (piecewise-linear bandwidth profile driven by HH:MM waypoints).

See [`traffic-agent/README.md`](../../traffic-agent/README.md) for the traffic-agent binary details and pattern reference.

### Key spec fields

| Field | Description |
|---|---|
| `spec.source_devices[]` | List of source `Device` names. All must be `Ready`. |
| `spec.destination_device` | Destination `Device` name. Must be `Ready`. |
| `spec.protocol` | `TCP` or `UDP`. |
| `spec.port` | Base destination port (each source gets port + index). |
| `spec.duration` | Test duration in seconds. |
| `spec.bandwidth` | Target bandwidth e.g. `100Mbps`, `1Gbps`. |
| `spec.pattern_type` | Traffic pattern: `constant`, `periodic`, `burst`, `poisson`, `multi_sine`, or `schedule`. |
| `spec.pattern_config` | Pattern-specific settings (wave type, period, amplitude, burst rate/duration/interval, Poisson arrival rate, sine components, schedule waypoints). |
| `spec.concurrent_users` | Maximum concurrent sessions (default 1). |
| `spec.metrics_enabled` | Enable metrics collection (default false). |
| `spec.metrics_interval` | Metrics sampling interval in seconds (default 5). |

### Status phases

`Pending` → `Deploying` → `Running` → `Completed` / `Failed` / `Stopped`

Status exposes:
- **Per-source metrics** in `source_status[]` (device name, throughput, latency, packet loss, jitter, active sessions, retransmissions)
- **Aggregate totals** in `aggregate_metrics` (total throughput, avg latency, avg packet loss, total connections)

### Example

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: spoke-to-hub-test
  namespace: default
spec:
  source_devices:
    - dev1
    - dev2
  destination_device: devhub
  protocol: TCP
  port: 5201
  duration: 300
  bandwidth: "100Mbps"
  pattern_type: constant
  metrics_enabled: true
  metrics_interval: 5
```

**Pattern examples:** `examples/controller/transport/default-network/l3vpn-blue-test.yaml`

### Lifecycle Handler

**Path:** `controller/src/infrastructure/traffictest/lifecycle.py`  
**Playbooks:** (none — the handler drives the traffic-agent REST API directly, no Ansible)

Key behaviors:
- **CREATE:** Validates devices → allocates ports → Ansible runs `traffic-agent serve` + `traffic-agent run` → monitors process
- **UPDATE:** Re-allocates ports if changed, restarts traffic if device list changes
- **DELETE:** Kills traffic-agent processes, deallocates ports
- **TIMER (metrics_interval):** Scrapes metrics from running processes, updates status + Spanner
- **STARTUP:** Anti-entropy sync to recover port allocations and running tests after operator restart

---

## Testing

```bash
# Deploy a test topology
kubectl apply -f examples/controller/transport/default-network/

# Wait for infrastructure to be ready
kubectl wait --for=condition=ready infra/l3vpn-hub-spoke-infra --timeout=300s

# Check generated resources
kubectl get lnet  # Should show all network segments
kubectl get vr    # Should show all routers

# Deploy test devices
kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: Device
metadata:
  name: testdev
  namespace: network
spec:
  network_name: "lan-spoke1"
  ip_address: "10.100.1.100"
  gateway: "10.100.1.1"
EOF

# Run a traffic test
kubectl apply -f examples/controller/transport/default-network/l3vpn-blue-test.yaml

# Watch test progress
kubectl get tt -w
```
