# HetGNN Example — PyTorch / torch_geometric

Heterogeneous GNN autoencoder that identifies *which fault layer* is responsible
(Interface / BGP / Router) rather than just *which node* is anomalous.

```bash
pip install torch torch-geometric
python simple_hetgnn_pinpointing.py
```

---

## Production correspondence

This is a **direct simplified port** of `domains/transport/gnn/src/model/hetgnn.py`.

| Pattern | This example | Production `hetgnn.py` |
|---|---|---|
| Arch | `HeteroConv(SAGEConv, aggr='sum')` | Identical |
| Init | `set_input_dims(input_dims)` | Identical |
| Forward | `x_dict, edge_index_dict → (recon_dict, out_embeddings)` | Identical |
| Weights | `self.lin_dict`, `self.decoder_dict` (ModuleDict) | Identical |
| Data | Synthetic tensors | Spanner `NetworkMetrics` query |
| Hidden | 32 | 64 |

Replacing the synthetic data generators with Spanner queries is the only
change needed to promote this to production.

---

## Topology

```
  [router: pe1] ──has_interface──▶ [interface: pe1_eth1]
  [router: p1 ] ──has_interface──▶ [interface: p1_eth3 ]
                                         ↕ connects_to
  [router: pe1] ──has_bgp──────▶  [bgp_session: pe1_bgp]
```

| Node type | Nodes | Features |
|---|---|---|
| `router` | pe1, p1 | cpu, mem, ospf_state |
| `interface` | pe1_eth1, p1_eth3 | tx_drops, rx_drops, mtu_norm |
| `bgp_session` | pe1_bgp | bgp_state, pfx_count_norm, uptime_norm |

Edge indices are `{(src_type, relation, dst_type): Tensor[2, E]}` — local indices
within each node type's own index space, matching PyG's `HeteroConv` convention.

---

## Architecture

```
x_dict = {
  'router':      [N_R=2, F=3]
  'interface':   [N_I=2, F=3]
  'bgp_session': [N_B=1, F=3]
}
    │
lin_dict[nt](x) → ReLU        # per-type projection  [N, F] → [N, H]
    │
HeteroConv(SAGEConv) × 2      # typed message passing
    │
h_dict = {nt: [N, H=32]}      # out_embeddings
    │
decoder_dict[nt](h)            # per-type decode       [N, H] → [N, F]
    │
recon_dict = {nt: [N, F]}

branch_score[nt] = max(mean((recon[nt] - x[nt])², dim=1))
```

---

## Faults demonstrated

### Fault 1 — MTU mismatch on pe1_eth1 (Interface branch)

```
pe1_eth1:  tx_drops = 0.75  (was 0.01)
           mtu_norm = 0.156  (1400 bytes, was 0.167 = 1500 bytes)
Router and BGP unchanged.
```

Expected output:
```
Branch-level diagnosis:
  router         0.0001
  interface      0.18xx   ⚠️  FAULT LAYER
  bgp_session    0.0001
→ Root layer: interface
```

### Fault 2 — pe1_bgp session teardown (BGP branch)

```
pe1_bgp:  bgp_state = 0.0  (was 1.0 = Established)
          pfx_count = 0.0  (was 0.50 = 500 prefixes)
          uptime    = 0.0  (timer reset)
```

Expected output:
```
Branch-level diagnosis:
  router         0.0001
  interface      0.0001
  bgp_session    0.63xx   ⚠️  FAULT LAYER
→ Root layer: bgp_session
```

---

## Multi-task loss

```python
loss = (loss_router + loss_interface + loss_bgp) / 3.0
```

Production uses weighted sum `α·L_config + β·L_protocol + γ·L_metrics`
(research doc recommends α=0.3, β=0.3, γ=0.4 as starting weights).

---

## Production wiring

```python
# Replace this:
snapshots = generate_normal_snapshots(n=500)

# With this (Spanner):
from google.cloud import spanner
query = """
  SELECT node_name, metric_name, value
  FROM NetworkMetrics
  WHERE timestamp BETWEEN @start AND @end
  AND valid_end_ts IS NULL
"""
x_dict = build_x_dict_from_spanner(results)

# Write anomaly scores back:
INSERT INTO NodeEmbedding (node_id, anomaly_score, explanation, valid_start_ts)
VALUES (@node_id, @score, @branch_diagnosis, CURRENT_TIMESTAMP())
```

---

## Next step

→ `pytorch/STGNN/` — adds GRU temporal memory so the model catches transient
  faults (BGP flaps, short spikes) that have already recovered by the time
  the model runs.
