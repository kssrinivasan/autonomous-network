# GNN Examples — Failure Pinpointing

Minimal, self-contained examples that demonstrate the GNN failure pinpointing
concepts from [`docs/gnn/research/failure_pinpointing_research.md`](../../../research/failure_pinpointing_research.md).

These examples are **standalone** — no Spanner connection, no Docker, no PyTorch.
They run offline with just TensorFlow and NumPy.

---

## `simple_failure_pinpointing.py`

**What it demonstrates:** The core GCN autoencoder pattern for anomaly detection
on the Spanner lab topology (`examples/controller/transport/default-network/`).

### Concept

A Graph Convolutional Network (GCN) autoencoder is trained on synthetic
"normal" snapshots of the network. At inference time, it tries to reconstruct
any new snapshot. Nodes the model finds surprising — high reconstruction error
— are flagged as anomalous. The node with the highest error is the predicted
root cause.

```
Normal training data (500 snapshots)
    ↓
GCN Autoencoder learns "what healthy looks like"
    ↓
Fault snapshot injected (PE1 MTU = 1400, TX drops spike)
    ↓
Model fails to reconstruct PE1's features
    ↓
PE1 flagged as root cause  ✓
```

### Topology

All 12 routers from the hub-and-spoke lab, connected by their physical p2p links:

```
               RR1 ─── P1 ─── P2
                │      │ \     │
                │      │  PE1──CE1-SPOKE
                │      │
               RR2 ─── P3 ─── PE2──CE1-HUB
                       │
                       P4 ─── PE3──CE2-SPOKE
                       │
                      RR2
```

### GCN Architecture

```
Input X  [N=12 nodes, F=7 features]
    │
    ▼  GCN Layer 1 (encoder)
    │  A_hat @ X @ W1 → ReLU   [N, 32]
    │
    ▼  GCN Layer 2 (decoder)
       A_hat @ H @ W2           [N, 7]  ← reconstruction
```

`A_hat = D^(-1/2) (A+I) D^(-1/2)` — the normalised adjacency matrix.
Implemented as plain matrix multiplication — no GNN library required.

The adjacency matrix is what makes this a **graph** network:
each node's features are aggregated with its neighbours' features before
the linear transformation `W`, so PE1's embedding "knows" it is connected to P1.

### Node Features (7 per node)

| # | Name | Source analogue | Range |
|---|------|----------------|-------|
| 0 | `cpu_percent` | `show system cpu` | 0–1 |
| 1 | `mem_percent` | `show system memory` | 0–1 |
| 2 | `tx_drops_rate` | interface TX drops, log-scaled | 0–1 |
| 3 | `rx_drops_rate` | interface RX drops, log-scaled | 0–1 |
| 4 | `bgp_state` | 1 = Established, 0 = Down | {0, 1} |
| 5 | `ospf_state` | 1 = Full, 0 = Down/Init | {0, 1} |
| 6 | `mtu_norm` | MTU / 9000 | 0–1 |

### Fault Simulated

**Fault 1 — Silent Drop via MTU Mismatch** (from the research doc):

- PE1 `eth1` MTU set to `1400` (facing P1, which stays at `1500`)
- Large packets silently dropped on the PE1→P1 direction
- OSPF/BGP control plane stays UP — small hello packets pass fine
- `tx_drops_rate` spikes on PE1, `mtu_norm` changes from `0.167` to `0.156`
- Model detects PE1 as the outlier and names it the root cause

### How to Run

```bash
# Install dependencies (TensorFlow + NumPy only)
pip install tensorflow numpy

# Run the example
cd gnn/examples
python simple_failure_pinpointing.py
```

### Expected Output

```
============================================================
  GNN Failure Pinpointing — Simple TensorFlow Example
  Topology: L3VPN Hub-and-Spoke (Spanner lab)
  Fault:    MTU mismatch on PE1 uplink → P1 (Fault 1)
============================================================

[1/4] Building graph topology: 12 nodes, 15 edges
[2/4] Generating synthetic normal training data (500 snapshots)...
...
[3/4] Evaluating on a HEALTHY snapshot (sanity check)...
      Mean reconstruction error (healthy): 0.000xxx
      Max  reconstruction error (healthy): 0.000xxx

[4/4] Injecting FAULT 1: MTU mismatch on PE1 ...

============================================================
  Anomaly Scores (per-node reconstruction error)
  Threshold: 0.xxxx  (mean + 3×std)
============================================================
  Rank  Node          Score    Status
  1     pe1           0.xxxx   ⚠️  ANOMALOUS
  2     p1            0.xxxx   ✓ normal
  ...

  🔍 Predicted root cause: pe1

  Feature breakdown for node 'pe1':
  Feature            Actual   Expected    Error²
  tx_drops_rate      0.7500     0.0xxx    0.xxxx   ← highest driver
  mtu_norm           0.1560     0.1xxx    0.xxxx
  ...
```

---

## What's Next

These examples are the simplest possible starting point. The
[research doc](../../../research/failure_pinpointing_research.md)
describes three progressively more powerful architectures:

| Step | Architecture | Adds | Best for |
|------|-------------|------|----------|
| ✅ **Here** | GCN Autoencoder | Snapshot anomaly detection | Getting started |
| Next | **D-GAT** | Directed edges, asymmetric attention | Silent blackholing, asymmetric drops |
| Next | **STGNN** | LSTM temporal backbone | Hardware degradation, slow faults |
| Next | **HetGNN** | Typed nodes (Router/Interface/BGP) | Config drift vs hardware vs protocol |

The production PyTorch implementations live in `domains/transport/gnn/src/model/`.
