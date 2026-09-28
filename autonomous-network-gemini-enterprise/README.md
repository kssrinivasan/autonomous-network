# Autonomous Network Lab

**Autonomous Network Lab** is a sandbox to demonstrate autonomous network closed loop automation. Leveraging Graph Neural Networks (GNNs) and AI agents to intelligently monitor, analyze, troubleshoot, and manage a multi-domain telecommunications network.

This project provides a virtual network simulator with real-time network topology understanding, automated fault detection, intelligent incident correlation and resolution capabilities.

## Architecture Philosophy: Autonomous Domains + Cross-Domain Coordination

The system is organized around **autonomous domain stacks**, each responsible for a specific layer of the telecommunications network:

- **Transport Domain**: IP/MPLS routing and VPN connectivity
- **Core Domain**: 5G core network functions and signaling
- **Radio Domain**: Radio access network (gNodeB, UEs)
- **Infrastructure Domain**: Underlying compute, networking, and test infrastructure

![gcp architecture](/docs/drawings/architecture.drawio.svg)

> This diagram is the **target** architecture, not a deployment inventory. Every domain is drawn
> with the same stack, but today only transport has an orchestrator agent, intent and topology
> servers and a GNN — core and radio have a controller and a Spanner topology database and nothing
> else, and the infrastructure domain listed above is not drawn at all. See
> [GCP Architecture](#gcp-architecture) for what is actually deployed.

**Domain Autonomy**: Each domain operates independently with its own:
- Lifecycle management and orchestration
- Monitoring and anomaly detection
- Local troubleshooting and remediation
- Domain-specific agents and tools
- Performance optimization

**Cross-Domain Coordination**: A cross-domain layer orchestrates above the autonomous domains to:
- Correlate events and anomalies across domain boundaries
- Resolve complex issues requiring multi-domain coordination
- Optimize end-to-end service delivery (e.g., UE → gNodeB → UPF → Internet)
- Tune inter-domain interfaces and dependencies
- Manage service-level objectives spanning multiple domains

This architecture enables both **local autonomy** (fast domain-specific responses) and **global intelligence** (end-to-end service assurance), mirroring real-world network operations where transport, core, and radio teams operate independently but coordinate for end-to-end service delivery.

### Functional Components

The main components of the autonomous network lab are shown below:

- [**Network Simulator**](/docs/network/Readme.md): Multi-domain virtual network simulator with autonomous domain stacks:
  - **Transport Domain**: [VyOS](https://vyos.io/) routers with BGP/OSPF underlay and L3VPN overlays
  - **Core Domain**: [free5gc](https://free5gc.org/) 5G core network functions (AMF, SMF, UPF, NRF, etc.)
  - **Radio Domain**: [UERANSIM](https://github.com/aligungr/UERANSIM) gNodeB and UE simulators
  - **Infrastructure Domain**: Linux networking, traffic testing, and VM provisioning
  - **Cross-Domain Layer**: Coordinates and tunes across autonomous domain stacks for end-to-end resolution
- [**GKE Network Automation**](/docs/automation/Readme.md): Kubernetes operator with domain-specific handlers for each autonomous stack, plus cross-domain orchestration. Each domain manages its own lifecycle via custom resource definitions (CRDs), with the cross-domain layer handling inter-domain coordination and optimization.
- [**Digital Shadow**](/docs/spanner/Readme.md): Google Cloud Spanner stores network topology graphs, temporal state and historical performance across all domains with cross-domain relationship tracking. 
- [**Network GNNs**](/docs/gnn/Readme.md): Training and serving infrastructure for graph neural network models that operate at both domain-specific and cross-domain levels to pinpoint failures and predict impact of changes. 
- [**Network Agents**](/docs/agents/Readme.md): Specialized agents for domain-specific operations (testing, log analysis) and cross-domain incident management and orchestration

## GCP Architecture

The lab GCP deployment architecture is shown in the diagram below. 

![GCP Architecture](/docs/drawings/gcp.drawio.svg)

> Two things the diagram does not yet show. The cross-domain agent fleet is drawn as the single
> **Supervisor** box — the other six agents (designer, detective, impact, chaos, tester, trust) and
> the Vertex AI Agent Runtime they share are missing. And the **Orchestrator**, **AI/ML** and
> **Intent** boxes in the Core, Radio and Service columns are target-state: they have no deployed
> counterpart. The table below is authoritative where the two disagree.

| Component | Description | Location   |
|-----------|-------------|------------|
| Dashboard | Flutter web console showing network topology along with current and historical anomalies. Talks to the supervisor Reasoning Engine directly; served on Cloud Run behind IAP. | /dashboard-v2 |
| Agents | Cross-domain agent fleet (supervisor, designer, detective, impact, chaos, tester, trust) on Vertex AI Agent Runtime, plus the transport domain orchestrator (ADK) | /agents-in-agent-runtime, /domains/transport/agents/ |
| Tools | MCP tool servers brokering agent access to network orchestration and Spanner state. `sharedmcp` serves the domain orchestrator; the `re`/`ge` twins serve the agent fleet and Gemini Enterprise; the service-domain server proxies the TMF intent and topology APIs | /services/sharedmcp, /mcp_server_for_ge, /domains/service/services/mcp |
| Config Connector | K8s based GCP infrastructure orchestration. | /gcp/configconnector.tpl.yaml |
| Orchestration Controller | Multi-domain K8s controller with autonomous domain handlers (transport, core, radio, infrastructure) each managing their own lifecycle via domain-specific CRDs, plus cross-domain graph management for inter-domain coordination.  | /controller |
| Network VM | Network simulator virtual machine hosting all domain network functions, lifecycle managed by Config Connector. | /gcp/networkvm.yaml |
| Cloud Monitoring | Domain-specific and cross-domain metrics and syslog sent to Cloud Monitoring. Eventarc and Cloud Functions process and update Spanner in near real time. | /services |
| Spanner | Current and historical network topology and state across all domains with cross-domain relationships, used to train and run inference with GNNs. | /gcp/spanner.tpl.yaml |
| Gitea | GitOps Git server for network design review. Runs on GKE behind a reserved regional address with a self-signed certificate — not Cloud Run. | /services/gitea |
| Vertex AI | Run-time for training and inferencing domain-specific and cross-domain GNNs | /docs/gnn/ |

## Running the demo

* [Installation Instructions](/INSTALL.md)

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).