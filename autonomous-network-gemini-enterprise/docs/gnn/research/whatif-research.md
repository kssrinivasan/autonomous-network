# Digital Twin Research

The document describes how to build a **Digital Twin** of a network using Graph Neural Networks (GNNs) to predict the impact to the network making specific changes.

Captured device state includes configuration, status and metrics, interface features include performance metrics. 

This document is a place to collect research on techniques that can implement predictive impact assessment on a network. 

This document uses a VyOS network as a case study. A number of VyOS routers are connected to deliver a L3 VPN service.


# Typical problems to solve

The digital twin must answer predictive questions about the impact of network changes before they are applied. Typical what-if scenarios include:

### Topology Changes
- **Link removal / failure** — What happens to service reachability, traffic load, and latency if a specific link goes down?
- **Node removal / failure** — Which services are affected if a router becomes unreachable?
- **Link addition** — How does adding a new link change traffic distribution and redundancy?
- **Peering changes** — What is the routing impact of adding or removing a BGP peer?

### Configuration Changes
- **Routing policy modification** — How does changing a route-map or prefix-list affect traffic paths across the network?
- **MPLS/VPN label changes** — What is the reachability impact of modifying VRF or label assignments on a L3VPN?
- **QoS policy changes** — How do bandwidth or priority changes on one interface ripple through end-to-end service performance?
- **Firewall / ACL rule changes** — Which traffic flows are blocked or permitted after a policy update?
- **Interface parameter changes** — What is the effect of changing MTU, speed, or duplex on a link?

### Traffic and Load Changes
- **Traffic surge** — If demand on a service doubles, which links become congested and what is the impact on latency/loss?
- **Traffic rerouting** — If a primary path is removed, which alternate paths absorb traffic and do they have capacity?
- **New service provisioning** — What is the impact of adding a new VPN customer with a given traffic profile?

### Maintenance and Upgrade Scenarios
- **Rolling upgrade** — In what order should routers be upgraded to minimize service disruption?
- **Planned outage window** — What pre-changes (e.g., traffic draining) are needed before taking a node out of service?
- **Rollback assessment** — If a change is reverted, does the network return to its previous stable state?

## Relevant Deep Learning Techniques

The feature encoding and training descriptions below assume the same data model as the failure pinpointing document: nodes are `PhysicalRouter`, `PhysicalInterface`, `VRF`, and `BGPSession` entities; edges are `PhysicalLink`, `HasInterface`, `BelongsToVRF`, and `PeersWith` relationships; time-series data comes from `NetworkMetrics`; and configuration state comes from the `PhysicalRouter.config` JSON blob, as extracted by the VyOS feature pipeline.

---

### Topology Changes
*Problems: link/node failure, link addition, peering changes*

#### Message Passing Neural Networks (MPNN)

**Why it fits / how it works**

MPNNs model the network as a message-passing graph: each router iteratively receives information from its directly connected neighbors, aggregates it, and updates its own state representation. Multiple rounds of passing propagate information further across the topology — exactly as routing protocols do in reality. This makes MPNNs a natural fit for topology what-if questions: removing or adding a link is simply a structural change to the graph before inference, and the subsequent message rounds propagate the impact outward hop-by-hop. The model learns from historical link-state events in the bitemporal Spanner history which downstream nodes and services are affected when a given link disappears, and generalises that learning to novel scenarios at inference time.

**Input feature encoding**

The what-if change is encoded directly into the graph structure before inference. To simulate removing a link, the `PhysicalLink` edge between two `PhysicalInterface` nodes is deleted from the graph (or its edge feature vector is zeroed). To simulate adding a link, a new edge is inserted with synthetic features derived from the interface specs (`PhysicalInterface.speed`, `PhysicalInterface.media_type`).

Node feature vector per `PhysicalRouter`:

| Feature | Source | Encoding |
| :--- | :--- | :--- |
| Role | `PhysicalRouter.role` | One-hot (P / PE / CE / RR) |
| Status | `PhysicalRouter.status` | Binary (1 = UP) |
| CPU utilization | `NetworkMetrics` (latest) | Normalize to [0, 1] |
| Memory utilization | `NetworkMetrics` (latest) | Normalize to [0, 1] |
| Loopback reachable | Derived from OSPF adjacency state | Binary |

Edge feature vector per `PhysicalLink`:

| Feature | Source | Encoding |
| :--- | :--- | :--- |
| Bandwidth | `PhysicalLink.bandwidth` | Normalize (value / max_bw) |
| Status | `PhysicalLink.status` | Binary (1 = UP) |
| Current utilization | `NetworkMetrics` tx_bytes rate | Normalize to [0, 1] |
| MTU | `PhysicalInterface.mtu` (both ends) | Normalize (value / 9000), mismatch flag |

**Training method and loss**

The model is trained to predict a target KPI vector for each node after the topology change is applied. The training set is built from the bitemporal history in Spanner: for each historical `valid_start_ts` event where a link status changed, the pre-change graph and the post-change KPI measurements (latency, reachability, VRF prefix counts) form a `(graph_before, KPI_after)` training pair.

```
Loss = MSE(predicted_KPI_vector, observed_KPI_vector)
     = (1 / N×K) × Σ_nodes Σ_KPIs ( predicted - observed )²
```

where K = number of KPI targets (e.g., latency, packet loss, reachable prefix count per VRF). A minimum of 200 historical link-state change events is recommended to cover diverse failure modes.

---

#### Graph Attention Networks (GAT)

**Why it fits / how it works**

GATs extend basic message passing by learning *how much attention* to pay to each neighbor rather than treating all neighbors equally. In a network, not all neighbors are equally important: a PE router connected to a Route Reflector and three P routers should weight a failing core link differently from a healthy CE-facing link. For topology what-if queries this is particularly valuable: when a link is removed, the attention mass that was assigned to it redistributes across the remaining paths, and the model predicts how much additional load each alternate path absorbs. The attention weights are also interpretable — a network engineer can inspect them to understand *why* the model predicted a given impact on a specific downstream device.

**Input feature encoding**

Same node and edge features as MPNN above. GAT additionally benefits from structural features that help the attention mechanism learn which neighbors are critical to a node's state:

| Feature | Source | Encoding |
| :--- | :--- | :--- |
| Neighbor degree | Graph topology | Normalize (degree / max_degree) |
| Shared VRF membership | `VRF.LocatedOn` edges | Binary per VRF ID |
| BGP session count | `BGPSession.BelongsToVRF` | Normalize (count / max_sessions) |

For a link-failure what-if, the attention weight learned by the model for that edge drops to zero (the edge is removed), allowing the model to propagate the adjusted attention mass to alternate paths and predict their resulting load.

**Training method and loss**

Same supervised training setup as MPNN. An additional auxiliary loss encourages interpretability: the learned attention weights for edges in the training set should correlate with actual traffic volume — edges carrying more traffic should receive higher attention from their endpoint nodes.

```
Loss_total = Loss_KPI + λ × Loss_attention_alignment
Loss_attention_alignment = MSE(attention_weight(e), normalize(tx_bytes(e)))
λ = 0.1
```

This regularisation produces attention maps that a network engineer can inspect to understand *why* the model predicted a given impact.

---

#### Graph Convolutional Networks (GCN)

**Why it fits / how it works**

GCNs encode the full topology by convolving each node's features with a weighted average of its neighbors' features across multiple layers. Each additional layer extends the receptive field by one more hop, so a 3-layer GCN incorporates information from up to 3 hops away. GCN is computationally efficient and well-suited to reachability prediction: two nodes that are structurally similar in the graph (same role, similar connectivity) produce similar embeddings, and modifying the adjacency matrix directly shifts those embeddings to reflect the new topology. It is the right starting point when the primary question is "which services lose reachability?" — a structural question that depends on graph connectivity more than on weighted neighbor importance.

**Input feature encoding**

GCN aggregates neighbor features uniformly (no attention), so the node feature vector must be richer to compensate. Concatenate config, protocol state, and metric features into a single vector per node using the same encoding pipeline as failure_pinpointing_research.md:

```
node_vector = [ config_features | protocol_features | metric_features ]
```

The adjacency matrix A is built from `PhysicalLink` edges. For a what-if simulation, A is modified before the forward pass: set `A[i][j] = 0` to remove a link, or insert a row/column to add a node.

**Training method and loss**

Trained as a graph-level regression: given the modified adjacency matrix and current node features, predict service-level reachability (binary per L3VPN service) and path latency.

```
Loss = BCE(predicted_reachability, observed_reachability)
     + MSE(predicted_latency, observed_latency)
```

Binary Cross-Entropy (BCE) is used for the reachability output (service is either reachable or not); MSE is used for latency. Use class weights in the BCE term to account for the fact that most services remain reachable most of the time — rare outage events should have higher weight.

---

### Configuration Changes
*Problems: routing policy, MPLS/VPN labels, QoS, ACLs, interface parameters*

#### Graph Convolutional Networks (GCN) — config propagation

**Why it fits / how it works**

For configuration changes, GCN is used differently from the topology variant: the graph structure (adjacency matrix) stays fixed, and the change is introduced through the *node feature vector* as a delta from the pre-change to the post-change config. The convolution then propagates that delta layer by layer through the graph, predicting how far the change's effect travels and which downstream nodes are impacted. This works well for localised changes — MTU, ACL rules, QoS policies — whose effect is bounded to a small number of hops and can be captured within GCN's fixed receptive field.

**Input feature encoding**

Config changes are encoded as the *delta* between the pre-change and post-change config feature vectors. The `PhysicalRouter.config` JSON blob is parsed using the VyOS extraction pipeline and diffed:

| Feature | Source | Encoding |
| :--- | :--- | :--- |
| MTU change | `interfaces.ethernet.ethX.mtu` | delta normalized (new − old) / 9000 |
| Route-map added/removed | `policy.route-map.X` | Binary delta per known policy |
| VRF RD change | `vrf.name.X.protocols.bgp.rd` | Hash delta — learned embedding of (old_hash XOR new_hash) |
| VRF RT import/export change | `vrf.name.X.protocols.bgp.route-target` | Binary change flag per RT value |
| BGP AS change | `protocols.bgp.local-as` | Normalize delta |

The delta vector is appended to the existing node feature vector. This gives the model both the current state and the nature of the change.

**Training method and loss**

Training pairs come from bitemporal history: config change events at `valid_start_ts` paired with KPI observations at `valid_start_ts + Δt` (typically 5–15 minutes post-change, once the network has converged). Loss is MSE on the predicted KPI change relative to baseline.

---

#### Message Passing Neural Networks (MPNN) — config propagation

**Why it fits / how it works**

When a configuration change has deep, multi-hop effects, MPNN is preferable to GCN because the number of message-passing rounds can be explicitly set to match the expected propagation depth. An RT misconfiguration on PE3 silently isolates CE2 two hops away; a route-map change on a PE can affect prefix visibility on a CE three hops away through a Hub VRF. MPNN encodes the config delta as edge and node features, then propagates the signal outward for as many rounds as the change requires to reach affected services. It also naturally handles the asymmetry of config propagation — the effect of a change on one device flows in a specific direction through the graph, which MPNN captures via per-edge message functions.

**Input feature encoding**

Config changes are encoded as edge features on the `PhysicalLink` connecting the modified device to its neighbors, capturing how the change propagates. For example, an MTU change on PE1's `eth1` becomes an edge feature on the PE1↔P1 link: `mtu_mismatch = |mtu_PE1 - mtu_P1| / 9000`. A routing policy change becomes a node feature delta that propagates outward through message rounds to predict which downstream nodes see a changed route set.

**Training method and loss**

Same as GCN config variant above. MPNN is preferred when the propagation depth matters (e.g., an RT change on PE3 affects CE2 two hops away) because MPNN can be configured with more message-passing rounds to reach further neighbors.

---

#### Causal Inference with Neural Networks

**Why it fits / how it works**

Standard regression models learn correlations, not causes. In a live network, config changes often happen during maintenance windows or high-traffic periods, so a model that simply correlates "change happened → KPI degraded" will conflate the change's effect with pre-existing conditions. Causal inference explicitly separates the *treatment* (the config change) from *confounders* (the state of the network at the time of the change), producing a counterfactual estimate: "what would the KPI have been if the change had *not* been made?" The difference between the two predictions is the isolated causal effect of the change — a much more reliable answer to "will this change degrade the service?" than a simple before/after comparison.

**Input feature encoding**

This technique frames each config change as an observational study. Three tensors are required per training example:

| Tensor | Contents | Source | Encoding |
| :--- | :--- | :--- | :--- |
| **Treatment** T | The config change applied | `valid_start_ts` diff of `PhysicalRouter.config` | Binary vector (1 per changed field) |
| **Confounders** X | Network state at the time of the change | All node features at `valid_start_ts − 5m` | Same normalization pipeline as above |
| **Outcome** Y | KPI measured after convergence | `NetworkMetrics` at `valid_start_ts + 15m` | Log-scaled rates, normalized latency |

**Training method and loss**

A two-headed neural network (e.g., a TARNet or DragonNet) is trained. One head predicts the outcome under treatment (change applied); the other predicts the outcome under no treatment (counterfactual baseline). The causal effect is the difference between the two heads' outputs.

```
Loss = MSE(Y_head_treated, Y_observed | T=1)
     + MSE(Y_head_control, Y_observed | T=0)
     + α × IPM(treated_embeddings, control_embeddings)
```

The third term (Integral Probability Metric) minimises the distribution shift between treated and control populations in the learned representation, reducing confounding bias. α = 0.5 is a reasonable starting value. Training requires a balanced mix of historical snapshots where the config change was and was not applied.

---

### Traffic and Load Changes
*Problems: traffic surge, rerouting, new service provisioning*

#### Temporal Graph Networks (TGN)

**Why it fits / how it works**

TGN maintains a persistent memory state per node that is updated continuously as new metric events arrive, rather than working from periodic static snapshots. This makes it the most accurate model for traffic dynamics: it sees the network as a live stream of `NetworkMetrics` updates, capturing the temporal dependencies between traffic bursts, queue build-up, and eventual packet drops in real time. For traffic surge what-if queries, TGN can project forward from the *current evolving state* of the network rather than from a fixed baseline. If a link is already at 60% utilisation and rising, TGN factors in that trajectory when predicting whether a doubled load would cause congestion — something a snapshot model would miss.

**Input feature encoding**

TGN processes a stream of timestamped graph events rather than static snapshots. Events are sourced from `NetworkMetrics` records, keyed by `node_name`, `metric_name`, `timestamp`:

| Event type | Trigger | Feature vector |
| :--- | :--- | :--- |
| Metric update | New `NetworkMetrics` row | `[tx_bytes_rate, rx_bytes_rate, tx_drops_rate, rx_drops_rate, utilization]` — all log-scaled |
| Link state change | `PhysicalLink.status` update | `[link_bw, new_status, delta_t_since_last_change]` |
| BGP prefix change | `bgp_pfx_rcvd` delta | `[prefix_delta, normalized_count, bgp_uptime_log]` |

For a traffic surge what-if, the `tx_bytes_rate` feature on the affected service's ingress links is scaled up by the hypothetical multiplier (e.g., ×2) before inference.

**Training method and loss**

TGN is trained self-supervised by predicting future edge events: given the event history up to time T, predict which edges will have metric updates at T+1 and what their values will be.

```
Loss = MSE(predicted_metric_vector(e, T+1), observed_metric_vector(e, T+1))
```

averaged over all edges with updates in the prediction window. The model learns the normal relationship between traffic load and link utilization; at inference time, scaled-up input features expose which links would breach capacity thresholds.

---

#### Long Short-Term Memory (LSTM)

**Why it fits / how it works**

LSTMs process telemetry as ordered sequences, maintaining a hidden state that functions as a rolling memory of recent link behaviour. They excel at capturing recurring patterns — diurnal traffic cycles, business-hours spikes, weekend valleys — because their internal gating mechanism learns which past time steps are worth retaining and which can be forgotten. For a traffic surge what-if, the LSTM's memory of recent utilisation trends is used to forecast whether the additional load would tip a link into saturation. A link sitting at 85% utilisation after a peak hour requires a different prediction than the same link at 85% and trending down; the LSTM captures that distinction naturally from the sequence.

**Input feature encoding**

Per-link time-series tensor shaped `[T, F]` where T = time steps and F = features. One LSTM is trained per link (or a single LSTM with link ID as an additional feature). Input features are extracted from `NetworkMetrics` at 1-minute intervals:

| Feature | Metric name | Encoding |
| :--- | :--- | :--- |
| TX utilization | `tx_bytes` | rate / link_bandwidth → [0, 1] |
| RX utilization | `rx_bytes` | rate / link_bandwidth → [0, 1] |
| Drop rate | `tx_drops` | log-scaled rate |
| Queue depth proxy | `tx_drops` acceleration | second derivative of log-scaled rate |
| Time of day | Timestamp | sin/cos encoding of hour-of-day to capture diurnal patterns |

For a surge what-if, the utilization input is multiplied by the surge factor at the query time step; the LSTM then unrolls forward to forecast whether utilization exceeds 1.0 (100% capacity).

**Training method and loss**

Trained as a sequence-to-scalar regression: given the last T=60 minutes of link utilization, predict the peak utilization over the next 15 minutes.

```
Loss = MSE(predicted_peak_utilization, observed_peak_utilization)
     + λ × max(0, predicted_peak − 0.9)²   (penalty for under-predicting near-saturation)
```

The second term is an asymmetric penalty that makes the model conservative near the 90% utilization threshold, reducing false-safe predictions that could allow a change causing congestion to be approved.

---

#### Transformer / Temporal Attention

**Why it fits / how it works**

Transformers use self-attention to directly compare any two time steps in the input sequence, regardless of how far apart they are — unlike LSTMs, which process steps one-by-one and can struggle with very long-range dependencies. In a multi-router network, this matters: a traffic spike on P1→P3 two hours ago may still be relevant to predicting the impact of a new surge today, and the Transformer can attend back to it directly. More importantly, the joint `[T, N, F]` input tensor lets the model attend *across nodes at the same time step*, learning cross-router patterns like "congestion on P1→P3 at T=0 predicts congestion on P2→P4 at T+5min." This cross-node temporal reasoning — simultaneously attending to multiple routers across multiple time steps — is not possible with per-link LSTMs and makes the Transformer the strongest model for network-wide load impact assessment.

**Input feature encoding**

Multi-variate time-series tensor shaped `[T, N, F]` covering all nodes simultaneously, enabling the Transformer to attend across both time and topology. Features per node per time step:

| Feature | Source | Encoding |
| :--- | :--- | :--- |
| TX/RX bytes rate | `NetworkMetrics` | Log-scaled, normalized to link capacity |
| Packet drops | `NetworkMetrics` | Log-scaled rate |
| Queue depth | `NetworkMetrics` (if available) or inferred from drop acceleration | Log-scaled |
| BGP prefix count | `NetworkMetrics` (`bgp_pfx_rcvd`) | Ratio to baseline |
| OSPF adjacency count | Derived from `BGPSession` / OSPF state | Normalize to max expected |

Node position in the graph is embedded using a learned positional embedding keyed by `PhysicalRouter` ID, allowing the Transformer to learn topology-aware attention patterns.

**Training method and loss**

Trained as masked time-step prediction (analogous to BERT but for time-series): randomly mask 15% of time steps across the input tensor and train the model to reconstruct the masked values.

```
Loss = (1 / |masked|) × Σ_masked_positions MSE(predicted_value, actual_value)
```

At inference, the query is: "Given the current T steps of telemetry, what would the next K steps look like if TX utilization on link X were doubled?" The masked positions are set to the counterfactual values and the model fills in the downstream effects.

---

#### Multi-Task Learning

**Why it fits / how it works**

Training separate models for latency, packet loss, throughput, and reachability wastes data and risks producing inconsistent predictions — a model predicting high throughput while another predicts high packet loss for the same scenario. Multi-task learning forces a single GNN backbone to simultaneously explain all four outputs through a shared representation. This shared bottleneck compels the model to learn the underlying drivers of service quality (link utilisation, path length, queue depth) rather than overfitting to correlates of any single KPI. For new service provisioning, all four impact predictions are produced in a single forward pass, and the shared backbone generalises better to novel traffic profiles because the KPIs constrain each other during training.

**Input feature encoding**

Shared input is the same full node feature vector used by GCN/MPNN above — config, protocol state, and current metrics concatenated. A single GNN backbone (e.g., 3-layer GCN) produces per-node embeddings. Multiple task-specific heads then branch off the shared embedding:

| Head | Target | Loss type |
| :--- | :--- | :--- |
| Latency head | End-to-end path latency per L3VPN service | MSE |
| Loss head | Packet loss rate per service | MSE |
| Throughput head | Achievable throughput per service | MSE |
| Reachability head | Binary service reachability | Binary Cross-Entropy |

For a new service provisioning what-if, the new VPN's expected traffic profile is added as a feature to the relevant PE and CE nodes before inference; all four heads then simultaneously output predictions.

**Training method and loss**

```
Loss_total = w1 × MSE(latency) + w2 × MSE(loss_rate) + w3 × MSE(throughput) + w4 × BCE(reachability)
```

Starting weights: w1=0.25, w2=0.25, w3=0.25, w4=0.25. Can be tuned via gradient uncertainty weighting (GradNorm) if one head dominates the gradient updates. Training data is assembled from historical `NetworkMetrics` snapshots labeled with the corresponding service KPIs derived from end-to-end probes.

---

### Maintenance and Upgrade Scenarios
*Problems: rolling upgrades, planned outages, rollback assessment*

#### Graph-based Reinforcement Learning

**Why it fits / how it works**

Upgrade sequencing is a combinatorial optimisation problem: for N routers there are N! possible orderings, and the optimal order depends on the current network state, the VPN topology, and live traffic load. RL learns a *policy* — a mapping from network state to the best next action — rather than exhaustively searching the space. By operating over GNN embeddings of the graph rather than raw features, the agent generalises across different network states that look structurally similar in latent space, even if they differ in raw telemetry values. Each step the agent takes (upgrading one router) changes the graph state, and the policy is rewarded for sequences that minimise cumulative service impact across the entire maintenance window.

**Input feature encoding**

The RL state at each step is the current GNN embedding of the full network graph — the same node embeddings produced by a pre-trained GCN or MPNN. This means the RL agent does not consume raw features directly; it operates in the learned latent space of network state.

State vector per step: concatenation of all node embeddings `[N × embedding_dim]` plus a binary mask vector indicating which routers have already been upgraded.

Action space: choose the next router to upgrade (one of the N routers not yet upgraded). The action is represented as a node selection over the graph.

Reward signal derived from `NetworkMetrics`:
```
reward = −Σ_services service_impact_score(t)
service_impact_score = w_latency × Δlatency + w_drops × Δdrop_rate + w_reachability × (1 − reachability)
```

A negative reward means the upgrade step caused service degradation; the policy learns to sequence upgrades to minimise cumulative impact.

**Training method and loss**

Trained with Proximal Policy Optimization (PPO) in a simulated environment built from historical bitemporal snapshots. Each rollout replays a historical upgrade window: the agent picks an upgrade order, the simulator applies the sequence using historical state transitions from Spanner, and the reward is computed from the observed KPI trajectory.

```
Loss_PPO = −E[min(r_t × A_t, clip(r_t, 1−ε, 1+ε) × A_t)]  +  c1 × value_loss  −  c2 × entropy
```

where `r_t` is the ratio of new to old policy probability, `A_t` is the advantage estimate, and the entropy term encourages exploration. ε=0.2, c1=0.5, c2=0.01 are standard starting values.

---

#### Variational Autoencoders (VAE)

**Why it fits / how it works**

VAEs learn a compressed, probabilistic representation of complete network state snapshots. The encoder maps a high-dimensional snapshot (all node and edge features across the whole network) down to a low-dimensional latent vector; the decoder reconstructs the original snapshot from that vector. The latent space is smooth and continuous by design: small changes to the network produce small movements in latent space, while large structural changes produce large displacements. This geometric property makes the L2 distance between two latent vectors a reliable measure of "how different are these two network states?" — directly applicable to rollback assessment, where the question is whether the restored state has returned to the same neighbourhood in latent space as the pre-change baseline.

**Input feature encoding**

The VAE encodes a complete network state snapshot into a low-dimensional latent vector. A snapshot is a flattened concatenation of all node feature vectors at a single timestamp:

```
snapshot_vector = concat(node_features_router_1, ..., node_features_router_N,
                         edge_features_link_1, ..., edge_features_link_M)
```

Node features are the same config + protocol + metric vectors used throughout. Edge features include `PhysicalLink` bandwidth, status, and current utilization. The vector is normalized per-feature using statistics computed from the training set.

For rollback assessment: the pre-change snapshot is encoded to a latent vector `z_before`. After the rollback, the restored snapshot is encoded to `z_after`. The L2 distance `||z_before − z_after||` quantifies how closely the network has returned to its pre-change state. A threshold on this distance (learned from historical rollback events) determines whether the rollback is complete.

**Training method and loss**

Standard VAE objective (Evidence Lower BOund, ELBO):

```
Loss = Reconstruction_loss + β × KL_divergence

Reconstruction_loss = MSE(decoded_snapshot, original_snapshot)
KL_divergence = −0.5 × Σ(1 + log(σ²) − μ² − σ²)
```

β=1.0 for a standard VAE; increase to β=4.0 (β-VAE) for a more disentangled latent space where individual latent dimensions correspond to interpretable network state factors (e.g., one dimension for control-plane health, another for data-plane load). Training data: all historical snapshots with `valid_end_ts IS NOT NULL` (i.e., states that existed and then changed), as these represent real stable network states.

---

#### Transfer Learning / Fine-Tuning

**Why it fits / how it works**

Production networks accumulate limited labeled history for rare events — there may be only a handful of historical rolling upgrades or planned outages to learn from. Transfer learning solves this by first pre-training on abundant synthetic data from a simulated topology (the VyOS lab), where faults and maintenance events can be injected freely, then fine-tuning on the small set of real production events. The pre-trained backbone has already learned general representations of network health — what healthy utilisation looks like, how BGP reconvergence manifests in embeddings, how config changes propagate — that transfer directly to the production topology. Fine-tuning only needs to adapt the task-specific prediction heads to the production environment's characteristics, requiring far less data than training from scratch.

**Input feature encoding**

Pre-training uses the same feature schema as above but sourced from a **simulated** topology (e.g., the VyOS lab hub-and-spoke setup in `telco-lab/l3vpn-hub-spoke.yaml`) rather than production data. Synthetic faults — link failures, config mutations, traffic surges — are injected and recorded as labeled training examples.

The pre-trained backbone (typically a 3–4 layer GCN or MPNN) learns generic network representations. During fine-tuning on the production topology, only the task-specific prediction heads are updated initially (the backbone is frozen) to avoid overwriting the pre-trained representations with limited production data.

Fine-tuning input uses identical encoding to the pre-training phase, with one addition: a **topology embedding** derived from structural graph statistics (diameter, average degree, number of VRFs) is appended to the node features. This helps the model distinguish the production topology from the simulation and adapt its predictions accordingly.

**Training method and loss**

**Phase 1 — Pre-training** (on simulation data, abundant labels):
```
Loss = task_loss (MSE or BCE depending on KPI target)
```
Train for 200 epochs with full gradient updates to all layers.

**Phase 2 — Fine-tuning** (on production data, limited labeled events):
```
Loss = task_loss + λ × ||θ_backbone − θ_pretrained||²
```
The L2 regularisation term (weight λ=0.01) anchors the backbone weights close to their pre-trained values, preventing catastrophic forgetting when production data is scarce. After 20 epochs with the backbone frozen, unfreeze and train jointly for another 50 epochs with the anchored loss. A minimum of 30 labeled production events (e.g., 30 historical maintenance windows with recorded KPI outcomes) is sufficient to fine-tune the prediction heads.

