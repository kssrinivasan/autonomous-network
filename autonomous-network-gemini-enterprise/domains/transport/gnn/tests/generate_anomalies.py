"""
generate_anomalies.py

Creates synthetic anomaly snapshots from a cached local training snapshot.
No Spanner connection required.

Two nodes are injected with out-of-distribution features:
  - Router  "router:ce1-hub"                : CPU spike  (cpu=0.95, mem=0.80)
  - Interface (first iface on router:ce1-hub): CRC/error  (rx_errs_rate=0.85,
                                               rx_err_gradient=0.90,
                                               carrier_changes=8.0)

The model was trained with all cpu/mem/error values = 0.0, so these spikes
produce scaled inputs of 0.85–0.95 (scaler mean=0, scale=1) vs a decoder
that always outputs ~0 → per-node MSE >> anomaly thresholds (0.15 / 0.20).

Run from gnn/tests/:
    python3 generate_anomalies.py
"""
import copy
import json
import pickle
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SNAPSHOT_SRC = Path(__file__).parent / "notebook_artifacts" / "snapshots" / "snapshot_0050.pkl"
OUT_DIR      = Path(__file__).parent / "anomaly_synthetic_data"
OUT_DIR.mkdir(exist_ok=True)

# Delete any stale files from a previous run
for old in OUT_DIR.glob("*.json"):
    old.unlink()

# ── Load baseline ─────────────────────────────────────────────────────────────
with open(SNAPSHOT_SRC, "rb") as f:
    baseline = pickle.load(f)

print(f"Loaded baseline: {baseline['timestamp']} "
      f"({len(baseline['nodes'])} nodes, {len(baseline['edges'])} edges)")

# ════════════════════════════════════════════════════════════════════════════
# Snapshot 0 — clean baseline (no anomalies), used to warm up the notebook
# ════════════════════════════════════════════════════════════════════════════
snap_clean = copy.deepcopy(baseline)
snap_clean["timestamp"] = "2026-05-19T12:00:00"

with open(OUT_DIR / "snapshot_0000.json", "w") as f:
    json.dump(snap_clean, f, indent=2)
print("Saved clean baseline → snapshot_0000.json")

# ════════════════════════════════════════════════════════════════════════════
# Snapshot 1 — anomalous: CPU spike on one router + CRC spike on one interface
# ════════════════════════════════════════════════════════════════════════════
ROUTER_TARGET    = "router:ce1-hub"
INTERFACE_TARGET = None   # pinned to first interface of ce1-hub

snap_anom = copy.deepcopy(baseline)
snap_anom["timestamp"] = "2026-05-19T12:00:30"

for node in snap_anom["nodes"]:
    nid  = node["id"]
    ntyp = node["type"]

    # Anomaly 1: CPU + memory spike on the hub CE router
    if ntyp == "router" and nid == ROUTER_TARGET:
        node["state"] = 0.0  # training always 1.0  → scaled -1.0, expected ~0
        node["cpu"]   = 0.95 # training always 0.0  → scaled  0.95, expected ~0
        node["mem"]   = 0.80 # training always 0.0  → scaled  0.80, expected ~0
        print(f"\n[INJECT] Router state/CPU/mem spike → {nid}")
        print(f"          state: 1.0 → {node['state']}  (router down)")
        print(f"          cpu:   0.0 → {node['cpu']}")
        print(f"          mem:   0.0 → {node['mem']}")

    # Anomaly 2: CRC error spike on the first interface of ce1-hub
    if ntyp == "interface" and node.get("device_id") == ROUTER_TARGET:
        if INTERFACE_TARGET is None:
            INTERFACE_TARGET = nid
        if nid == INTERFACE_TARGET:
            node["rx_errs_rate"]    = 0.85   # training always 0.0
            node["rx_err_gradient"] = 0.90   # training always 0.0
            node["carrier_changes"] = 8.0    # training always 0.0
            print(f"\n[INJECT] Interface CRC spike   → {nid}")
            print(f"          rx_errs_rate:    0.0 → {node['rx_errs_rate']}")
            print(f"          rx_err_gradient: 0.0 → {node['rx_err_gradient']}")
            print(f"          carrier_changes: 0.0 → {node['carrier_changes']}")

with open(OUT_DIR / "snapshot_0001.json", "w") as f:
    json.dump(snap_anom, f, indent=2)

print(f"\nSaved anomalous snapshot → snapshot_0001.json")
print(f"\nSummary")
print(f"  Anomalous router    : {ROUTER_TARGET}")
print(f"  Anomalous interface : {INTERFACE_TARGET}")
print(f"  Output directory    : {OUT_DIR}")

# ════════════════════════════════════════════════════════════════════════════
# Snapshot 2 — VRF RT misconfiguration on PE3 (BLUE_SPOKE)
#
# Models the fault from environment/telco-lab/failure_tests/default/
#   vrf-rt-misconfiguration.yaml — which patches PE3's BLUE_SPOKE VRF
#   rt_import from ["65035:1030"] to ["65035:9999"]. PE3 stops importing
#   hub routes; CE2-spoke loses hub connectivity; BGP sessions stay Established.
#
# Effect on the router-aggregation features we added to gnn_utils.py:
#   - rt_import_signature: deviates on PE3 (different rt_import set hashes
#     to a different value) — PE1/PE4 stay at 0.6997, PE3 moves.
#   - vrf_min_routes: drops to 0.0 on PE3 (BLUE_SPOKE loses routes).
#   - vrf_count: unchanged (PE3 still hosts both VRFs).
#
# We pick a mutated rt_sig value that is clearly off the PE1/PE4 cluster
# (0.6997) but plausible (in [0,1] range; this is a hash → [0,1]).
# ════════════════════════════════════════════════════════════════════════════
PE3_TARGET             = "router:pe3"
RT_SIG_FAULTY          = 0.3142   # arbitrary distinct value vs healthy 0.6997
VRF_MIN_ROUTES_FAULTY  = 0.0      # BLUE_SPOKE has no routes after RT mismatch
VRF_RT_MISMATCH_FAULTY = 1.0      # PE3's BLUE_SPOKE rt_import disagrees with consensus
                                  # → Jaccard with the PE1/PE2/PE4 BLUE_SPOKE consensus is 0
                                  # → 1 - Jaccard = 1.0 (full mismatch)

snap_rt = copy.deepcopy(baseline)
snap_rt["timestamp"] = "2026-05-19T12:01:00"

injected_pe3 = False
for node in snap_rt["nodes"]:
    if node.get("type") == "router" and node.get("id") == PE3_TARGET:
        orig_sig      = node.get("rt_import_signature")
        orig_min      = node.get("vrf_min_routes")
        orig_mismatch = node.get("vrf_rt_import_mismatch")
        node["rt_import_signature"]      = RT_SIG_FAULTY
        node["vrf_min_routes"]           = VRF_MIN_ROUTES_FAULTY
        node["vrf_rt_import_mismatch"]   = VRF_RT_MISMATCH_FAULTY
        injected_pe3 = True
        print(f"\n[INJECT] PE3 VRF RT misconfiguration → {PE3_TARGET}")
        print(f"          rt_import_signature:    {orig_sig:.4f} → {node['rt_import_signature']}")
        print(f"          vrf_min_routes:         {orig_min} → {node['vrf_min_routes']}")
        print(f"          vrf_rt_import_mismatch: {orig_mismatch} → {node['vrf_rt_import_mismatch']}")
        break

if not injected_pe3:
    print(f"\n[WARN] PE3 router '{PE3_TARGET}' not found in baseline snapshot — "
          "RT misconfig snapshot will be effectively the same as the clean baseline.")

with open(OUT_DIR / "snapshot_0002.json", "w") as f:
    json.dump(snap_rt, f, indent=2)
print(f"\nSaved RT-misconfig snapshot → snapshot_0002.json")

print("\nDone. The notebook will load snapshot_0001.json (the last file) for inference.")

if __name__ == "__main__":
    pass
