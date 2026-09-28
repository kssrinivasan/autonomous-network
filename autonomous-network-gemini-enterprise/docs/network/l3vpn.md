# L3 VPN Model

The example in the demo is a L3 VPN with multiple sites provisioned on a number of VyOS routers. This section describes the physical and logical models. 

## L3 VPN

L3VPN consists of multiple access links, VPN routing and forwarding (VRF) tables, and MPLS paths or P2MP LSPs. A L3VPN can be configured to connect two or more customer sites. In hub-and-spoke MPLS L3VPN environments, the spoke routers have unique Route Distinguishers (RDs). The spoke sites export their routes to the hub. Spokes can talk to hubs, but never have direct paths to other spokes. All traffic is controlled and delivered over the hub site.

The telco-lab provisions two L3VPN services over a shared MPLS provider network:

* **BLUE VPN** — hub-and-spoke topology. `pe2` is the hub PE (VRF `BLUE_HUB`) and `pe1`, `pe3`, `pe4` are spoke PEs (VRF `BLUE_SPOKE`).
* **RED VPN** — any-to-any mesh topology. All four PEs (`pe1`–`pe4`) participate in VRF `RED_MESH` with symmetric import/export route-targets, so every site can reach every other site directly.

![example](/docs/drawings/transport/l3vpn-example.drawio.svg)

Multi-protocol BGP (MP-BGP) delivers L3VPN control-plane information across the network via two Route Reflectors (`rr1`, `rr2`). The PE spokes import the route-target `65035:1030` (the `BLUE_HUB` export RT) and export their own RT `65035:1011`. The hub imports both `65035:1011` and `65035:1030` so it can re-advertise spoke prefixes back out.

The customer edge nodes on the spoke sites can only learn the network prefixes of the hub site.

### BLUE VPN — VRF Summary

| Node | Role  | VRF        | RD              | RT Import              | RT Export  |
|------|-------|------------|-----------------|------------------------|------------|
| pe2  | Hub   | BLUE_HUB   | 10.80.80.1:1011 | 65035:1011, 65035:1030 | 65035:1030 |
| pe1  | Spoke | BLUE_SPOKE | 10.50.50.1:1011 | 65035:1030             | 65035:1011 |
| pe3  | Spoke | BLUE_SPOKE | 10.60.60.1:1011 | 65035:1030             | 65035:1011 |
| pe4  | Spoke | BLUE_SPOKE | 10.70.70.1:1011 | 65035:1030             | 65035:1011 |

### RED VPN — VRF Summary

| Node | Role | VRF      | RD              | RT Import  | RT Export  |
|------|------|----------|-----------------|------------|------------|
| pe1  | Mesh | RED_MESH | 10.55.55.1:2000 | 65035:2000 | 65035:2000 |
| pe2  | Mesh | RED_MESH | 10.65.65.1:2000 | 65035:2000 | 65035:2000 |
| pe3  | Mesh | RED_MESH | 10.75.75.1:2000 | 65035:2000 | 65035:2000 |
| pe4  | Mesh | RED_MESH | 10.85.85.1:2000 | 65035:2000 | 65035:2000 |

The following sections break this L3 VPN topology into logically separate models that have different but interlinked lifecycles. These models provide the schema for representing the network in the network topology service.

## Physical Model

This model represents the physical connectivity and capacity available in the network. Modelling vendor and location aspects of the physical routers and the physical links connecting router ports together.

![physical model](/docs/drawings/transport/physical_model.drawio.svg)

### Nodes

The following nodes are modelled.

* __Physical_Router:__ Represents any physical router device (PE, P, RR).
    * __Properties:__ router_id, name, make, model, serial_number, location, role (e.g., "PE", "P", "RR")
* __Physical_Interface:__ Represents a physical port on a Physical_Router.
    * __Properties:__ interface_id, name (e.g., "eth1"), port_speed, media_type, ip_address
* __Physical_Link:__ Represents a direct physical cable connection between two Physical_Interfaces.
    * __Properties:__ link_id, bandwidth, status (e.g., "up", "down")

### Relationships:

#### HAS_PHYSICAL_INTERFACE:
Direction: Physical_Router -> Physical_Interface
Description: A physical router has multiple physical interfaces.

#### CONNECTS_PHYSICALLY_TO:
Direction: Physical_Interface -> Physical_Link
Description: A physical interface is an endpoint of a physical link. (Or direct between interfaces, but using a Physical_Link node allows adding link-specific properties like status, bandwidth, etc.)

#### PART_OF_PHYSICAL_LINK:

Direction: Physical_Link -> Physical_Interface (inverse of above, for clarity)
Description: A physical link connects two specific physical interfaces.

#### FORWARDS_TRAFFIC_VIA: (Implicit, but useful for understanding the overlay on underlay)

Direction: PE_Router (logical) -> Physical_Router (physical)
Description: A logical PE router entity is instantiated on a specific physical router. (This links the two models)

### Telco-Lab Physical Topology

The telco-lab infrastructure (`l3vpn-hub-spoke-infra`) consists of the following nodes spread across UK data centre locations:

#### Provider (P) Routers

| Name | Router ID | Location       | Site           |
|------|-----------|----------------|----------------|
| p1   | 10.0.0.3  | London, UK     | London-DC1     |
| p2   | 10.0.0.4  | Manchester, UK | Manchester-DC1 |
| p3   | 10.0.0.5  | Edinburgh, UK  | Edinburgh-DC1  |
| p4   | 10.0.0.6  | Leeds, UK      | Leeds-DC1      |

#### Route Reflectors (RR)

| Name | Router ID | Location       | Site            |
|------|-----------|----------------|-----------------|
| rr1  | 10.0.0.1  | Birmingham, UK | Birmingham-DC1  |
| rr2  | 10.0.0.2  | Bristol, UK    | Bristol-DC1     |

#### Provider Edge (PE) Routers

| Name | Router ID | Location       | Site           |
|------|-----------|----------------|----------------|
| pe1  | 10.0.0.7  | Oxford, UK     | Oxford-DC1     |
| pe2  | 10.0.0.8  | Cambridge, UK  | Cambridge-DC1  |
| pe3  | 10.0.0.10 | Brighton, UK   | Brighton-DC1   |
| pe4  | 10.0.0.11 | Cardiff, UK    | Cardiff-DC1    |

#### Customer Edge (CE) Routers — BLUE VPN

| Name      | Router ID  | Location         | Site               | Role  |
|-----------|------------|------------------|--------------------|-------|
| ce1-spoke | 10.0.0.80  | Sheffield, UK    | Sheffield-Site1    | Spoke |
| ce1-hub   | 10.0.0.100 | Nottingham, UK   | Nottingham-Hub1    | Hub   |
| ce2-spoke | 10.0.0.90  | Liverpool, UK    | Liverpool-Site1    | Spoke |
| ce3-spoke | 10.0.0.91  | Huddersfield, UK | Huddersfield-Site1 | Spoke |

#### Customer Edge (CE) Routers — RED VPN

| Name    | Router ID  | Location       | Site            |
|---------|------------|----------------|-----------------|
| ce1-red | 10.0.0.101 | Norwich, UK    | Norwich-Site1   |
| ce2-red | 10.0.0.102 | Coventry, UK   | Coventry-Site1  |
| ce3-red | 10.0.0.103 | Plymouth, UK   | Plymouth-Site1  |
| ce4-red | 10.0.0.104 | Leicester, UK  | Leicester-Site1 |

#### Point-to-Point Links (Provider Core)

| Link Name | Subnet            | VLAN | Router A (iface) | Router B (iface) |
|-----------|-------------------|------|------------------|------------------|
| p1-p2     | 172.16.30.0/24    | 301  | p1 (eth1)        | p2 (eth1)        |
| p1-p3     | 172.16.40.0/24    | 302  | p1 (eth2)        | p3 (eth2)        |
| p2-p4     | 172.16.60.0/24    | 303  | p2 (eth2)        | p4 (eth2)        |
| p3-p4     | 172.16.50.0/24    | 304  | p3 (eth3)        | p4 (eth3)        |
| p1-rr1    | 172.16.10.0/24    | 305  | p1 (eth4)        | rr1 (eth2)       |
| p2-rr1    | 172.16.20.0/24    | 306  | p2 (eth3)        | rr1 (eth1)       |
| p3-rr2    | 172.16.70.0/24    | 307  | p3 (eth4)        | rr2 (eth2)       |
| p4-rr2    | 172.16.80.0/24    | 308  | p4 (eth1)        | rr2 (eth1)       |
| p1-pe1    | 172.16.90.0/24    | 309  | p1 (eth3)        | pe1 (eth1)       |
| p1-pe2    | 172.16.100.0/24   | 310  | p1 (eth5)        | pe2 (eth1)       |
| p3-pe2    | 172.16.110.0/24   | 311  | p3 (eth1)        | pe2 (eth2)       |
| p4-pe3    | 172.16.140.0/24   | 312  | p4 (eth4)        | pe3 (eth1)       |
| p2-pe4    | 172.16.150.0/24   | 313  | p2 (eth4)        | pe4 (eth1)       |
| p3-pe1    | 172.16.160.0/24   | 314  | p3 (eth5)        | pe1 (eth4)       |
| p2-pe3    | 172.16.170.0/24   | 315  | p2 (eth5)        | pe3 (eth4)       |

#### PE-to-CE Links (Customer-Facing) — BLUE VPN

| Link Name     | Subnet         | VLAN | PE              | CE              |
|---------------|----------------|------|-----------------|-----------------|
| pe1-ce1-spoke | 10.50.50.0/24  | 401  | pe1 eth2 (.1)   | ce1-spoke eth1 (.2) |
| pe2-ce1-hub   | 10.80.80.0/24  | 402  | pe2 eth3 (.1)   | ce1-hub eth1 (.2)   |
| pe3-ce2-spoke | 10.60.60.0/24  | 403  | pe3 eth2 (.1)   | ce2-spoke eth1 (.2) |
| pe4-ce3-spoke | 10.70.70.0/24  | 404  | pe4 eth2 (.1)   | ce3-spoke eth1 (.2) |

#### PE-to-CE Links (Customer-Facing) — RED VPN

| Link Name    | Subnet         | VLAN | PE              | CE              |
|--------------|----------------|------|-----------------|-----------------|
| pe1-ce1-red  | 10.55.55.0/24  | 405  | pe1 eth3 (.1)   | ce1-red eth1 (.2) |
| pe2-ce2-red  | 10.65.65.0/24  | 406  | pe2 eth4 (.1)   | ce2-red eth1 (.2) |
| pe3-ce3-red  | 10.75.75.0/24  | 407  | pe3 eth3 (.1)   | ce3-red eth1 (.2) |
| pe4-ce4-red  | 10.85.85.0/24  | 408  | pe4 eth3 (.1)   | ce4-red eth1 (.2) |

#### LAN Networks

| Network   | Subnet          | Gateway      | CE Router | Interface |
|-----------|-----------------|--------------|-----------|-----------|
| lan-spoke1 | 10.100.1.0/24  | 10.100.1.1   | ce1-spoke | eth2      |
| lan-hub    | 10.100.2.0/24  | 10.100.2.1   | ce1-hub   | eth2      |
| lan-spoke2 | 10.100.3.0/24  | 10.100.3.1   | ce2-spoke | eth2      |
| lan-spoke3 | 10.100.4.0/24  | 10.100.4.1   | ce3-spoke | eth2      |
| lan-red1   | 10.101.1.0/24  | 10.101.1.1   | ce1-red   | eth2      |
| lan-red2   | 10.101.2.0/24  | 10.101.2.1   | ce2-red   | eth2      |
| lan-red3   | 10.101.3.0/24  | 10.101.3.1   | ce3-red   | eth2      |
| lan-red4   | 10.101.4.0/24  | 10.101.4.1   | ce4-red   | eth2      |

### Linking Logical to Physical

The logical PE router nodes (pe1–pe4) from the VPN service model are realized by the corresponding physical router nodes. This cross-layer linkage allows the topology service to answer questions such as:

* What physical router is hosting a specific logical PE?
* What physical links are traversed between two PE routers?
* What is the bandwidth of the physical link supporting a particular logical VPN segment?
* If a physical interface goes down, which logical services (L3VPNs, VRFs) might be affected?

This multi-layered graph model provides a powerful way to represent and analyze complex telco networks.

## Infrastructure Descriptor

The `VyOSInfrastructure` resource describes the full physical topology — routers, interfaces, point-to-point links, LAN segments, and attached end devices. The telco-lab infrastructure is defined in [`examples/controller/transport/default-network/infrastructure.yaml`](/examples/controller/transport/default-network/infrastructure.yaml).

## Underlay Descriptor

The underlay configures P and PE routers with OSPF (area 0 backbone), iBGP (AS 65001 with `rr1`/`rr2` as route reflectors), and MPLS/LDP. The underlay is defined in [`examples/controller/transport/default-network/underlay.yaml`](/examples/controller/transport/default-network/underlay.yaml).

![underlay model](/docs/drawings/transport/underlay_logical_models.drawio.svg)

```yaml
apiVersion: google.dev/v1
kind: VyOSUnderlay
metadata:
  name: l3vpn-hub-spoke-underlay
  namespace: default
spec:
  infrastructureRef: l3vpn-hub-spoke-infra
  routing:
    ospf:
      router_id_source: "loopback"
      areas: [{ area_id: "0.0.0.0", type: "backbone" }]
    bgp:
      as_number: 65001
      router_id_source: "loopback"
      route_reflectors: ["10.0.0.1", "10.0.0.2"]
  mpls:
    enabled: true
    ldp:
      router_id_interface: "loopback"
  routers:
    - name: "rr1"
      protocols:
        ospf:
          router_id: "10.0.0.1"
        bgp:
          as_number: 65001
          router_id: "10.0.0.1"
          route_reflector: true
          neighbors:
            - { peer: "10.0.0.7", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.8", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.10", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.11", remote_as: 65001, route_reflector_client: true }
    - name: "rr2"
      protocols:
        ospf:
          router_id: "10.0.0.2"
        bgp:
          as_number: 65001
          router_id: "10.0.0.2"
          route_reflector: true
          neighbors:
            - { peer: "10.0.0.7", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.8", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.10", remote_as: 65001, route_reflector_client: true }
            - { peer: "10.0.0.11", remote_as: 65001, route_reflector_client: true }
    # pe1 – pe4 peer to both route reflectors
    - name: "pe1"
      protocols:
        ospf:
          router_id: "10.0.0.7"
        bgp:
          as_number: 65001
          router_id: "10.0.0.7"
          neighbors: [{ peer: "10.0.0.1", remote_as: 65001 }, { peer: "10.0.0.2", remote_as: 65001 }]
        mpls:
          enabled: true
          ldp: { router_id: "10.0.0.7", interfaces: ["eth1", "eth4"] }
```

## L3VPN Service Descriptor

The `VyOSL3VPN` resource describes the overlay VPN service — VRF names, RDs, route-targets, CE-facing interfaces, and eBGP peerings towards CE routers. Each VPN service references the underlay by name.

![edge model](/docs/drawings/transport/edge_logical_model.drawio.svg)

### BLUE VPN (Hub-and-Spoke)

Defined in [`examples/controller/transport/default-network/blue-vpn.yaml`](/examples/controller/transport/default-network/blue-vpn.yaml).

```yaml
apiVersion: google.dev/v1
kind: VyOSL3VPN
metadata:
  name: l3vpn-blue-service
  namespace: default
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
            neighbors: [{ peer: "10.50.50.2", remote_as: 65035 }]
    - name: "pe2"
      vrfs:
        - name: "BLUE_HUB"
          table: 400
          rd: "10.80.80.1:1011"
          rt_export: ["65035:1030"]
          rt_import: ["65035:1011", "65035:1030"]
          interfaces: ["eth3"]
      bgp:
        vrfs:
          - name: "BLUE_HUB"
            neighbors: [{ peer: "10.80.80.2", remote_as: 65035 }]
    - name: "pe3"
      vrfs:
        - name: "BLUE_SPOKE"
          table: 200
          rd: "10.60.60.1:1011"
          rt_export: ["65035:1011"]
          rt_import: ["65035:1030"]
          interfaces: ["eth2"]
      bgp:
        vrfs:
          - name: "BLUE_SPOKE"
            neighbors: [{ peer: "10.60.60.2", remote_as: 65035 }]
    - name: "pe4"
      vrfs:
        - name: "BLUE_SPOKE"
          table: 200
          rd: "10.70.70.1:1011"
          rt_export: ["65035:1011"]
          rt_import: ["65035:1030"]
          interfaces: ["eth2"]
      bgp:
        vrfs:
          - name: "BLUE_SPOKE"
            neighbors: [{ peer: "10.70.70.2", remote_as: 65035 }]
  ce_routers:
    - name: "ce1-spoke"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.80"
          neighbors: [{ peer: "10.50.50.1", remote_as: 65001, description: "eBGP to pe1 BLUE_SPOKE VRF" }]
    - name: "ce1-hub"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.100"
          neighbors: [{ peer: "10.80.80.1", remote_as: 65001, description: "eBGP to pe2 BLUE_HUB VRF" }]
    - name: "ce2-spoke"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.90"
          neighbors: [{ peer: "10.60.60.1", remote_as: 65001, description: "eBGP to pe3 BLUE_SPOKE VRF" }]
    - name: "ce3-spoke"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.91"
          neighbors: [{ peer: "10.70.70.1", remote_as: 65001, description: "eBGP to pe4 BLUE_SPOKE VRF" }]
```

### RED VPN (Any-to-Any Mesh)

Defined in [`examples/controller/transport/default-network/red-vpn.yaml`](/examples/controller/transport/default-network/red-vpn.yaml).

```yaml
apiVersion: google.dev/v1
kind: VyOSL3VPN
metadata:
  name: l3vpn-red-service
  namespace: default
spec:
  underlayRef: l3vpn-hub-spoke-underlay
  services:
    - name: "RED_MESH"
      type: "l3vpn"
      topology: "any-to-any"
  routers:
    - name: "pe1"
      vrfs:
        - name: "RED_MESH"
          table: 300
          rd: "10.55.55.1:2000"
          rt_export: ["65035:2000"]
          rt_import: ["65035:2000"]
          interfaces: ["eth3"]
      bgp:
        vrfs:
          - name: "RED_MESH"
            neighbors: [{ peer: "10.55.55.2", remote_as: 65035 }]
    - name: "pe2"
      vrfs:
        - name: "RED_MESH"
          table: 300
          rd: "10.65.65.1:2000"
          rt_export: ["65035:2000"]
          rt_import: ["65035:2000"]
          interfaces: ["eth4"]
      bgp:
        vrfs:
          - name: "RED_MESH"
            neighbors: [{ peer: "10.65.65.2", remote_as: 65035 }]
    - name: "pe3"
      vrfs:
        - name: "RED_MESH"
          table: 300
          rd: "10.75.75.1:2000"
          rt_export: ["65035:2000"]
          rt_import: ["65035:2000"]
          interfaces: ["eth3"]
    - name: "pe4"
      vrfs:
        - name: "RED_MESH"
          table: 300
          rd: "10.85.85.1:2000"
          rt_export: ["65035:2000"]
          rt_import: ["65035:2000"]
          interfaces: ["eth3"]
  ce_routers:
    - name: "ce1-red"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.101"
          neighbors: [{ peer: "10.55.55.1", remote_as: 65001, description: "eBGP to pe1 RED_MESH VRF" }]
    - name: "ce2-red"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.102"
          neighbors: [{ peer: "10.65.65.1", remote_as: 65001, description: "eBGP to pe2 RED_MESH VRF" }]
    - name: "ce3-red"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.103"
          neighbors: [{ peer: "10.75.75.1", remote_as: 65001, description: "eBGP to pe3 RED_MESH VRF" }]
    - name: "ce4-red"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.104"
          neighbors: [{ peer: "10.85.85.1", remote_as: 65001, description: "eBGP to pe4 RED_MESH VRF" }]
```

### Logical Relationships

```
Tenant_Blue --HAS_VPN--> L3VPN_SpokeHub
L3VPN_SpokeHub --USES_VRF (pe_id=pe2)--> VRF_Blue_pe2 (BLUE_HUB)
L3VPN_SpokeHub --USES_VRF (pe_id=pe1)--> VRF_Blue_pe1 (BLUE_SPOKE)
L3VPN_SpokeHub --USES_VRF (pe_id=pe3)--> VRF_Blue_pe3 (BLUE_SPOKE)
L3VPN_SpokeHub --USES_VRF (pe_id=pe4)--> VRF_Blue_pe4 (BLUE_SPOKE)

pe2 --HOSTS_VRF--> VRF_Blue_pe2
pe2 --HAS_INTERFACE--> eth3 (ce1-hub facing)

ce1-hub --CONNECTS_TO_PE (int_ce=eth1, int_pe=eth3)--> pe2
ce1-hub --HAS_INTERFACE--> eth2 (lan-hub: 10.100.2.0/24)

Tenant_Red --HAS_VPN--> L3VPN_Mesh
L3VPN_Mesh --USES_VRF (pe_id=pe1)--> VRF_Red_pe1 (RED_MESH)
L3VPN_Mesh --USES_VRF (pe_id=pe2)--> VRF_Red_pe2 (RED_MESH)
L3VPN_Mesh --USES_VRF (pe_id=pe3)--> VRF_Red_pe3 (RED_MESH)
L3VPN_Mesh --USES_VRF (pe_id=pe4)--> VRF_Red_pe4 (RED_MESH)
```

## Security

Corporate security profile


## Application QoS

QoS profiles to prioritise traffic.


## Network Intent

Requirements on how the network should perform.

### Connectivity & Reachability:

#### Inter-Site Communication:

All designated customer sites (e.g., Headquarters, Branch Offices, Data Centers, Cloud VPCs) must be able to securely communicate with each other over the VPN.

Example: Branch offices must be able to access applications hosted in the main data center and also communicate directly with other branch offices if required (depending on topology: hub-and-spoke vs. full mesh).

#### Application Access:

Users and applications at any connected site must be able to reliably reach specific services and resources hosted at other connected sites.

Example: Employees at ce1-spoke (lan-spoke1: 10.100.1.0/24) need access to services hosted at ce1-hub (lan-hub: 10.100.2.0/24).

#### Internet Access (Optional):

Define whether sites should have direct internet access, or if all internet traffic should be backhauled through a central site (e.g., the Hub) for centralized security.

Example: All internet bound traffic from Spoke-Sites must egress via the Hub-Site's internet gateway.

### Performance & Quality of Service (QoS):

#### Bandwidth:

Sufficient bandwidth must be provisioned to support critical business applications and expected traffic volumes between sites.

Example: A minimum of 50 Mbps dedicated bandwidth is required between any spoke site and the hub site.

#### Latency:

Acceptable latency limits for time-sensitive applications.

Example: Latency for VoIP traffic between any two connected sites must not exceed 100ms round-trip time (RTT).

#### Jitter & Packet Loss:

Minimal jitter and packet loss to ensure quality for real-time applications like voice and video.

Example: Jitter for video conferencing must be below 20ms, and packet loss below 0.1%.

#### Traffic Prioritization:

Ability to prioritize critical application traffic over less critical traffic (e.g., VoIP over file transfers).

Example: VoIP traffic must receive highest priority (e.g., DSCP EF marking) across the VPN, followed by critical business applications.

### Security & Isolation:

Data Confidentiality: All data transmitted over the VPN must be encrypted to prevent unauthorized access.

Example: All traffic within the VPN must be protected using AES-256 encryption.

#### Data Integrity:

Ensure that data transmitted over the VPN has not been tampered with.

Example: Data integrity must be guaranteed using SHA-256 hashing.

#### Network Segmentation:

The customer's network traffic must be logically isolated from other customers' traffic within the service provider's infrastructure.

Example: BLUE VPN traffic must be completely separate from RED VPN traffic and from any other customer's traffic on the service provider network.

#### Access Control:

The ability to control which specific services or hosts can communicate across the VPN (e.g., using firewalls at the CE or PE edge).

Example: Only specific ports (e.g., 80, 443, 3389) should be allowed between spoke sites and the hub site for business applications.

### Reliability & Availability:

Uptime Guarantee (SLA): The VPN service must meet a specified uptime Service Level Agreement (SLA).

Example: The VPN service must have a minimum uptime of 99.9% (less than ~8.7 hours downtime per year).

#### Redundancy:

Critical sites or connections should have redundant paths or failover mechanisms to prevent single points of failure.

Example: The hub site (ce1-hub / pe2) must have redundant connections through both `p1` and `p3` for high availability. Dual route reflectors (`rr1`, `rr2`) ensure no single point of failure in the BGP control plane.

#### Disaster Recovery:

The VPN must support disaster recovery scenarios, allowing critical sites to maintain connectivity even if a primary path or data center fails.

Example: In the event of a regional outage affecting the primary data center, traffic must automatically reroute to an alternate path within 30 minutes.

### Management & Support:

Monitoring & Visibility: Ability to monitor the health, performance, and traffic of the VPN connections.

Example: We need access to a portal or reports showing bandwidth utilization, latency, and status of our VPN links.

#### Troubleshooting & Support:

Clear process for reporting issues and guaranteed response times from the service provider.

Example: Critical issues must have a 1-hour response time and 4-hour resolution time as per our support agreement.

#### Scalability:

The VPN solution must be scalable to accommodate future growth in sites, users, or bandwidth requirements.

Example: The VPN architecture must easily allow for adding new branch offices within a month's notice.


