# Network Automation

The lifecycle automation code that manages the multi-domain telecommunications network is provided as a GKE Kubernetes operator (`controller/`). 

The Kubernetes operator is built on the [kopf](https://kopf.readthedocs.io/en/latest/) framework. It manages the full lifecycle of virtual network functions and lab infrastructure across multiple network domains by watching Custom Resources and driving Ansible playbooks against the Network VM and Kubernetes clusters.

Resources are reconciled declaratively — create, update and delete operations are all handled automatically.

## Operator Architecture

The operator manages **multiple network domains**, each responsible for a specific layer or function of the telecommunications network:

| Domain | Purpose | CRDs | Details |
|--------|---------|------|---------|
| **Infrastructure** | Base network topology, bridges, devices, traffic testing | VyOSInfrastructure, LinuxNetwork, Device, TrafficTest | [infrastructure.md](infrastructure.md) |
| **Transport** | VyOS router provisioning and MPLS/L3VPN services | VyOSRouter, VyOSUnderlay, VyOSL3VPN | [transport.md](transport.md) |
| **Core** | 5G core network functions (free5gc) | FiveGCore, NetworkFunction | [core.md](core.md) |
| **Radio** | RAN gNodeB and UE simulation (UERANSIM) | CellSite | [radio.md](radio.md) |
| **Supporting** | VM provisioning, fault injection | VyOSVM, NetworkFailure | [supporting.md](supporting.md) |
| **Observability** | Graph topology and monitoring | Graph sync, OpsAgent config | [supporting.md](supporting.md#graph--observability) |

Each domain has its own:
- **CRD definitions** in `controller/config/<domain>/`
- **Lifecycle handlers** in `controller/src/<domain>/lifecycle.py`
- **Ansible playbooks** in `controller/src/<domain>/playbooks/` (where needed)
- **Dedicated reconciliation logic** registered as separate kopf handler domains

This multi-domain architecture enables:
- **Parallel reconciliation** across domains
- **Clean separation of concerns** (transport vs core vs radio)
- **Independent scaling** of domain-specific handlers
- **Modular development** and testing

## Operator Logs

To view operator logs:

```bash
kubectl get pods
# NAME                              READY   STATUS    RESTARTS   AGE
# vyosnetwork-operator-<UUID>        1/1     Running   0          9m22s

kubectl logs -f vyosnetwork-operator-<UUID>
```

## Cross-Domain Dependency Chain

```
VyOSVM (automation host)
  └── VyOSInfrastructure (Infrastructure domain)
        ├── LinuxNetwork (generated)
        └── VyOSRouter (generated) → Transport domain
              └── VyOSUnderlay → Transport domain
                    └── VyOSL3VPN → Transport domain
                          ├── Device (Infrastructure domain)
                          │     └── TrafficTest (Infrastructure domain)
                          ├── FiveGCore (Core domain)
                          │     └── NetworkFunction (Core domain)
                          └── CellSite (Radio domain)
```

The operator enforces these dependencies automatically. Resources that depend on a parent will wait (retrying every 10–30 seconds) until the parent reaches `Ready` state before proceeding.

## Quick Reference by Domain

### Infrastructure Domain
**Base network topology, Linux bridges, test devices, and traffic testing**

| CRD | Purpose | kubectl shortname |
|-----|---------|-------------------|
| VyOSInfrastructure | Declares full network topology; generates child VyOSRouter and LinuxNetwork resources | `infra` |
| LinuxNetwork | Linux bridge / Docker network segment | `lnet` |
| Device | Simulated end-user device or CPE container | `dev` |
| TrafficTest | Traffic test using traffic-agent binary | `tt` |

[→ Infrastructure Domain Details](infrastructure.md)

### Transport Domain
**VyOS router provisioning, MPLS underlay, and L3VPN services**

| CRD | Purpose | kubectl shortname |
|-----|---------|-------------------|
| VyOSRouter | Single VyOS router container with full protocol config | `vr` |
| VyOSUnderlay | Underlay routing protocols (OSPF, BGP, MPLS/LDP) | `underlay` |
| VyOSL3VPN | Layer 3 VPN (VRF) services on PE routers | `l3vpn` |

[→ Transport Domain Details](transport.md)

### Core Domain
**5G core network functions (free5gc)**

| CRD | Purpose | kubectl shortname |
|-----|---------|-------------------|
| FiveGCore | Orchestrates full 5G core network (AMF, SMF, UPF, NRF, etc.) | `core` |
| NetworkFunction | Individual 5G network function (AMF, SMF, UPF, NRF, NSSF, etc.) | `nf` |

[→ Core Domain Details](core.md)

### Radio Domain
**RAN simulation (UERANSIM gNodeB + UEs)**

| CRD | Purpose | kubectl shortname |
|-----|---------|-------------------|
| CellSite | UERANSIM gNodeB + UE simulation | `cellsite` |

[→ Radio Domain Details](radio.md)

### Supporting Resources
**VM provisioning, fault injection, and observability**

| CRD | Purpose | kubectl shortname |
|-----|---------|-------------------|
| VyOSVM | GCP VM for network automation host | `vyosvm` |
| NetworkFailure | Fault injection for chaos testing | `nf` (collision) |

Gitea is **not** a CRD. It is a plain GKE Deployment installed by `services/gitea/install.sh`;
see [supporting.md](supporting.md#gitea) and [services/gitea/Readme.md](../../services/gitea/Readme.md).

[→ Supporting Resources Details](supporting.md)


## Common Operations

```bash
# List all resources across all domains
kubectl get infra,underlay,l3vpn,vr,lnet,dev,tt,core,nf,cellsite

# List resources by domain
kubectl get infra,lnet,dev,tt           # Infrastructure domain
kubectl get vr,underlay,l3vpn           # Transport domain
kubectl get core,nf                     # Core domain (5G)
kubectl get cellsite                    # Radio domain

# Watch traffic test progress
kubectl get tt -w

# Describe a failing resource for error details
kubectl describe vr p1
kubectl describe infra l3vpn-hub-spoke-infra
kubectl describe core free5gc-1
kubectl describe cellsite site-1

# Delete a full topology (child resources are garbage-collected)
kubectl delete infra l3vpn-hub-spoke-infra

# Deploy the full L3VPN hub-spoke lab topology
kubectl apply -f examples/controller/transport/default-network/

# Deploy 5G core + RAN
kubectl apply -f examples/controller/multidomain/default/
```

## Development & Testing

See individual domain documentation for:
- **Domain-specific testing** procedures
- **Ansible playbook** development
- **CRD schema** evolution
- **Integration testing** with other domains
