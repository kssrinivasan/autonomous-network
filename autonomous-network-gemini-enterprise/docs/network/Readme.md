# Network Simulator

The network simulator runs [VyOS routers](https://vyos.io/) as a set of containers inside a GCE Virtual Machine. Vyos containers are connected over linux bridges to mimick physical connections.

The drawing below shows a sample virtual network deployment in a GCE virtual machine. This is dynamically orchestrated by a network operator running in GKE with performance a logging sent to Cloud Monitoring. 

![virtual network](/docs/drawings/networking.drawio.svg)

The following Linux networking components make up a virtual network as follows:

* **Containers**: Vyos Routers and end host/free5gc devices are deployed in docker containers.
* **Veth Pair**: Virtual ethernet pairs are added to each container to add interfaces inside the container. 
* **Linux Bridge**: the other end of the veth pair is added to a linux bridge which provides a l2 connection between 2 container interfaces. A special management bridge connects all mgmt interfaces for each container allowing their endpoints to be scraped. 

The virtual network lifecycle Automation & monitoring components are as follows:

* [**GKE Operator**](/controller/Readme.md): a k8s operator manages a set of CRDs that provision complete virtual networks. 
* **OpsAgent**: each vyos router container deployed provides a prometheus endpoint that can be scraped by an OpsAgent also installed to the network virtual machine. Vyos syslog is also sent to OpsAgent.
* **Cloud Monitoring**: OpsAgent sends everything it sees and scrapes to Cloud Monitoring. 
* **EventArc**: An eventarc and cloud function [collect metrics](/docs/network/metrics.md) and logs from cloud monitoring and updates Spanner. 

## Accessing the Network VM

The network simulator runs inside the `networkvm` GCE instance. To access it via SSH:

```bash
# SSH into the network VM using IAP tunnel
gcloud compute ssh networkvm \
  --project=${GOOGLE_PROJECT} \
  --zone=${GOOGLE_ZONE} \
  --tunnel-through-iap
```

**Prerequisites:**
- IAP firewall rule must be configured (automatically created by `install.sh`)
- You need the IAM role: `IAP-Secured Tunnel User` or `iap.tunnelInstances.accessViaIAP`

**Troubleshooting:**
If you get an IAP connection error (`Error while connecting [4003: 'failed to connect to backend']`), run the troubleshooter:

```bash
gcloud compute ssh networkvm \
  --project=${GOOGLE_PROJECT} \
  --zone=${GOOGLE_ZONE} \
  --troubleshoot \
  --tunnel-through-iap
```

Common causes:
- Missing IAP firewall rule (should allow ingress from `35.235.240.0/20` on port 22)
- VM is stopped or not running
- Missing IAP permissions

## Sample Transport Network

The diagram below illustrates a L3VPN network provided as an example by the system.

![l3vpn](/docs/drawings/transport/l3vpn-example.drawio.svg)

Two L3VPN services run over a shared MPLS core (OSPF area 0, LDP, iBGP AS 65001) across four P-routers at 1 Gbps, with Route Reflectors `rr1` and `rr2` distributing iBGP routes:

* **BLUE VPN (hub-and-spoke)** — `pe2` is the hub PE (`BLUE_HUB` VRF, imports from both hub and spoke route-targets); `pe1`, `pe3`, and `pe4` are spoke PEs (`BLUE_SPOKE` VRF, imports only the hub route-target). Three CE spoke routers and one CE hub router connect at 100 Mbps. Spokes can reach the hub but never communicate directly with each other — all inter-spoke traffic is controlled via the hub.
* **RED VPN (any-to-any mesh)** — all four PEs (`pe1`–`pe4`) participate in `RED_MESH` with symmetric import/export route-targets (`65035:2000`), giving every CE site full reachability to every other site without a central hub.

[More details on the L3VPN model can be found here.](/docs/network/l3vpn.md)

## Traffic Simulator

The [TrafficTest CRD](/controller/config/infrastructure/traffic.yaml) defines end-to-end network traffic testing scenarios between devices in the virtual network.

A test specifies one or more source devices and a single destination, the transport protocol (TCP or UDP), a target bandwidth, and a duration. Multiple sources are supported simultaneously, enabling aggregate load tests — for example, several branch-office CPEs all sending to a central hub at the same time.

Setting `bidirectional: true` starts a reverse flow from the destination back to each source simultaneously, using independently configurable bandwidth and pattern. This models realistic asymmetric application traffic such as a video stream (large download, small upload) or web browsing (large responses, small requests).

Traffic behaviour is controlled by a configurable pattern type:

- **`constant`** — steady fixed rate; ideal for baseline measurements and SLA verification.
- **`periodic`** — single sine, square, or sawtooth wave; useful for QoS validation.
- **`burst`** — alternates high-rate bursts with idle periods; simulates bursty applications.
- **`poisson`** — stochastic user arrivals with configurable session length and think time.
- **`multi_sine`** — superposes N independent sine waves on a base rate; supports a `wall_clock` time reference so each component naturally peaks at the same UTC hour every day, regardless of when the test started. Gaussian noise can be added for realistic variation. Designed for long-running GNN training-data generation.
- **`schedule`** — piecewise-linear (or step) bandwidth profile defined by named HH:MM UTC waypoints; repeats daily or weekly. Ideal for business-hours and shift-based capacity simulation.

Traffic is generated by the **traffic-agent** binary — a single static Go binary baked into every device container image. The agent exposes per-flow Prometheus metrics on `:9091/metrics` (throughput, latency, jitter, packet loss, active sessions), which are scraped automatically by the Ops Agent and written to Cloud Monitoring and Cloud Spanner — no metrics configuration is needed in the TrafficTest spec. See [`traffic-agent/README.md`](/traffic-agent/README.md) for details.

The operator tracks test execution through a `status` subresource that progresses through the phases `Pending → Deploying → Running → Completed / Failed / Stopped`. Status shows the current phase and a human-readable message per source device. Start and end timestamps are recorded alongside troubleshooting guidance if something goes wrong. Flow metrics (throughput, latency, packet loss, jitter) are not stored in `status` — they are available in Cloud Monitoring and Cloud Spanner via the metrics collector.

Sample traffic tests are provided for both VPNs — [BLUE hub-and-spoke](/examples/controller/transport/default-network/l3vpn-blue-test.yaml) and [RED any-to-any mesh](/examples/controller/transport/default-network/l3vpn-red-test.yaml) — including unidirectional (TCP multi_sine and UDP schedule) and bidirectional variants.

## Virtual Mobile Network

free5gc containers can be attached to a vyos transport network as seen in the picture below. This is work in progress as of now. 

![free5gc](/docs/drawings/free5gc/l3vpn-example.drawio.svg)



