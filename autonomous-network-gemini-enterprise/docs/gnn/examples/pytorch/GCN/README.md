# GCN Example — PyTorch / torch_geometric

Simplest possible PyG failure pinpointing: a homogeneous GCN autoencoder
trained on healthy telemetry, scoring anomalous nodes by reconstruction error.

```bash
pip install torch torch-geometric
python simple_gcn_pinpointing.py
```

---

## Production correspondence

| This example | Production (`domains/transport/gnn/src/model/`) |
|---|---|
| `GCNConv` layers | `SAGEConv` via `HeteroConv` in `hetgnn.py` / `stgnn.py` |
| `nn.ModuleList` of convs | `self.convs` (identical pattern) |
| `nn.Linear` decoder | `self.decoder_dict[node_type]` |
| `(recon, embeddings)` return | `(recon_dict, out_embeddings)` |
| MSE loss on healthy data | Same self-supervised objective |

The GCN uses a single shared weight matrix for all nodes and edges.
The production HetGNN replaces this with per-type weights via `HeteroConv`.

---

## Topology

```
  pe1 (0) ── p1 (1) ── ce1-spoke (2)
```

6 features per node: `tx_drops, rx_drops, mtu_norm, cpu_percent, mem_percent, ospf_state`

Edge index is bidirectional `[2, 4]`. `GCNConv` adds self-loops automatically.

---

## What it demonstrates

**Fault 1 — MTU mismatch on pe1:**
- `tx_drops` spikes to 0.75 (healthy: 0.01)
- `mtu_norm` deviates to 0.156 (MTU 1400 vs 1500)
- Model trained only on healthy data flags `pe1` with highest MSE
- Feature breakdown ranks `tx_drops` as the top driver

**Limitation:** The GCN outputs "pe1 is anomalous" but cannot say *which layer*
(config / protocol / interface) the fault is in. That requires HetGNN.

---

## Architecture

```
x [N=3, F=6]
    │
GCNConv(6, 32) + ReLU         # conv layer 1
    │
GCNConv(32, 32) + ReLU        # conv layer 2
    │
h [N=3, H=32]                 # latent embeddings (out_embeddings)
    │
Linear(32, 6)                 # decoder
    │
recon [N=3, F=6]

anomaly_score[i] = mean((recon[i] - x[i])²)   # per-node MSE
```

---

## Next step

→ `pytorch/HetGNN/` — adds typed branches (Router / Interface / BGP)
  so the model names the fault *layer*, not just the fault *node*.
