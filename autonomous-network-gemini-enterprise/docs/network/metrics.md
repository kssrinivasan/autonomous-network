# VyOS Metrics: Collection and Processing

This document summarises the raw metrics scraped from VyOS routers and device containers (traffic-agent), and the metrics that the metrics collector selects and writes to Cloud Spanner.

---

## 1. Raw Metrics Available from VyOS

VyOS exposes two Prometheus-format metric endpoints on each router node. Both are collected by the Google Cloud Ops Agent and stored in Cloud Monitoring under the `prometheus.googleapis.com/` namespace.

### 1.1 System Metrics — `node_exporter` (port 9100)

Provided by the Linux [**node_exporter**](https://github.com/prometheus/node_exporter) running inside the VyOS VM. These cover broad operating system health.

| Category | Metric Prefix | Description |
|---|---|---|
| **CPU** | `node_cpu_seconds_total` | Time spent in each CPU mode (idle, user, system, iowait, steal, softirq, nice, irq) per core |
| | `node_cpu_guest_seconds_total` | Time CPUs spent in guest VMs |
| | `node_load1/5/15` | 1-, 5- and 15-minute load averages |
| | `node_schedstat_running_seconds_total` | Seconds CPU was running processes |
| | `node_schedstat_waiting_seconds_total` | Seconds processes were waiting for CPU |
| | `node_schedstat_timeslices_total` | CPU timeslices executed |
| | `node_context_switches_total` | Total context switches |
| **Memory** | `node_memory_MemTotal_bytes` | Total physical memory |
| | `node_memory_MemFree_bytes` | Free physical memory |
| | `node_memory_MemAvailable_bytes` | Available memory (including reclaimable) |
| | `node_memory_Cached_bytes` | Page cache size |
| | `node_memory_Buffers_bytes` | Buffer cache size |
| | `node_memory_SwapTotal_bytes` / `SwapFree_bytes` | Swap size and usage |
| | `node_memory_Active_bytes` / `Inactive_bytes` | Active and inactive memory |
| | `node_memory_Slab_bytes` | Kernel slab allocator usage |
| | `node_memory_Committed_AS_bytes` | Total committed virtual address space |
| **Network Interfaces** | `node_network_up` | Interface operational state (1 = up) |
| | `node_network_carrier` | Physical carrier state |
| | `node_network_carrier_changes_total` | Total carrier state changes (flaps) |
| | `node_network_carrier_up_changes_total` | Carrier up transitions |
| | `node_network_carrier_down_changes_total` | Carrier down transitions |
| | `node_network_receive_bytes_total` | Total bytes received per interface |
| | `node_network_transmit_bytes_total` | Total bytes transmitted per interface |
| | `node_network_receive_packets_total` | Total packets received |
| | `node_network_transmit_packets_total` | Total packets transmitted |
| | `node_network_receive_errs_total` | Receive errors per interface |
| | `node_network_transmit_errs_total` | Transmit errors per interface |
| | `node_network_receive_drop_total` | Receive drops per interface |
| | `node_network_transmit_drop_total` | Transmit drops per interface |
| | `node_network_receive_multicast_total` | Multicast packets received |
| | `node_network_mtu_bytes` | Interface MTU |
| | `node_network_speed_bytes` | Interface speed |
| | `node_network_info` | Interface metadata (MAC, admin/oper state, duplex, alias) |
| **Disk** | `node_disk_read_bytes_total` / `writes_completed_total` | Disk read/write throughput and IOPS |
| | `node_disk_io_time_seconds_total` | Time spent doing disk I/O |
| **Filesystem** | `node_filesystem_size_bytes` / `free_bytes` / `avail_bytes` | Filesystem capacity, free and available space |
| **Network Statistics** | `node_netstat_Tcp_*` | TCP connection counts, retransmits, errors, opens |
| | `node_netstat_Udp_*` | UDP datagram counters and errors |
| | `node_netstat_Icmp_*` / `Icmp6_*` | ICMP message counts and errors |
| | `node_netstat_Ip_Forwarding` | IP forwarding enabled status |
| | `node_netstat_IpExt_InOctets` / `OutOctets` | Total IP octets in and out |
| **Conntrack** | `node_nf_conntrack_entries` | Current active connection tracking entries |
| | `node_nf_conntrack_entries_limit` | Maximum conntrack table size |
| | `node_nf_conntrack_stat_drop` / `early_drop` | Conntrack drops and forced evictions |
| **Socket Stats** | `node_sockstat_TCP_inuse` / `TCP_alloc` | In-use and allocated TCP sockets |
| | `node_sockstat_UDP_inuse` | In-use UDP sockets |
| | `node_sockstat_sockets_used` | Total IPv4 sockets in use |
| **Softnet** | `node_softnet_processed_total` | Packets processed by CPU softirq |
| | `node_softnet_dropped_total` | Packets dropped in softirq |
| | `node_softnet_times_squeezed_total` | Times packet processing ran out of quota |
| **System** | `node_boot_time_seconds` | Node boot timestamp |
| | `node_forks_total` | Total process forks |
| | `node_intr_total` | Total interrupts serviced |
| | `node_procs_running` / `procs_blocked` | Running and I/O-blocked process counts |
| | `node_pressure_cpu_waiting_seconds_total` | CPU pressure stall time |
| | `node_pressure_io_waiting_seconds_total` | I/O pressure stall time |
| | `node_entropy_available_bits` | Available entropy for random number generation |
| | `node_arp_entries` | ARP table entries per interface |
| | `node_os_info` | OS version and name labels |
| | `node_uname_info` | Kernel and hostname information |

### 1.2 Routing Metrics — `frr_exporter` (port 9101)


Provided by the [**frr_exporter**](https://github.com/tynany/frr_exporter
) running on each VyOS router. These cover the FRR (Free Range Routing) stack.

| Category | Metric | Description |
|---|---|---|
| **BFD** | `frr_bfd_peer_count` | Number of BFD peers detected |
| **Collector Health** | `frr_collector_up` | Whether each FRR collector scraped successfully (collectors: bfd, bgp, bgp6, ospf, route) |
| | `frr_scrape_duration_seconds` | Time taken per collector scrape |
| | `frr_scrapes_total` | Total number of FRR scrapes performed |
| **BGP** | `frr_bgp_peer_state` | BGP peer session state (by AFI, neighbour address, VRF) |
| | `frr_bgp_peer_prefixes_advertised_count_total` | Total prefixes advertised to a BGP peer (by AFI, neighbour address, VRF) |
| | `frr_bgp_peer_prefixes_received_count_total` | Total prefixes received from a BGP peer (by AFI, neighbour address, VRF) |
| | `frr_bgp_peer_uptime_seconds` | BGP peer session uptime in seconds (by AFI, neighbour address, VRF) |
| | `frr_bgp_peer_message_received_total` | Total BGP messages received from a peer |
| | `frr_bgp_peer_message_sent_total` | Total BGP messages sent to a peer |
| | `frr_bgp_peers_count_total` | Total number of BGP peers (by AFI, VRF) |
| | `frr_bgp_peers_memory_bytes` | Memory used by BGP peer structures |
| | `frr_bgp_rib_count_total` | Total routes in the BGP RIB (by AFI, VRF) |
| | `frr_bgp_rib_memory_bytes` | Memory used by the BGP RIB |
| **OSPF** | `frr_ospf_neighbors` | Number of OSPF neighbours detected per interface and VRF |
| | `frr_ospf_neighbor_adjacencies` | Number of full OSPF adjacencies formed per interface and VRF |
| **Routing Table** | `frr_route_total` | Total routes in the routing table (by AFI: ipv4 / ipv6, VRF) |
| | `frr_route_total_fib` | Total routes installed in the FIB (forwarding table) |

### 1.3 free5gc Metrics — Prometheus endpoint (port 9089)

Provided by the free5gc network functions (AMF, SMF, UPF, NRF, UDM, UDR, AUSF, PCF, NSSF, BSF). Each network function exposes Prometheus metrics on port 9089 (configurable via the `metrics.port` setting in the NF config). These metrics cover 5G core network health, control plane signaling, data plane throughput, and subscriber state.

| Category | Metric Prefix | Description |
|---|---|---|
| **Go Runtime** | `go_gc_*` | Garbage collection statistics (duration, pause time, GC cycles) |
| | `go_goroutines` | Number of active goroutines in the network function |
| | `go_memstats_*` | Memory allocator statistics (heap, stack, GC metadata, allocated objects) |
| | `go_threads` | Number of OS threads created |
| **HTTP/SBI** | `free5gc_http_request_duration_seconds` | Histogram of HTTP request latencies for SBI (Service Based Interface) operations |
| | `free5gc_http_requests_total` | Total count of HTTP requests received by the network function |
| | `free5gc_http_response_size_bytes` | Histogram of HTTP response sizes in bytes |
| **AMF (Access & Mobility)** | `free5gc_amf_registered_ue_count` | Number of UEs (User Equipment) currently registered |
| | `free5gc_amf_registered_gnb_count` | Number of gNodeBs currently connected |
| | `free5gc_amf_pdu_session_count` | Number of active PDU sessions |
| | `free5gc_amf_ngap_messages_*` | NGAP message counters (received/sent) |
| | `free5gc_amf_nas_messages_*` | NAS message counters (received/sent) |
| **SMF (Session Management)** | `free5gc_smf_pdu_session_count` | Number of active PDU sessions managed by SMF |
| | `free5gc_smf_pfcp_sessions_count` | Number of active PFCP sessions with UPFs |
| | `free5gc_smf_pfcp_messages_*` | PFCP message counters (received/sent) |
| | `free5gc_smf_upf_association_count` | Number of UPFs currently associated |
| **UPF (User Plane)** | `free5gc_upf_active_sessions_count` | Number of active data plane sessions |
| | `free5gc_upf_packets_*` | User plane packet counters (received/sent) |
| | `free5gc_upf_bytes_*` | User plane byte counters (received/sent) |
| | `free5gc_upf_pfcp_messages_*` | PFCP message counters (received/sent) |
| **NRF (NF Repository)** | `free5gc_nrf_nf_instances_count` | Total NF instances registered |
| | `free5gc_nrf_nf_discovery_requests_total` | NF discovery request count |
| | `free5gc_nrf_nf_registration_requests_total` | NF registration request count |
| | `free5gc_nrf_nf_heartbeat_count` | NF heartbeat message count |
| **AUSF (Authentication)** | `free5gc_ausf_authentication_requests_total` | Total authentication requests |
| | `free5gc_ausf_authentication_success_total` | Successful authentications |
| | `free5gc_ausf_authentication_failures_total` | Failed authentications |
| **UDM (Unified Data Mgmt)** | `free5gc_udm_subscriber_data_requests_total` | Subscriber data requests |
| | `free5gc_udm_subscriber_count` | Number of provisioned subscribers |
| **UDR (Unified Data Repository)** | `free5gc_udr_database_queries_total` | Total database query count |
| | `free5gc_udr_database_query_duration_seconds` | Database query latency histogram |
| **PCF (Policy Control)** | `free5gc_pcf_policy_authorization_requests_total` | Policy authorization requests |
| | `free5gc_pcf_active_policies_count` | Number of active policies |
| **NSSF (Slice Selection)** | `free5gc_nssf_slice_selection_requests_total` | Network slice selection requests |
| | `free5gc_nssf_available_slices_count` | Number of available network slices |
| **BSF (Binding Support)** | `free5gc_bsf_binding_sessions_count` | Active binding sessions |
| | `free5gc_bsf_binding_discovery_requests_total` | Binding discovery requests |
| **Process Stats** | `process_cpu_seconds_total` | Total CPU time (user + system) consumed |
| | `process_open_fds` | Number of open file descriptors |
| | `process_resident_memory_bytes` | Resident memory size |
| | `process_virtual_memory_bytes` | Virtual memory size |
| **Prometheus Handler** | `promhttp_metric_handler_requests_*` | Scrape request counters and in-flight gauge |

**Note:** free5gc metrics are currently **not collected** by the metrics collector or written to Cloud Spanner. They are available for direct Prometheus scraping on port 9089 of each network function container. Future enhancement: add free5gc metrics to the metrics collector pipeline and define a `CoreNetworkMetrics` Spanner table.

---

## 2. Metrics Selected and Written to Cloud Spanner

The metrics collector (`services/metricscollector/src/main.py`) polls Cloud Monitoring every 20 seconds. Rather than ingesting all available metrics, it selects a focused subset relevant to network health and routing state. Only series labelled with `router_name` and belonging to the `vyos-lab` job are processed.

### 2.1 Spanner Table Schema

VyOS router metrics (SYSTEM and ROUTING) are written to the `NetworkMetrics` table with the following columns:

| Column | Description |
|---|---|
| `timestamp` | Metric sample end-time |
| `node_name` | Router name (from `router_name` label) |
| `metric_name` | Raw metric name (e.g. `node_network_up`) |
| `metric_type` | Prometheus type: `gauge` or `counter` |
| `kind` | Category: `SYSTEM` (node_ prefix) or `ROUTING` (frr_ / process_ prefix) |
| `value` | Numeric metric value (float) |
| `labels` | Full label set as JSON (device/interface, VRF, AFI, neighbour, etc.) |
| `interface` | Value of the `device` label if present, else empty string |
| `vrf` | Value of the `vrf` label if present, else empty string |
| `afi` | Value of the `afi` label if present, else empty string |

Traffic-agent metrics are written to a **separate** `TrafficFlowSeries` table (see §2.4).

### 2.2 Selected System Metrics

These are derived from **node_exporter** and categorised as `kind = SYSTEM`:

| Metric Name | Type | Description |
|---|---|---|
| `node_load1` | gauge | 1-minute CPU load average |
| `node_memory_SwapFree_bytes` | gauge | Free swap space in bytes |
| `node_memory_MemTotal_bytes` | gauge | Total physical memory in bytes |
| `node_memory_MemAvailable_bytes` | gauge | Available memory (including reclaimable) in bytes |
| `node_network_up` | gauge | Interface operational state (1 = up, 0 = down) — per interface |
| `node_network_carrier` | gauge | Physical carrier state — per interface |
| `node_network_mtu_bytes` | gauge | Interface MTU in bytes — per interface |
| `node_network_transmit_queue_length` | gauge | Transmit queue length — per interface |
| `node_network_carrier_changes_total` | counter | Cumulative carrier flaps — per interface |
| `node_network_receive_bytes_total` | counter | Bytes received — per interface |
| `node_network_receive_drop_total` | counter | Receive drops — per interface |
| `node_network_receive_errs_total` | counter | Receive errors — per interface |
| `node_network_receive_packets_total` | counter | Packets received — per interface |
| `node_network_transmit_bytes_total` | counter | Bytes transmitted — per interface |
| `node_network_transmit_drop_total` | counter | Transmit drops — per interface |
| `node_network_transmit_errs_total` | counter | Transmit errors — per interface |
| `node_network_transmit_packets_total` | counter | Packets transmitted — per interface |

### 2.3 Selected Routing Metrics

These are derived from **frr_exporter** and categorised as `kind = ROUTING`:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `frr_bfd_peer_count` | gauge | — | Total BFD peers detected on the router |
| `frr_bgp_peer_state` | gauge | `afi`, `neighbor`, `vrf` | BGP peer session state |
| `frr_bgp_peer_prefixes_advertised_count_total` | gauge | `afi`, `neighbor`, `vrf` | Total prefixes advertised to a BGP peer |
| `frr_bgp_peer_prefixes_received_count_total` | gauge | `afi`, `neighbor`, `vrf` | Total prefixes received from a BGP peer |
| `frr_bgp_peer_uptime_seconds` | gauge | `afi`, `neighbor`, `vrf` | BGP peer session uptime in seconds |
| `frr_bgp_peer_message_received_total` | counter | `afi`, `neighbor`, `vrf` | Total BGP messages received from a peer |
| `frr_bgp_peer_message_sent_total` | counter | `afi`, `neighbor`, `vrf` | Total BGP messages sent to a peer |
| `frr_bgp_peers_count_total` | gauge | `afi`, `vrf` | Total number of BGP peers |
| `frr_bgp_peers_memory_bytes` | gauge | `afi`, `vrf` | Memory used by BGP peer structures |
| `frr_bgp_rib_count_total` | gauge | `afi`, `vrf` | Total routes in the BGP RIB |
| `frr_bgp_rib_memory_bytes` | gauge | `afi`, `vrf` | Memory used by the BGP RIB |
| `frr_collector_up` | gauge | `collector` | Health of each FRR data collector (bfd, bgp, bgp6, ospf, route) |
| `frr_ospf_neighbor_adjacencies` | gauge | `area`, `iface`, `vrf` | Full OSPF adjacencies formed per interface |
| `frr_ospf_neighbors` | gauge | `area`, `iface`, `vrf` | OSPF neighbours detected per interface |
| `frr_route_total` | gauge | `afi`, `vrf` | Total routes in the routing table |
| `frr_route_total_fib` | gauge | `afi`, `vrf` | Total routes installed in the forwarding table |

**Not collected: the `process_*` exporter self-metrics.** `process_open_fds`,
`process_network_receive_bytes_total` and `process_network_transmit_bytes_total` are emitted by
both exporters on a router — node_exporter on `:9100` and frr_exporter on `:9101` — under the same
`router_name`. Cloud Monitoring separates them only by the *resource* label `instance`, which the
collector does not read, so the two emitters collided on write: 54 rows per poll, 3 metrics × 18
routers. They were dropped from `SELECTED_METRICS` rather than given a key column, because nothing
consumed them and `frr_collector_up` already reports exporter liveness. See 7.8b in the migration
plan.


## 2.4. Traffic-Agent Metrics (Device Containers, port 9091)

Traffic-agent metrics are written to Cloud Spanner by the `metricscollector` into the **`TrafficFlowSeries`** table (separate from the router tables). The `node_name` column is set to the source device name extracted from the `flow_id` (e.g. `"dev1-to-hub-tcp_dev1"` → `node_name="dev1"`). For bidirectional reverse flows (`*_rev` suffix) the `_rev` is stripped before extraction so the correct device name is stored.

Because `node_name` is derived from `flow_id`, **both ends of a leg carry the same `node_name`**. The `role` column (`source` / `destination`) is what distinguishes them, and it is part of the primary key for that reason — the "Populated on" column below is exactly why. Reading a metric from the wrong end returns 0, or in the case of `packet_loss_pct` a fabricated ~50%. This was the defect fixed by replacing `TrafficFlowMetrics`; see migration plan 7.8a.

| Metric | Type | Populated on | Description |
|---|---|---|---|
| `traffic_agent_bytes_sent_total` | Counter | `role=source` only | Total bytes sent by this flow (0 on destination side) |
| `traffic_agent_bytes_received_total` | Counter | `role=destination` only | Total bytes received by this flow (0 on source side) |
| `traffic_agent_throughput_bps` | Gauge | Both | **Bidirectional** throughput in bits/sec (sent + received). Kept for backward compatibility — prefer `throughput_sent_bps` / `throughput_recv_bps` (see note below) |
| `traffic_agent_throughput_sent_bps` | Gauge | Both | **Outbound-only** throughput in bits/sec (bytes sent since last scrape × 8 ÷ elapsed). Use this for source-role flows to get the true one-directional send rate |
| `traffic_agent_throughput_recv_bps` | Gauge | Both | **Inbound-only** throughput in bits/sec (bytes received since last scrape × 8 ÷ elapsed). Use this for destination-role flows to get the true one-directional receive rate |
| `traffic_agent_latency_ms` | Gauge | `role=destination`, UDP only | Mean one-way latency in milliseconds (OWD stamped at receiver) |
| `traffic_agent_jitter_ms` | Gauge | `role=destination`, UDP only | Mean inter-packet jitter in milliseconds |
| `traffic_agent_packet_loss_pct` | Gauge | `role=destination`, UDP only | Packet loss percentage (sequence-number gap detection at receiver) |
| `traffic_agent_active_sessions` | Gauge | `role=source` only | Number of concurrent sessions within this flow |
| `traffic_agent_flow_running` | Gauge | Both | `1` if flow phase is `running`, `0` for all other phases |

**`throughput_bps` vs `throughput_sent_bps` / `throughput_recv_bps`:**

The original `throughput_bps` gauge computes `(bytes_sent + bytes_received since last scrape) × 8 ÷ elapsed_seconds`. For a **bidirectional** flow this means the source agent reports both its outbound traffic **and** the reverse traffic it receives, resulting in a value approximately **2× the actual link rate** (e.g. 180 Mbps for a 90 Mbps bidirectional test).

The new directional gauges fix this:
- **`throughput_sent_bps`** = `bytes_sent_delta × 8 ÷ elapsed` — true outbound rate (use for source-role flows)
- **`throughput_recv_bps`** = `bytes_received_delta × 8 ÷ elapsed` — true inbound rate (use for destination-role flows)

The UI Performance panel uses `throughput_sent_bps` from source-role flows for the aggregate throughput display.

All three gauges are delta-based — do **not** apply `rate()` to them; query them directly.

**Latency, jitter, packet loss** are computed from UDP sequence-number timestamps embedded in each packet. They are not available for TCP flows. These metrics are only meaningful on **destination-role** flows (the receiver measures one-way delay and sequence gaps); source-role values will always be 0.

### 2.5 Aggregation

Before storage, metrics are aligned over the poll window (20 seconds):

- **Counters** (`CUMULATIVE` kind): aligned using `ALIGN_RATE` — the value stored is the per-second rate of change over the window.
- **Gauges** (`GAUGE` kind): aligned using `ALIGN_MEAN` — the average value over the window.

### 2.6 Retention

A background thread runs every 20 minutes and deletes rows from the router metric tables and `TrafficFlowSeries` older than `RETENTION_HOURS` (default **4 hours**), keeping the Spanner tables compact and relevant for near-real-time analysis. Spanner also carries a one-day `ROW DELETION POLICY` on these tables as a backstop; BigQuery holds the same rows for analysis past both windows.


### 3.3 Example Prometheus Queries

```promql
# Forward throughput (source → destination)
traffic_agent_throughput_bps{flow_id="dev1-to-hub-tcp_dev1", role="source"}

# Received throughput on the hub for the same flow
traffic_agent_throughput_bps{flow_id="dev1-to-hub-tcp_dev1", role="destination"}

# All flows currently running (either direction)
traffic_agent_flow_running == 1

# UDP packet loss on any active spoke→hub flow
traffic_agent_packet_loss_pct{flow_id=~".*_dev.*", role="source"}

# Reverse flow (bidirectional) throughput from hub back to dev1
traffic_agent_throughput_bps{flow_id="dev1-hub-tcp-bidir_dev1_rev", role="source"}
```

---

## 4. Data Flow Summary

```
VyOS Router
  ├── node_exporter  :9100  ──┐
  └── frr_exporter   :9101  ──┤
                              │  (Prometheus scrape, job=vyos-lab)
Device Container (per device)
  └── traffic-agent  :9091  ──┤
                              │  (Prometheus scrape, job=traffic-agents)
free5gc Network Function
  └── prometheus     :9089  ──┤  (available, not yet collected)
                              │
                    GCP Ops Agent
                              │
                    Cloud Monitoring
                    (prometheus.googleapis.com/*)
                              │
                    metricscollector (Cloud Run)
                    - Polls every 20s
                    - 36 VyOS metrics  (job=vyos-lab)
                        node_* → RouterSystemMetrics, frr_* → RouterRoutingSeries
                    - 10 traffic-agent metrics (job=traffic-agents) → TrafficFlowSeries
                    - Aggregates (rate or mean)
                    - free5gc metrics: not yet implemented
                              │
                    Cloud Spanner                        BigQuery
                    RouterSystemMetrics  (transport)     serviceperformance dataset
                    RouterRoutingSeries  (transport)     one table per Spanner table,
                    TrafficFlowSeries    (service)       named in lower case
                    CoreNetworkMetrics   (future — for free5gc metrics)
```
