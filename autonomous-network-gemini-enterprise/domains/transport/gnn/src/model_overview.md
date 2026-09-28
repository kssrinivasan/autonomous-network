# GNN Model Overview

This document provides a comprehensive overview of the **Heterogeneous Graph Neural Network (HetGNN)** model, describing its feature engineering mapping, graph layers, training hyperparameters, and production serving execution.

---

## 1. Node Features & Preprocessing Pipeline

Every raw network observation is processed and scaled before being converted into PyTorch Geometric `HeteroData` tensors.

### Missing Data Rule
Any missing telemetry or status value (`None` or blank) is hardcoded to **`0.0`** during graph tensor generation in `GraphBuilder`.

### Categorical & Numerical Scaling
Continuous telemetry columns are Z-score scaled ($\frac{x - \mu}{\sigma}$) using parameters fitted on the healthy training set snapshots via `fit_scalers`. Categorical features (such as one-hot role columns) are passed raw. Flows with all-zero traffic metrics (inactive/unmeasured flows) are excluded from scaler fitting to prevent them from polluting the distribution.

### Features Catalogue by Node Type

#### **Router Nodes (17 Features)**
*   `state` (Scaled): `1.0` if status is `"running"`, else `0.0`.
*   `cpu` (Scaled): Average 1-minute CPU load average.
*   `mem` (Scaled): Utilization fraction derived from $1.0 - (\text{MemAvailable} / \text{MemTotal})$.
*   `ospf_num_routes` (Scaled): Total OSPF routes calculated.
*   `pfx_count_norm` (Scaled): Total BGP prefixes advertised across BGP peers.
*   `routes_fib` (Scaled): Total active routes in Forwarding Information Base (FIB).
*   `bfd_peers` (Scaled): Count of active Bidirectional Forwarding Detection (BFD) peers.
*   `ospf_neighbors` (Scaled): Count of discovered OSPF neighbors.
*   `ospf_adj` (Scaled): Count of established OSPF adjacencies.
*   `rt_import_signature` (Scaled): Deterministic MD5 hash mapped to $[0,1]$ of the sorted set of import Route Targets across the router's VRFs. Flips when any VRF has an RT misconfiguration.
*   `vrf_min_routes` (Scaled): Minimum route count across all VRFs hosted on this router. Drops when any single VRF loses routes.
*   `vrf_count` (Scaled): Total number of VRFs hosted by this router (structural signal).
*   `vrf_rt_import_mismatch` (Scaled): Binary signal — `1.0` if any VRF on this router has an rt_import string that diverges from the expected per-VPN canonical value. Directly targets the `vrf-rt-misconfiguration` fault class.
*   `role_P`, `role_PE`, `role_RR`, `role_CE` (Raw): One-hot encoding of the router's architecture role.

Scaler scope: indices `0–12` (13 continuous features); indices `13–16` (role one-hot) are passed raw and not scaled.

#### **Interface Nodes (16 Features)**
*   `state` (Scaled): `1.0` if active and up, else `0.0`.
*   `rx_drops`, `tx_drops` (Scaled): Dropped packet rates.
*   `mtu_norm` (Scaled): MTU size normalized as $\text{MTU\_bytes} / 9000.0$.
*   `rx_errs_rate`, `tx_errs_rate` (Scaled): Receive/transmit errors per second.
*   `rx_bytes_rate`, `tx_bytes_rate` (Scaled): Bitrate per second.
*   `rx_packets_rate`, `tx_packets_rate` (Scaled): Packets per second.
*   `tx_queue_len` (Scaled): Depth of the transmit interface queue.
*   `carrier_changes` (Scaled): Frequency of interface status carrier flips.
*   `carrier_state` (Scaled): Current physical layer state value.
*   `rx_err_gradient` (Scaled): First-order rate of change of errors computed in-memory: $\frac{\text{rx\_errs\_rate}_{t} - \text{rx\_errs\_rate}_{t-1}}{\Delta t}$.
*   `rx_util`, `tx_util` (Scaled): Calculated utilization ratios relative to speed: $\frac{\text{throughput\_bps}}{i\_speed\_bps}$.

#### **VPN Nodes (3 Features)**
*   `state` (Scaled): `1.0` if status is active/running, else `0.0`.
*   `num_active_peers` (Scaled): Sum of VRF BGP sessions in an `"established"` state.
*   `total_prefixes` (Scaled): Cumulative OSPF and BGP prefixes active under this VPN VRF label.

> **Note**: `uptime_norm` was removed from VPN features. It saturates at `1.0` once a VPN is older than one day and becomes a dead feature that the decoder can never learn to reconstruct precisely, leading to spurious per-feature MSE explanations. The deployment only has N=3 VPN nodes, so removing one noisy feature improves stability.

#### **Flow Nodes (7 Features)**
*   `throughput_sent_bps` (Scaled): Bits sent per second measured by Traffic Agent.
*   `throughput_recv_bps` (Scaled): Bits received per second measured by Traffic Agent.
*   `latency_ms` (Scaled): Traffic round-trip latency (averaged across the Spanner window to suppress single-sample bursts).
*   `jitter_ms` (Scaled): Packet jitter (averaged).
*   `packet_loss_pct` (Scaled): Percentage of packets dropped (UDP flows, averaged).
*   `is_running` (Scaled): `1.0` if phase is `"running"`, else `0.0`.
*   `is_bidirectional` (Scaled): `1.0` if traffic flow configuration is bidirectional, else `0.0`.

> **Note**: Flow KPI aggregation uses `AVG(value)` rather than `MAX(value)` in the Spanner query. This suppresses oscillating anomaly scores caused by single-sample latency/jitter/packet-loss bursts on UDP reverse flows.

---

## 2. Graph Schema

The heterogeneous graph contains **4 node types** connected by **11 directed edge types**.

### Edge Types

| Edge | Relation | Direction |
|:---|:---|:---|
| `(router, has_interface, interface)` | Physical containment | → |
| `(interface, interface_of, router)` | Inverse of above | ← |
| `(interface, connected_to, interface)` | Physical link (symmetric) | ↔ |
| `(router, peer_of, router)` | OSPF + iBGP control plane (symmetric) | ↔ |
| `(router, member_of, vpn)` | Service layer membership | → |
| `(vpn, has_member, router)` | Inverse of above | ← |
| `(flow, originates_from, interface)` | Source CE-facing interface | → |
| `(flow, terminates_at, interface)` | Destination CE-facing interface | → |
| `(interface, source_of, flow)` | Inverse of originates_from | ← |
| `(interface, destination_of, flow)` | Inverse of terminates_at | ← |
| `(flow, runs_over, vpn)` | Flow-to-VPN service binding | → |

---

## 3. Model Architecture

The model is a **Heterogeneous Graph Autoencoder** (`HetGNN`) formulated to process schemas with disjoint node topologies.

### Layer Stack

```
Input node vectors (d_i)
        │
        ▼
Linear Projection (lin_dict)            # d_i → hidden=64, per node type
        │  ReLU + Dropout(p=0.3)
        ▼
HeteroConv Layer 1 (SAGEConv, aggr=mean) # hidden=64 → hidden=64
        │  ReLU + Dropout(p=0.3)
        ▼
HeteroConv Layer 2 (SAGEConv, aggr=mean) # hidden=64 → hidden=64
        │
        ├──────────────────────────────────────────► Node Embeddings z_i ∈ ℝ^64
        │                                            (used for anomaly scoring)
        ▼
Linear Decoders (decoder_dict)           # hidden=64 → d_i, per node type
        │
        ▼
Reconstructed input vectors x̂_i         (used for feature attribution only)
```

### Component Details

| Component | Implementation | Notes |
|:---|:---|:---|
| **Input projection** (`lin_dict`) | `nn.Linear(d_i, 64)` per node type | Maps heterogeneous feature dims to a uniform 64-d latent space |
| **Activation** | `ReLU` | Applied after projection and after each conv layer |
| **Regularization** | `Dropout(p=0.3)` | Applied after projection ReLU and after each SAGEConv ReLU |
| **Message passing** (`convs`) | `HeteroConv(SAGEConv(64→64), aggr='mean')` × 2 | Symmetric mean-aggregation GraphSAGE; both layers share the same `hidden=64` dimension |
| **Isolated nodes** | Retain previous representation | Nodes with no incoming edges of a given type keep their layer-*k−1* embedding rather than being zeroed |
| **Output embeddings** | `h_dict[nt]` after final conv | `z_i ∈ ℝ^64` — input to Mahalanobis anomaly scoring |
| **Decoders** (`decoder_dict`) | `nn.Linear(64, d_i)` per node type | Type-specific reconstruction heads; output used for per-feature attribution, not anomaly flagging |

### Feature Dimensions

| Node Type | Input Dim (`d_i`) | Continuous (scaled) | Categorical (raw) |
|:---|:---:|:---:|:---:|
| Router | 17 | 13 (indices 0–12) | 4 one-hot role cols |
| Interface | 16 | 16 | — |
| VPN | 3 | 3 | — |
| Flow | 7 | 7 | — |

---

## 4. Training Regime

### Dataset Configuration
*   **Snapshot Cadence**: **30 seconds** per snapshot (`INTERVAL_MINUTES = 0.5`).
*   **Training Window**: **360 snapshots** (~3 hours) fetched by default from Spanner (`num_snapshots` in `pipeline.py`).
*   **Outlier Snapping**: Snapshots with statistical averages $>4.0\sigma$ away from the global mean (e.g., data collection gaps) are automatically dropped.
*   **Validation Split**: **20%** of the fetched sequence is held out for validation.

### Hyperparameters

| Parameter | Code Symbol | Value / Default | Description |
|:---|:---|:---|:---|
| **Random Seed** | `RANDOM_SEED` | `None` (stochastic) | No default seed — stochastic initialization is more robust than seed pinning for this autoencoder. Set via `RANDOM_SEED` env var to re-enable a fixed seed (e.g., for a sweep). Two previously pinned seeds (42, 1337) both produced degenerate VPN models. |
| **Epochs** | `EPOCHS` | `50` | Maximum training epochs. |
| **Learning Rate** | `LEARNING_RATE` | `0.001` | Initial Adam step size. |
| **Hidden Channels** | `HIDDEN_CHANNELS` | `64` | Embedding dimensionality for all node types. |
| **Number of Layers** | `NUM_LAYERS` | `2` | SAGEConv message-passing layers. |
| **Dropout** | `DROPOUT` | `0.3` | Regularization rate applied after initial projection and after each SAGEConv layer. |
| **Validation Split** | `VALIDATION_SPLIT` | `0.20` | Fraction of snapshots held out for validation. |
| **Early Stopping Patience** | `EARLY_STOPPING_PATIENCE` | `10` | Epochs to wait without validation improvement before halting. |
| **Early Stopping Delta** | `MIN_DELTA` | `0.001` | Minimum improvement in validation loss to reset the patience counter. |
| **Router Loss Weight** | `ALPHA` | `0.25` | Reconstruction loss multiplier for router nodes. |
| **Interface Loss Weight** | `GAMMA` | `0.25` | Reconstruction loss multiplier for interface nodes. |
| **VPN Loss Weight** | `VPN_WEIGHT` | `0.25` | Reconstruction loss multiplier for VPN nodes (raised from 0.10 → 0.25 to boost gradient signal for the N=3 VPN population). |
| **Flow Loss Weight** | `FLOW_WEIGHT` | `0.25` | Reconstruction loss multiplier for flow nodes (raised from 0.10 → 0.25). |
| **Diversity Loss Weight** | `DIVERSITY_WEIGHT` | `0.10` | Contrastive diversity penalty multiplier. |

### Loss Function

The training objective minimizes a combined multi-task loss:

$$\mathcal{L}_{total} = 0.25 \cdot \mathcal{L}_{router} + 0.25 \cdot \mathcal{L}_{interface} + 0.25 \cdot \mathcal{L}_{vpn} + 0.25 \cdot \mathcal{L}_{flow} + 0.10 \cdot \mathcal{L}_{diversity}$$

*   **Per-type reconstruction loss** ($\mathcal{L}_{router}$, etc.): Mean squared error between the decoder output $\hat{x}_i$ and the original input $x_i$ for each node type.
*   **Contrastive Diversity Loss** ($\mathcal{L}_{diversity}$): Penalizes pairwise cosine similarity within each node type's embedding batch. Computed as the mean upper-triangle similarity of the normalized embedding matrix, encouraging the model to produce diverse latent representations and prevent embedding collapse.

### Optimizer & Scheduler

| Setting | Value |
|:---|:---|
| **Optimizer** | Adam |
| **Initial LR** | `0.001` |
| **LR Scheduler** | `ReduceLROnPlateau` — multiplies LR by `0.5` after 5 epochs without validation improvement |
| **Early Stopping** | Halts and restores best weights if validation loss does not improve for 10 consecutive epochs ($\Delta < 0.001$) |

---

## 5. Post-Training Baseline Calibration

After training, the model runs all training snapshots through the encoder in evaluation mode to establish healthy-state statistics used at serving time.

### Mahalanobis Distance Scoring

For each node type, the calibration step computes:

1.  **Centroid** $\mu \in \mathbb{R}^{64}$ and **covariance** $\Sigma \in \mathbb{R}^{64 \times 64}$ of healthy training embeddings.
2.  **Regularized inverse covariance**:
    $$\Sigma_{reg}^{-1} = (\Sigma + \epsilon I)^{-1}, \quad \epsilon = 10^{-5}$$
    The ridge term $\epsilon I$ ensures invertibility for rare-class types (e.g., VPN with N=3 nodes per snapshot, which can produce near-rank-deficient covariances).
3.  **Mahalanobis distance** for each healthy training embedding:
    $$D_M(z) = \sqrt{(z - \mu)^T \Sigma_{reg}^{-1} (z - \mu)}$$

If covariance inversion fails at runtime, the code falls back to the identity matrix (degrading $D_M$ to Euclidean distance) and logs a warning.

### Threshold Calibration Formula

The anomaly detection threshold for each node type is:

$$\text{threshold}_{type} = \max\!\left(\bar{D}_M + K \cdot \sigma_{D_M},\; D_M^{max} \times \text{FLOOR\_MULT}_{type}\right)$$

| Parameter | Value | Rationale |
|:---|:---|:---|
| **MAHA\_K** | `2.0` | ~95th percentile of a Gaussian — sensitive enough to catch single-feature faults like vrf-rt-misconfiguration where only one PE's embedding shifts modestly |
| **FLOOR\_MULT router** | `1.2` | PE2 hub and CE/RR routers sit structurally far from the population centroid; live healthy D_M for PE2 routinely exceeds `mean + 2σ` |
| **FLOOR\_MULT interface** | `1.2` | Route-reflector loopbacks and hub-side links diverge from the bulk distribution for structural reasons |
| **FLOOR\_MULT vpn** | `1.5` | N=3 unique VPN nodes; empirical `mean+2σ` is artificially tight because variance is within-node temporal fluctuation, not across-population |
| **FLOOR\_MULT flow** | `1.2` | Per-flow KPIs drift with traffic conditions; pure `mean+2σ` on a 3h training window underestimates serving variance |

The threshold is logged at training time showing which branch (`mean+Kσ` or `floor`) was selected per type. Both the cluster statistics ($\mu$, $\Sigma^{-1}$) and calibrated thresholds are saved to `model_stats.pth`.

---

## 6. Embedding Generation & Anomaly Scoring in Depth

### Embedding Generation

Each forward pass of `HetGNN` produces a **64-dimensional embedding** $z_i \in \mathbb{R}^{64}$ for every node in the graph. The embedding path is:

1. **Input projection**: raw feature vector $x_i \in \mathbb{R}^{d_i}$ is projected into the shared latent space via `lin_dict[type](x_i)`, followed by `ReLU` and `Dropout(0.3)`. This maps all node types — regardless of their input dimensionality — to a common 64-d representation.

2. **Message passing (Layer 1)**: `HeteroConv` aggregates neighbor messages for each edge type using `SAGEConv` with mean aggregation:
   $$h_i^{(1)} = \sigma\!\left(W_1 \cdot \text{MEAN}\bigl\{h_j^{(0)} \mid j \in \mathcal{N}(i)\bigr\} + B_1 \cdot h_i^{(0)}\right)$$
   followed by `ReLU` + `Dropout(0.3)`. Nodes with no incoming edges of a given relation retain their $h^{(0)}$ projection unchanged.

3. **Message passing (Layer 2)**: same SAGEConv applied to $h^{(1)}$. The resulting $h_i^{(2)} \in \mathbb{R}^{64}$ is the final embedding $z_i$.

The embedding $z_i$ is a graph-contextualized representation — it encodes not just the node's own telemetry but the neighborhood's state across multiple hop levels and edge relation types (physical links, control-plane adjacencies, VPN membership, traffic flows).

### Mahalanobis Anomaly Scoring (Serving Time)

At serving time, `serve.py` computes the anomaly score for each node as follows:

1. **Load calibration artifacts**: $\mu_{type}$, $\Sigma_{reg}^{-1}$, and `calibrated_threshold` are loaded from `model_stats.pth` (saved at training time). These are kept in an in-process cache (`_cache`) so they survive across concurrent requests without re-loading.

2. **Compute embedding**: the snapshot is fed through `HetGNN` to obtain $z_i$ for every node.

3. **Score each node**:
   $$D_M(z_i) = \sqrt{(z_i - \mu_{type})^T \, \Sigma_{reg}^{-1} \, (z_i - \mu_{type})}$$
   A score of $D_M = 0$ means the node's embedding sits exactly at the healthy centroid. The score grows as the embedding moves away from the healthy cluster — both in *distance* and scaled by the *shape* of the healthy distribution (so directions of low variance are amplified, and noisy dimensions are down-weighted).

4. **Flag anomaly**: node is flagged if $D_M(z_i) > \text{threshold}_{type}$.

5. **Feature attribution**: for flagged nodes, the per-feature reconstruction MSE $\|x_i - \hat{x}_i\|^2$ is decomposed per feature column. The top-3 contributing features are recorded as a root-cause hint. This is purely interpretive — the anomaly flag comes from $D_M$, not from MSE.

### Why Mahalanobis over MSE

| Criterion | MSE (per-feature) | Mahalanobis (embedding-space) |
|:---|:---|:---|
| Accounts for feature correlation | No — treats each feature independently | Yes — $\Sigma^{-1}$ captures correlated feature drift |
| Sensitive to small multi-feature shifts | No — single-feature change diluted across all dims | Yes — shift in any principal direction is detected |
| Stable for small-N node types (VPN N=3) | No — decoder variance dominates | Yes — encoder embeddings are more stable than decoder outputs |
| Interpretability | Direct (which raw feature is wrong) | Indirect — top-3 MSE features added as attribution |

---

## 7. Serving Regime

*   **Serving Target**: Deployed as an HTTP server on Google Cloud Run / Vertex AI Endpoints. Model artifacts are loaded directly from the GCS path set in `AIP_STORAGE_URI` (injected by Vertex AI at deploy time). Falls back to the legacy `latest_run.json` manifest if `AIP_STORAGE_URI` is absent, enabling backward-compatible local testing.
*   **Execution Cadence**: Triggered **every 1 minute** via Cloud Scheduler (`gnn-inference-scheduler`).
*   **Serving Workflow**:
    1.  Receives POST `/predict` request containing a specific timestamp.
    2.  Pulls the latest snapshot at that timestamp from Spanner.
    3.  Feeds the snapshot through `GraphBuilder` using the calibrated `scalers.pkl` artifact.
    4.  Processes the graph through `HetGNN` to produce latent node embeddings $z_i \in \mathbb{R}^{64}$ and decoder reconstructions $\hat{x}_i$.
    5.  Computes the **Mahalanobis Distance** $D_M(z_i)$ per node using $\mu$ and $\Sigma_{reg}^{-1}$ from `model_stats.pth`.
    6.  Flags a node as anomalous if $D_M(z_i) > \text{threshold}_{type}$.
    7.  Uses per-feature reconstruction MSE (decoder output) strictly as an **attribution tool** to identify the top-3 features contributing to the anomaly for root-cause explanations. The anomaly *flag* is always driven by $D_M$, not MSE.
    8.  Saves node embeddings, Mahalanobis scores, anomaly flags, and root-cause feature attributions to the Spanner `NodeEmbedding` table.

> **Fallback**: For model artifacts produced before Mahalanobis scoring was introduced, `serve.py` falls back to per-feature reconstruction MSE with hardcoded thresholds. This ensures backward compatibility with older deployed model versions.

---

## 8. MLOps & Infrastructure Stack

### Training Pipeline (Vertex AI Pipelines / KFP v2)

Training is orchestrated as a **Kubeflow Pipelines v2** DAG, submitted to Vertex AI Pipelines via `submit_pipeline.py` (or triggered by `./install.sh --deploy gnn`). The pipeline compiles from `pipeline.py` and runs as the named job `gnn-training-pipeline`.

```
[ingest_snapshots] → [fit_scalers] → [train_model] → [evaluate_model]
                                                             │
                                          val_loss < 1.0?   │  (quality gate)
                                                  ┌──────── ▼ ───────────┐
                                                  │   [register_model]   │
                                                  │   [deploy_endpoint]  │
                                                  │ [update_scheduler]   │
                                                  └──────────────────────┘
```

| Pipeline Step | Component | Output |
|:---|:---|:---|
| **1. Ingest** | `ingest_snapshots` | Fetches 360 snapshots from Spanner (30 s cadence, 3 h window); parallelized via `ThreadPoolExecutor(max_workers=8)`; serializes to GCS as `.pkl` files |
| **2. Fit Scalers** | `fit_scalers` | Fits per-type `StandardScaler`s from the ingested snapshots; saves `scalers.pkl` to GCS |
| **3. Train** | `train_model` | Launches a Vertex AI Custom Training Job (`n1-standard-4` by default); runs `train_hetgnn.py`; saves `model.pth` + `model_stats.pth` to GCS |
| **4. Evaluate** | `evaluate_model` | Scores the trained model on the held-out 20% validation split; emits `val_loss` |
| **5. Quality Gate** | `dsl.Condition(val_loss < 1.0)` | Steps 6–8 are `NOT_TRIGGERED` and no deploy occurs if val_loss exceeds the threshold. Pipeline still reports `SUCCEEDED` — always check per-task states |
| **6. Register** | `register_model` | Registers `model.pth` + `scalers.pkl` + `serve_image` to Vertex AI Model Registry as a new `gnn-hetgnn` version |
| **7. Deploy** | `deploy_endpoint` | Deploys the new model version to the `gnn-endpoint` Vertex AI Endpoint, shifting 100% traffic to the new deployed model |
| **8. Update Scheduler** | `update_inference_scheduler` | Updates Cloud Scheduler `gnn-inference-scheduler` to point at the newly deployed endpoint |

### Serving Stack

| Component | Technology | Details |
|:---|:---|:---|
| **Model Server** | Vertex AI Endpoint + Cloud Run | HTTP server running `serve.py` (aiohttp); receives POST `/predict`, returns JSON anomaly summary |
| **Model Artifact Loading** | `AIP_STORAGE_URI` (env var) | Vertex AI sets this to the deployed model's GCS artifact dir; `serve.py` downloads `model.pth`, `model_stats.pth`, `scalers.pkl` to `/tmp` on first request and caches in-process across requests |
| **Inference Trigger** | Cloud Scheduler (`gnn-inference-scheduler`) | Cron `* * * * *` — fires every minute; sends POST `/predict` with current timestamp |
| **Data Source** | Google Cloud Spanner (`networktopology-db`) | `SpannerDataset` reads router, interface, VPN, flow, and VRF tables; applies SCD Type-2 point-in-time filters |
| **Result Storage** | Spanner `NodeEmbedding` table | Per-node embedding vectors, $D_M$ scores, anomaly flags, and top-3 feature attributions; PK `(node_id, timestamp DESC)` |

### Build & Deployment

| Step | Tool | Notes |
|:---|:---|:---|
| **Container build** | Cloud Build (`domains/transport/gnn/cloudbuild.tpl.yaml` → `cloudbuild.yaml`) | Jinja2 template rendered with env vars from `setenv.sh`. Source is tarred at `gcloud builds submit` time — edits after submit are NOT in the image |
| **Training image** | `gcr.io/.../traingnn-vertex` | Contains `train_hetgnn.py`, `model/`, `pipeline/`, `utils/` |
| **Serving image** | `gcr.io/.../servegnn-vertex` | Contains `serve.py`, model loaders, Spanner client |
| **Artifact store** | GCS `gnn-networks-gnn-artifacts` | Path pattern: `models/hetgnn/<pipeline_run_id>/` |
| **Model registry** | Vertex AI Model Registry | Versioned `gnn-hetgnn` entries; each has a `serve_image_uri` and GCS artifact pointer |

### Spanner Schema (relevant tables)

| Table | Pattern | Retention |
|:---|:---|:---|
| `PhysicalRouter`, `PhysicalInterface`, `VRF`, `BGPSession`, `L3VPNService`, `TrafficFlow`, … | SCD Type-2 topology — `(id, valid_start_ts DESC)`, `valid_end_ts IS NULL` = active row | Indefinite |
| `NetworkMetrics` | Time-series; indexed on `timestamp DESC` (`NetworkMetricsByTimestamp`) | 3 hours (metricscollector retention worker) |
| `TrafficFlowSeries` | Time-series; indexed on `timestamp DESC` (`TrafficFlowSeriesByTimestamp`) | One-day row deletion policy |
| `NodeEmbedding` | Per-node embedding + $D_M$ score per inference tick; PK `(node_id, timestamp DESC)` | Indefinite |
| `FaultEvent` | Ground-truth fault annotations for evaluation | Indefinite |
