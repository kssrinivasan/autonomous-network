# STGNN Example — PyTorch / torch_geometric

Spatio-Temporal GNN autoencoder that catches **transient faults** — events
that happened inside the observation window but have already recovered by the
time inference runs.

```bash
pip install torch torch-geometric
python simple_stgnn_pinpointing.py
```

---

## The problem it solves

A static HetGNN processes one snapshot at a time. If a BGP session flaps DOWN
and then comes back UP within the same polling window, the final snapshot looks
perfectly healthy — anomaly score ≈ 0.

The STGNN feeds the full sequence `[t=0, t=1, t=2]` through a GRU. The GRU's
hidden state at t=2 still carries the "scar" from t=1's fault. The decoder
cannot reconstruct t=2's healthy features from that displaced hidden state,
producing a high anomaly score even though t=2 looks normal.

---

## Production correspondence

This is a **direct simplified port** of the production HetGNN in `domains/transport/gnn/src/model/hetgnn.py` (there is no separate `stgnn.py` — the temporal model is folded into it).

| Pattern | This example | Production `stgnn.py` |
|---|---|---|
| Spatial | `HeteroConv(SAGEConv, aggr='mean')` | Identical |
| Temporal | `nn.GRU` per node type in `self.rnns` (ModuleDict) | Identical |
| Init | `set_input_dims(input_dims)` | Identical |
| Input | `x_dict_seq: {nt: [N, T, F]}` | Identical |
| Forward | `x_dict_seq, edge_index_dict, hidden_state_dict → (recon_dict, out_embeddings, new_hidden_states)` | Identical |
| Loss | `MSE(recon[nt], x_seq[nt][:, -1, :])` | Identical |
| Data | Synthetic sequences | Spanner `NetworkMetrics` sliding windows |
| Hidden | 32, T=3 | 64, T=60 (fast window) |

---

## Topology

Same as HetGNN example — static topology across all T time steps:

```
  [router: pe1] ──has_bgp──▶ [bgp_session: pe1_bgp]
  [router: pe1] ──has_interface──▶ [interface: pe1_eth1]
  [router: p1 ] ──has_interface──▶ [interface: p1_eth3]
```

---

## Input format

```python
x_dict_seq = {
  'router':      Tensor[N_R=2, T=3, F=3],   # cpu, mem, ospf at each step
  'interface':   Tensor[N_I=2, T=3, F=3],   # drops, mtu at each step
  'bgp_session': Tensor[N_B=1, T=3, F=3],   # bgp_state, pfx, uptime at each step
}
```

Production uses `T=60` for the "fast" (5-minute, 5-second interval) window.

---

## Architecture

```
x_dict_seq {nt: [N, T, F]}
    │
for t in 0..T-1:
  ├─ lin_dict[nt](x[:,t,:]) + ReLU       # typed projection
  ├─ HeteroConv(SAGEConv) × 2            # spatial message passing
  └─ record v_t[nt]                      # spatial embedding at step t

for each node type nt:
  h_seq = stack(v_0[nt], ..., v_{T-1}[nt])   # [N, T, H]
  rnn_out, h_n = rnns[nt](h_seq)             # GRU: [N, T, H], [1, N, H]
  final_emb = rnn_out[:, -1, :]              # [N, H] — trajectory summary
  recon[nt] = decoder_dict[nt](final_emb)   # [N, F]

anomaly_score[nt] = MSE(recon[nt], x_seq[nt][:, -1, :])
```

---

## Faults demonstrated

### Fault 1 — BGP flap (t=1 DOWN, t=2 recovered)

```
bgp_session sequence:
  t=0:  [1.0, 0.50, 0.80]   ← healthy
  t=1:  [0.0, 0.00, 0.00]   ← fault
  t=2:  [1.0, 0.50, 0.80]   ← recovered (looks identical to healthy)
```

Static HetGNN on t=2 alone → score ≈ 0.000 (no alarm)
STGNN on full sequence     → bgp_session score >> 0 (alarm raised)

### Fault 2 — Persistent MTU mismatch (all 3 time steps)

```
interface[pe1_eth1] sequence:
  t=0,1,2:  [0.75, 0.01, 0.156]   ← sustained tx_drops + MTU deviation
```

Both HetGNN and STGNN catch this. STGNN additionally captures the
trajectory (sustained = hardware/config) vs the BGP flap (transient = protocol).

---

## GRU hidden state (streaming inference)

```python
# Production pattern: pass hidden state between inference windows
hidden = None
for window in sliding_windows(telemetry, T=60, step=5):
    recon, embeddings, hidden = model(window, edge_index_dict, hidden)
    scores = compute_scores(recon, window)
    alert_if_threshold_crossed(scores)
```

The `hidden_state_dict` parameter in `forward()` is exposed for exactly this.

---

## Production wiring

```python
# Replace generate_normal_sequences() with:
query = """
  SELECT node_name, metric_name, timestamp, value
  FROM NetworkMetrics
  WHERE timestamp BETWEEN @start AND @end
  ORDER BY node_name, timestamp
"""
# Build sliding windows of T=60 steps at 5-second intervals
sequences = build_sequences_from_spanner(results, T=60, step_seconds=5)
```

For the multi-resolution approach from the research doc:
- **Fast** (T=60, 5s intervals) → microbursts, BGP flaps
- **Medium** (T=60, 1min intervals) → protocol convergence
- **Slow** (T=96, 15min intervals) → SFP degradation, memory leaks
