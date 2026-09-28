# Transport Domain

**Source:** `controller/src/transport/`  
**CRDs:** `controller/config/transport/`

The Transport domain manages VyOS-based IP/MPLS transport networks, including router provisioning, underlay routing protocols (OSPF, BGP, MPLS/LDP), and Layer 3 VPN services.

## Domain Overview

The Transport domain provides:
- **Router lifecycle** via `VyOSRouter` (VyOS containers with full protocol stacks)
- **Underlay protocols** via `VyOSUnderlay` (OSPF, BGP, MPLS/LDP configuration)
- **L3VPN services** via `VyOSL3VPN` (VRF provisioning on PE routers)

This domain builds on top of the Infrastructure domain's `VyOSInfrastructure` topology declarations.

### Dependency Chain

```
VyOSInfrastructure (Infrastructure domain)
  └── VyOSRouter (generated child, Transport domain)
        └── VyOSUnderlay (Transport domain)
              └── VyOSL3VPN (Transport domain)
```

Each layer waits for the previous to reach `Ready` status before proceeding, enabling safe incremental network build-out.

---

## VyOSRouter

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get vr` or `kubectl get vyosrouters`

### Summary

Represents a single VyOS router container running on the Network VM. `VyOSRouter` resources are usually created automatically by `VyOSInfrastructure` (Infrastructure domain), but can also be created directly for standalone routers.

The operator:
- Waits for all referenced `LinuxNetwork` resources to be `Ready`.
- Uses Ansible to create the VyOS Docker container on the Network VM.
- Applies the full VyOS configuration (interfaces, VLANs, VRFs, OSPF, BGP, MPLS, static routes, DHCP, NAT, QoS, firewall).
- Monitors the container every 120 seconds via timer handlers and updates status on state changes.
- Syncs router status to Cloud Spanner for graph analysis and agent queries.

VyOS routers expose FRRouting (FRR) metrics via a Prometheus exporter on port 9342, which the GCP Ops Agent scrapes for telemetry.

### Key spec fields

| Field | Description |
|---|---|
| `spec.hostname` | Router hostname (used in VyOS CLI prompt). |
| `spec.router_id` | Router ID (IPv4 address, typically the loopback). |
| `spec.image` | VyOS container image, default `vyos:1.5`. |
| `spec.source_network` | Name of the `VyOSInfrastructure` that generated this router (for ownership tracking). |
| `spec.role` | Router role: `P` (provider core), `PE` (provider edge), or `CE` (customer edge). |
| `spec.location` | Physical location metadata: `city`, `country`, `latitude`, `longitude`, `site`. |
| `spec.interfaces[]` | Interface definitions: `name`, `address` (CIDR), `mtu`, `vlan`, `vrf`, `linux_network` (which LinuxNetwork bridge to connect to). |
| `spec.vrfs[]` | VRF definitions: `name`, `table`, `rd` (route distinguisher), `rt_export`, `rt_import` (route targets). |
| `spec.protocols.ospf` | OSPF config: `router_id`, `areas[]` (area ID, networks, interfaces, type), `passive_interfaces[]`. |
| `spec.protocols.bgp` | BGP config: `as_number`, `router_id`, `route_reflector` (bool), `neighbors[]`, `vrfs[]`, `address_families[]`. |
| `spec.protocols.mpls` | MPLS/LDP config: `enabled`, `ldp.router_id`, `ldp.interfaces[]`. |
| `spec.protocols.static` | Static routes: `destination`, `next_hop`, `distance`, `vrf`. |
| `spec.services.dhcp[]` | DHCP server configs per network. |
| `spec.services.nat` | NAT source rules (masquerade or address). |
| `spec.qos` | QoS policy references. |
| `spec.firewall` | Firewall policy references. |

### Status phases

`Pending` → `Creating` → `Configuring` → `Running` / `Failed`

The status exposes:
- `container_id` — Docker container ID
- `ip_address` — Management interface IP
- `interfaces[]` — Per-interface state (up/down, counters)
- `protocols_status` — OSPF/BGP/MPLS neighbour counts and state summaries

### Lifecycle Handler

**Path:** `controller/src/transport/vyosrouter/lifecycle.py`  
**Playbooks:** `controller/src/transport/vyosrouter/playbooks/`

Key behaviors:
- **CREATE:** Waits for `LinuxNetwork` dependencies → Ansible creates VyOS container → applies full config → starts FRR exporter
- **UPDATE:** Patches VyOS config on spec changes (interfaces, VRFs, protocols)
- **DELETE:** Ansible removes container
- **TIMER (120s):** Checks container state, updates status, syncs to Spanner on changes

---

## VyOSUnderlay

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get underlay` or `kubectl get vyosunderlays`

### Summary

Applies underlay routing protocol configuration (OSPF, BGP, MPLS/LDP) on top of an existing `VyOSInfrastructure`. The operator waits for the referenced infrastructure to reach `Ready` status, then patches each named `VyOSRouter` resource with the per-router protocol configuration.

This allows the topology definition (`VyOSInfrastructure`) to remain separate from the protocol configuration, making it easy to swap underlay designs (e.g., OSPF-only → OSPF+MPLS+BGP) without recreating routers.

### Design Philosophy

The underlay provides:
- **Reachability** between router loopbacks (via OSPF or IS-IS)
- **Label distribution** for MPLS forwarding (via LDP or segment routing)
- **iBGP mesh** for VPNv4/VPNv6 route exchange (often via route reflectors)

### Key spec fields

| Field | Description |
|---|---|
| `spec.infrastructureRef` | Name of the `VyOSInfrastructure` to build on top of. Must be `Ready` before this resource is processed. |
| `spec.routing.ospf` | Global OSPF settings: `router_id_source` (loopback interface name), area definitions (`area_id`, `type`). |
| `spec.routing.bgp` | Global BGP settings: `as_number`, `router_id_source`, `route_reflectors[]` (list of loopback IPs). |
| `spec.mpls` | Global MPLS settings: `enabled`, LDP `router_id_interface` (loopback name), `label_range` (min-max). |
| `spec.routers[]` | Per-router protocol overrides. Each entry has a `name` (matching a router in the infrastructure) and a `protocols` block supporting `ospf`, `bgp`, `mpls`, and `static` sections. |

### Lifecycle phases

`Waiting` → `Processing` → `Ready` / `Error`

### Example

```yaml
apiVersion: google.dev/v1
kind: VyOSUnderlay
metadata:
  name: l3vpn-hub-spoke-underlay
  namespace: network
spec:
  infrastructureRef: l3vpn-hub-spoke-infra
  routing:
    ospf:
      router_id_source: "loopback"
      areas:
        - area_id: "0.0.0.0"
          type: "backbone"
    bgp:
      as_number: 65001
      router_id_source: "loopback"
      route_reflectors: ["10.0.0.1", "10.0.0.2"]
  mpls:
    enabled: true
    ldp:
      router_id_interface: "loopback"
  routers:
    - name: "p1"
      protocols:
        ospf:
          router_id: "10.0.0.3"
          areas:
            - area: "0.0.0.0"
              type: "backbone"
              networks: ["172.16.30.0/24"]
        mpls:
          enabled: true
          ldp:
            router_id: "10.0.0.3"
            interfaces: ["eth1", "eth2", "eth3", "eth4", "eth5"]
    - name: "rr1"
      protocols:
        bgp:
          as_number: 65001
          router_id: "10.0.0.1"
          route_reflector: true
          neighbors:
            - peer: "10.0.0.7"
              remote_as: 65001
              route_reflector_client: true
```

### Lifecycle Handler

**Path:** `controller/src/transport/vyosunderlay/lifecycle.py`

Key behaviors:
- **CREATE:** Waits for `VyOSInfrastructure` → patches each `VyOSRouter` with protocol config (via JSON Patch)
- **UPDATE:** Re-patches routers on spec changes
- **DELETE:** No-op (protocols removed when VyOSRouter deleted)

---

## VyOSL3VPN

**Group/Version:** `google.dev/v1`  
**kubectl:** `kubectl get l3vpn` or `kubectl get vyosl3vpns`

### Summary

Applies Layer 3 VPN (VRF) services to PE routers. The operator waits for the referenced `VyOSUnderlay` to reach `Ready` status, then for each router in `spec.routers`:

1. Patches the `VyOSRouter` with VRF definitions (`name`, `table`, RD, RT import/export).
2. Assigns named interfaces to VRFs using a JSON patch.
3. Patches BGP VRF configuration (per-VRF neighbours, address families).

This is the third layer of the dependency chain:  
`VyOSInfrastructure` → `VyOSUnderlay` → `VyOSL3VPN`

### Design Philosophy

L3VPN services provide:
- **Isolated routing tables** per customer/service (VRFs)
- **Route distribution** via MP-BGP VPNv4/VPNv6 address families
- **Flexible topologies** (hub-and-spoke, any-to-any, extranet) via RT import/export policies

### Key spec fields

| Field | Description |
|---|---|
| `spec.underlayRef` | Name of the `VyOSUnderlay` to build on. Must be `Ready` before proceeding. |
| `spec.services[]` | Global L3VPN service definitions: `name`, `type` (l3vpn), `topology` (hub/spoke/any-to-any), `rd`, `rt_export`, `rt_import`. |
| `spec.routers[]` | Per-router VRF and BGP configuration. Each router entry has: `name`, `vrfs[]` (name, table, rd, rt_export, rt_import, interfaces list), `bgp.vrfs[]` (per-VRF neighbours). |

### Example

```yaml
apiVersion: google.dev/v1
kind: VyOSL3VPN
metadata:
  name: l3vpn-blue-service
  namespace: network
spec:
  underlayRef: l3vpn-hub-spoke-underlay
  services:
    - name: "BLUE_SPOKE"
      type: "l3vpn"
      topology: "spoke"
    - name: "BLUE_HUB"
      type: "l3vpn"
      topology: "hub"
  routers:
    - name: "pe1"
      vrfs:
        - name: "BLUE_SPOKE"
          table: 200
          rd: "10.50.50.1:1011"
          rt_export: ["65035:1011"]
          rt_import: ["65035:1030"]
          interfaces: ["eth2"]
      bgp:
        vrfs:
          - name: "BLUE_SPOKE"
            neighbors:
              - peer: "10.50.50.2"
                remote_as: 65035
```

### Lifecycle Handler

**Path:** `controller/src/transport/vyosvpn/lifecycle.py`

Key behaviors:
- **CREATE:** Waits for `VyOSUnderlay` → patches each `VyOSRouter` with VRF + BGP VRF config (via JSON Patch)
- **UPDATE:** Re-patches routers on spec changes
- **DELETE:** No-op (VRFs removed when VyOSRouter deleted)

---

## Testing

```bash
# Deploy infrastructure + underlay + L3VPN in sequence
kubectl apply -f examples/controller/transport/default-network/

# Wait for full stack
kubectl wait --for=condition=ready infra/l3vpn-hub-spoke-infra --timeout=300s
kubectl wait --for=condition=ready underlay/l3vpn-hub-spoke-underlay --timeout=300s
kubectl wait --for=condition=ready l3vpn/l3vpn-blue-service --timeout=300s

# Verify router state
kubectl get vr -o wide

# Check a specific router's protocol status
kubectl get vr pe1 -o jsonpath='{.status.protocols_status}'

# Access a router's VyOS CLI
ssh <network-vm-ip>
docker exec -it <router-container-name> vtysh
```

## Common Patterns

### Hub-and-Spoke L3VPN

```yaml
services:
  - name: "CUSTOMER_HUB"
    type: "l3vpn"
    topology: "hub"
    rt_export: ["65001:100"]
    rt_import: ["65001:100", "65001:101"]  # Imports hub + all spokes
  - name: "CUSTOMER_SPOKE"
    type: "l3vpn"
    topology: "spoke"
    rt_export: ["65001:101"]
    rt_import: ["65001:100"]  # Imports only hub
```

### Any-to-Any L3VPN

```yaml
services:
  - name: "CUSTOMER_ANYTOANY"
    type: "l3vpn"
    topology: "any-to-any"
    rt_export: ["65001:200"]
    rt_import: ["65001:200"]  # All sites import from each other
```

### Extranet (Shared Services)

```yaml
services:
  - name: "CUSTOMER_A"
    rt_export: ["65001:300"]
    rt_import: ["65001:300", "65001:999"]  # Import own + shared
  - name: "SHARED_SERVICES"
    rt_export: ["65001:999"]
    rt_import: ["65001:999"]
```
