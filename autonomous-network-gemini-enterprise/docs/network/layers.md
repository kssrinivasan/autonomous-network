# Network Layers and relationships

![layers](/docs/drawings/transport/l3vpn-example-layers.drawio.svg)

## Layer Config and Metrics

Each layer in the network model has a corresponding configuration resource (CRD) and a set of metrics collected from the routers. The table below summarises the relationship between layers, config, and metrics.

---

### Layer 1 — Physical Hardware

**Config** — Defined by the `VyOSInfrastructure` CRD ([`infrastructure.yaml`](/examples/controller/transport/default-network/infrastructure.yaml)). Specifies all routers, physical interfaces, point-to-point links, LAN segments, and attached end devices.

**Metrics** — Physical interface health scraped from `node_exporter` (port 9100) on each VyOS router, stored in Cloud Spanner with `kind=SYSTEM`:

| Metric | Type | Description |
|---|---|---|
| `node_network_up` | gauge | Interface operational state (1 = up, 0 = down) |
| `node_network_carrier` | gauge | Physical carrier state |
| `node_network_carrier_changes_total` | counter | Cumulative carrier flaps per interface |
| `node_network_mtu_bytes` | gauge | Interface MTU |
| `node_network_receive_bytes_total` | counter | Bytes received per interface |
| `node_network_transmit_bytes_total` | counter | Bytes transmitted per interface |
| `node_network_receive_packets_total` | counter | Packets received per interface |
| `node_network_transmit_packets_total` | counter | Packets transmitted per interface |
| `node_network_receive_errs_total` | counter | Receive errors per interface |
| `node_network_transmit_errs_total` | counter | Transmit errors per interface |
| `node_network_receive_drop_total` | counter | Receive drops per interface |
| `node_network_transmit_drop_total` | counter | Transmit drops per interface |
| `node_load1` | gauge | 1-minute CPU load average (router health) |
| `node_memory_MemAvailable_bytes` | gauge | Available memory in bytes (router health) |

---

### Layer 2 — Underlay Network

**Config** — Defined by the `VyOSUnderlay` CRD ([`underlay.yaml`](/examples/controller/transport/default-network/underlay.yaml)). Configures OSPF (area 0 backbone), iBGP (AS 65001 with `rr1`/`rr2` as route reflectors), and MPLS/LDP on all P and PE routers.

**Metrics** — Routing protocol health scraped from `frr_exporter` (port 9101) on each VyOS router, stored in Cloud Spanner with `kind=ROUTING`:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `frr_ospf_neighbors` | gauge | `area`, `iface`, `vrf` | OSPF neighbours detected per interface |
| `frr_ospf_neighbor_adjacencies` | gauge | `area`, `iface`, `vrf` | Full OSPF adjacencies formed per interface |
| `frr_bgp_peer_uptime_seconds` | gauge | `afi`, `neighbor`, `vrf` | iBGP peer session uptime in seconds |
| `frr_route_total` | gauge | `afi`, `vrf` | Total routes in the routing table |
| `frr_route_total_fib` | gauge | `afi`, `vrf` | Routes installed in the forwarding table (FIB) |
| `frr_collector_up` | gauge | `collector` | Health of each FRR data collector (ospf, bgp, bfd, route) |

---

### Layer 3 — Edge Overlay

**Config** — Defined by `VyOSL3VPN` CRDs, one per VPN service. Each resource references the underlay by name and configures VRF names, route distinguishers (RDs), route-targets (RT import/export), CE-facing interfaces, and eBGP peerings towards CE routers.

- BLUE VPN (hub-and-spoke): [`blue-vpn.yaml`](/examples/controller/transport/default-network/blue-vpn.yaml)
- RED VPN (any-to-any mesh): [`red-vpn.yaml`](/examples/controller/transport/default-network/red-vpn.yaml)

**Metrics** — VPN control-plane health (stored with `kind=ROUTING`) and end-to-end traffic metrics from the traffic-agent on each device container (stored with `kind=TRAFFIC`):

| Metric | Kind | Labels | Description |
|---|---|---|---|
| `frr_bgp_peer_prefixes_advertised_count_total` | ROUTING | `afi`, `neighbor`, `vrf` | Prefixes advertised per BGP peer and VRF |
| `frr_bfd_peer_count` | ROUTING | — | BFD peers detected on the router |
| `traffic_agent_throughput_bps` | TRAFFIC | `flow_id`, `role` | Instantaneous throughput per flow in bits/sec |
| `traffic_agent_bytes_sent_total` | TRAFFIC | `flow_id`, `role=source` | Total bytes sent by a flow |
| `traffic_agent_bytes_received_total` | TRAFFIC | `flow_id`, `role=destination` | Total bytes received by a flow |
| `traffic_agent_latency_ms` | TRAFFIC | `flow_id`, `role=source` | One-way latency in ms (UDP flows only) |
| `traffic_agent_jitter_ms` | TRAFFIC | `flow_id`, `role=source` | Inter-packet jitter in ms (UDP flows only) |
| `traffic_agent_packet_loss_pct` | TRAFFIC | `flow_id`, `role=source` | Packet loss percentage (UDP flows only) |
| `traffic_agent_active_sessions` | TRAFFIC | `flow_id`, `role=source` | Concurrent sessions within a flow |
| `traffic_agent_flow_running` | TRAFFIC | `flow_id`, `role` | 1 if flow is active, 0 otherwise |

Traffic metrics are written to the `NetworkMetrics` Spanner table with `interface=flow_id` and `node_name` set to the source device name. See [metrics.md](/docs/network/metrics.md) for full schema and aggregation details.
