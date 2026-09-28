# GNNs for Network Intelligence

Graph Neural Networks (GNNs) model a network as a graph where routers and interfaces are nodes and physical or logical connections are edges. By learning from historical telemetry, configuration state, and protocol events stored in the network's digital twin ([Spanner](/docs/spanner/Readme.md)), GNNs produce per-device embeddings — compact numerical representations of each node's health and behaviour. 

Two complementary use cases are addressed here. 

* **Failure pinpointing** uses GNNs to detect anomalies in live or historical embeddings, isolate which layer of the stack (hardware, configuration, or protocol) is the root cause, and attribute the anomaly to a specific named feature. 
* **What-if simulation** uses GNNs to predict the downstream impact of a proposed change — topology modification, configuration update, traffic surge, or maintenance action — before it is applied, giving operators a quantitative answer to "what would happen if…?" without touching the production network.

---

## Failure Pinpointing

Failure pinpointing uses GNN-generated embeddings to detect when a device or interface has drifted from its learned healthy baseline and to explain precisely what caused the deviation. Three complementary GNN architectures address the different failure modes a network exhibits.

### Spatio-Temporal GNNs (STGNNs)

STGNNs combine a spatial graph layer (GCN or GAT) that aggregates information across the network topology with a temporal sequence model (LSTM, GRU, or Transformer) that tracks how each node's state evolves over time. At each time step the spatial layer produces an embedding per node; the full sequence of embeddings is then fed into the temporal backbone, which learns a "trajectory of health" rather than a static snapshot. 

This makes STGNNs the right tool for slow-developing or transient faults that are invisible to threshold-based monitoring: microburst congestion (detectable within a fast 5-minute window), control plane overload from route churn, and gradual hardware degradation such as a failing optical transceiver accumulating CRC errors over hours. Because the model learns the *rate of change* and *acceleration* of key counters, it can extrapolate a degradation trend and raise an alert — with an estimated time-to-failure — well before a link actually goes down. The STGNN is trained self-supervised by predicting the next network state from recent history; an anomaly is signalled when the model's prediction error on live data exceeds the healthy baseline.

### Directed Graph Attention Networks (D-GATs)

Standard GNNs treat edges as symmetric, but routing is inherently directional: traffic from router A to router B follows different paths and policies than the reverse. D-GATs apply asymmetric attention mechanisms so that the message from A→B uses a different mathematical transformation than B→A. 

This makes D-GATs the natural fit for detecting asymmetric routing issues and silent blackholing — scenarios where OSPF and BGP sessions remain fully established while large packets are silently dropped in one direction due to an MTU mismatch, a corrupted forwarding table, or a misconfigured ACL. The D-GAT embedding captures "directional flow consistency": when a device is blackholing traffic, its embedding isolates a high incoming attention weight paired with zero functional outgoing flow, producing a distinct "sinkhole" signature in latent space. 

The model is trained as a graph autoencoder, learning to reconstruct directed edge features from node embeddings; edges it cannot reconstruct — particularly those with high drop asymmetry or traffic asymmetry — are flagged as anomalous.

### Heterogeneous GNNs (HetGNNs)

Real networks contain structurally different entity types — physical routers, logical VRFs, BGP sessions, interfaces, subnets — that update at different rates and carry different semantics. HetGNNs apply typed message passing: a message travelling over a `Physical_Link` edge is processed differently from one over a `BGP_Peering` edge, and a `Router_Config` node is encoded separately from an `Interface_Metrics` node. 

This decomposition produces branch-specific sub-embeddings (config, protocol, metrics) that can be inspected independently at inference time. When an anomaly is detected, the branch whose reconstruction error is highest directly identifies the fault layer: a config branch spike points to a fat-fingered route-map or RT misconfiguration; a protocol branch spike points to a BGP or OSPF control-plane failure; a metrics branch spike points to a hardware or traffic-level data-plane problem. 

HetGNNs are particularly well-suited to configuration drift and to separating control-plane from data-plane failures. Integrated Gradients attribution over the highest-error branch then identifies the specific named feature (e.g., `vrf_rt_import`, `mtu_normalized`) responsible, translating an abstract embedding anomaly into an actionable root cause explanation.

---

## What-If Simulation

What-if simulation uses GNNs to predict the impact of a proposed network change before it is applied. Rather than detecting anomalies in current state, these models take a modified graph — a topology with a link removed, a config with an updated route policy, or a traffic matrix with a doubled load — and produce quantitative forecasts of service KPIs (reachability, latency, packet loss, throughput) under the hypothetical scenario.

### Message Passing Neural Networks (MPNNs)

MPNNs model impact propagation the same way routing protocols do: information flows hop by hop through the graph, and multiple message-passing rounds extend the model's reach to distant nodes. 

For topology what-if queries (link removal, node failure, BGP peering changes) the proposed change is encoded directly into the graph before inference — a link is deleted, a node is disabled — and the subsequent message rounds propagate the impact outward. MPNNs are preferred over GCN when the change's effect must travel several hops, for example an RT misconfiguration on one PE that silently isolates a CE router two or three hops away. The model is trained on historical bitemporal data from Spanner: every recorded link-state event becomes a `(graph_before, KPI_after)` training pair, teaching the model which downstream nodes and services are affected when a given structural change occurs.

### Graph Attention Networks (GATs)

GATs extend basic message passing by learning per-neighbor attention weights, allowing the model to focus on the neighbors that matter most to a given node's predicted KPI. For topology what-if queries, when a link is removed its attention mass redistributes to surviving paths, and the model predicts how much additional load each alternate path absorbs — making GAT particularly useful for capacity planning and redundancy assessment. The learned attention weights are also interpretable: a network engineer can inspect them to understand *why* the model predicts a given impact on a specific downstream device, not just *what* the impact will be.

### Graph Convolutional Networks (GCNs)

GCNs encode topology by averaging each node's features with those of its neighbors across multiple layers, extending the receptive field by one hop per layer. They are computationally efficient and well-suited to two what-if problem types. For **topology changes**, the adjacency matrix is modified before the forward pass (remove a row/column to simulate node failure, zero an entry to remove a link) and the model predicts service reachability and latency. For **configuration changes**, the graph structure is held fixed and the change is introduced as a delta in the node feature vector; the convolution then propagates that delta through the graph to predict which downstream nodes are impacted and how far the effect travels.

### Causal Inference with Neural Networks

Standard regression models learn correlations, not causes. In live networks, config changes frequently happen alongside pre-existing conditions (high load, maintenance windows) that confound a simple before/after comparison. Causal inference networks (TARNet, DragonNet) explicitly separate the *treatment* (the config change) from *confounders* (the network state at the time of the change), producing a counterfactual prediction: what would the KPI have been if the change had *not* been made? The difference between the factual and counterfactual predictions is the isolated causal effect of the change — a far more reliable answer to "will this change degrade the service?" than naive correlation.

### Temporal Graph Networks (TGNs)

TGNs maintain a persistent, continuously-updated memory state per node, processing the network as a live stream of metric events rather than periodic snapshots. This makes TGN the most accurate model for traffic dynamics: when a link is already at 60% utilisation and trending upward, TGN factors in that trajectory when predicting whether a doubled load would cause congestion, rather than reasoning from a static baseline. For traffic surge, rerouting, and new service provisioning what-ifs, TGN projects forward from the current *evolving* network state, capturing queue build-up, burst correlations, and temporal dependencies between traffic spikes and eventual packet drops in real time.

### LSTMs and Transformers for Traffic Forecasting

For traffic load what-if queries, per-link LSTMs and network-wide Transformers offer complementary strengths. An **LSTM** processes each link's telemetry as an ordered time series, maintaining a rolling memory of diurnal cycles, business-hours peaks, and weekend valleys; for a surge what-if, it forecasts whether additional load tips a link past its saturation threshold given its current utilisation trend. A **Transformer** attends across both time steps and nodes simultaneously in a joint `[T, N, F]` tensor, directly learning cross-router patterns such as "congestion on P1→P3 now predicts congestion on P2→P4 five minutes later" — cross-node temporal reasoning that per-link LSTMs cannot capture and that makes Transformers the strongest model for network-wide load impact assessment.

### Multi-Task Learning

Rather than training separate models for latency, packet loss, throughput, and reachability, multi-task learning forces a single GNN backbone to produce all four predictions simultaneously through a shared representation. The shared bottleneck compels the model to learn the underlying drivers of service quality (link utilisation, path length, queue depth) rather than overfitting to correlates of a single KPI. The four task-specific heads branch off the shared embedding and are trained jointly with a weighted loss; for new service provisioning, all four impact forecasts are produced in a single forward pass, and the shared backbone generalises better to novel traffic profiles because the KPIs constrain each other during training.

### Graph-Based Reinforcement Learning

Rolling upgrade sequencing is a combinatorial optimisation problem — for N routers there are N! possible orderings — that cannot be solved by regression alone. A graph-based RL agent operates over GNN embeddings of the current network state, selecting which router to upgrade next at each step and receiving a reward signal derived from cumulative service impact (latency delta, drop rate, reachability) across the entire maintenance window. By operating in the learned latent space rather than on raw features, the policy generalises across structurally similar network states and learns to sequence upgrades in the order that minimises disruption — skills that transfer to novel topologies not seen during training.

### Variational Autoencoders (VAEs)

VAEs learn a compressed, probabilistic representation of complete network state snapshots. The latent space is smooth and continuous by design: small network changes produce small movements in latent space, and large structural changes produce large displacements. This geometric property makes the L2 distance between two latent vectors a reliable and interpretable measure of "how different are these two network states?" — directly applicable to rollback assessment, where the question is whether the restored state has returned to the same neighbourhood in latent space as the pre-change baseline. A threshold on this distance, learned from historical rollback events, determines whether a rollback is complete.

### Transfer Learning

Production networks accumulate limited labeled history for rare events such as planned outages or rolling upgrades. Transfer learning solves this by first pre-training a GNN backbone on abundant synthetic data generated by fault injection in the VyOS lab topology, where diverse failure modes and maintenance sequences can be injected freely. The pre-trained backbone learns general representations of network health — how BGP reconvergence manifests in embeddings, how config changes propagate, what healthy utilisation looks like — that transfer directly to the production environment. Fine-tuning on a small set of real production events (as few as 30 labeled maintenance windows) then adapts the task-specific prediction heads to the production topology's characteristics, requiring far less data than training from scratch.

---

## Further Reading

| Document | Contents |
| :--- | :--- |
| [Failure Pinpointing Research](./failure_pinpointing_research.md) | Full architecture details, input features, training procedures, and VyOS lab fault scenarios for STGNN, D-GAT, and HetGNN |
| [What-If Simulation Research](./whatif-research.md) | Full architecture details, input features, training procedures, and scenario walkthroughs for MPNN, GAT, GCN, TGN, LSTM, Transformer, Multi-Task, RL, VAE, and Transfer Learning |
| [HetGNN RCA over Spanner](./hetgnn_rca_spanner.md) | Detailed mapping of the Spanner `networkGraph` schema to HetGNN node/edge types and the three-step RCA workflow |
| [Embedding Integration](./embedding.md) | How GNN embeddings are generated, stored in the `NodeEmbedding` Spanner table, and queried via GQL |
