"""
test_model_training.py — CLI equivalent of test_training_and_serving_alvaro.ipynb.

Run from gnn/tests/:
    python3 test_model_training.py [--skip-ingest] [--skip-train]
"""
import importlib.metadata as _im
if not hasattr(_im, "packages_distributions"):
    _im.packages_distributions = lambda: {}

import argparse
import asyncio
import datetime
import json
import logging
import os
import pickle
import statistics
import sys
from pathlib import Path
from unittest.mock import patch

# ── Paths ─────────────────────────────────────────────────────────────────────
TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR   = TESTS_DIR.parent / "src"
for p in [str(SRC_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Credentials ───────────────────────────────────────────────────────────────
_creds = str(SRC_DIR / "networkagent.json")
if os.path.exists(_creds):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _creds
os.environ["GOOGLE_CLOUD_PROJECT"] = "gnn-networks"

import torch
import joblib

import train_hetgnn
import serve
import test_kfp_training_local
from utils.gnn_utils import (
    NODE_TYPES, ROUTER_FEATURES, INTERFACE_FEATURES,
    VPN_FEATURES, FLOW_FEATURES, ANOMALY_THRESHOLDS, FEATURE_DIMS,
    GraphBuilder,
)
from model.hetgnn import HetGNN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("test_model_training")

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ID       = "gnn-networks"
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "transporttopology-db")
INTERVAL_MINUTES = 0.5

# Dynamic UTC time ranges for the last 3 hours
_now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
# Round to nearest 30s interval
_now_utc = _now_utc - datetime.timedelta(seconds=_now_utc.second % 30, microseconds=_now_utc.microsecond)

TRAIN_FROM = _now_utc - datetime.timedelta(hours=3)
TRAIN_TO   = _now_utc - datetime.timedelta(minutes=15)
TEST_FROM  = _now_utc - datetime.timedelta(minutes=15)
TEST_TO    = _now_utc

OUTPUT_DIR   = TESTS_DIR / "notebook_artifacts"
SYNTH_DIR    = TESTS_DIR / "anomaly_synthetic_data"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Step helpers ──────────────────────────────────────────────────────────────

def load_or_fetch(cache_dir, from_time, to_time, label):
    cache_dir = Path(cache_dir)
    pkls = sorted(cache_dir.glob("*.pkl"))
    if pkls:
        log.info(f"[{label}] Cache hit: {len(pkls)} snapshots from {cache_dir.name}/")
        return [pickle.load(open(p, "rb")) for p in pkls]
    n = int((to_time - from_time).total_seconds() / (INTERVAL_MINUTES * 60))
    args = argparse.Namespace(
        project=PROJECT_ID, spanner_instance=SPANNER_INSTANCE,
        spanner_database=SPANNER_DATABASE, interval_minutes=INTERVAL_MINUTES,
        num_snapshots=n, from_time=from_time, to_time=to_time,
    )
    log.info(f"[{label}] Fetching {n} snapshots ({from_time} → {to_time}) ...")
    try:
        return test_kfp_training_local.step_ingest(args, cache_dir)
    except RuntimeError:
        log.warning(f"[{label}] No data for window — falling back to most recent snapshots")
        args.from_time = args.to_time = None
        return test_kfp_training_local.step_ingest(args, cache_dir)


def step_ingest():
    log.info("=== STEP 1: INGEST ===")
    train = load_or_fetch(OUTPUT_DIR / "snapshots",      TRAIN_FROM, TRAIN_TO, "train")
    test  = load_or_fetch(OUTPUT_DIR / "test_snapshots", TEST_FROM,  TEST_TO,  "test")
    log.info(f"Train snapshots: {len(train)}, Test snapshots: {len(test)}")
    # Connectivity check on first train snapshot with flows
    for snap in train:
        flows = [n for n in snap["nodes"] if n["type"] == "flow"]
        if not flows:
            continue
        flow_ids = {f["id"] for f in flows}
        flow_edges = [e for e in snap["edges"] if e["source"] in flow_ids or e["target"] in flow_ids]
        connected  = {e["source"] for e in flow_edges if e["source"] in flow_ids} | \
                     {e["target"] for e in flow_edges if e["target"] in flow_ids}
        disconnected = flow_ids - connected
        log.info(f"Flow connectivity check: {len(flows)} flows, {len(flow_edges)} edges, "
                 f"{len(disconnected)} disconnected")
        if disconnected:
            log.warning(f"  Disconnected flow IDs: {disconnected}")
        break
    return train, test


def step_train(train_snapshots):
    log.info("=== STEP 2: TRAIN ===")
    model, val_loss, gb, history = train_hetgnn.train_hetgnn_on_snapshots(
        snapshot_objects=train_snapshots,
        output_dir=str(OUTPUT_DIR),
        hidden_channels=64,
        num_layers=2,
        epochs_override=50,
    )
    log.info(f"Training finished. Best Val Loss: {val_loss:.4f}")
    return model, gb


def step_evaluate(model, gb, test_snapshots):
    log.info("=== STEP 3: EVALUATE (test set) ===")
    model.eval()
    criterion = torch.nn.MSELoss(reduction="none")
    node_type_errors = {nt: [] for nt in NODE_TYPES}

    with torch.no_grad():
        for snap in test_snapshots:
            hdata = gb.process_snapshot(snap)
            recons, _ = model(hdata.x_dict, hdata.edge_index_dict)
            for ntype, recon in recons.items():
                if ntype in hdata.x_dict:
                    mse = criterion(recon, hdata.x_dict[ntype]).mean(dim=1)
                    node_type_errors[ntype].extend(mse.cpu().numpy().tolist())

    print("\n── Test-set MSE per node type ───────────────────────────────────")
    print(f"  {'Node type':<16} {'n':>6}  {'mean MSE':>10}  {'max MSE':>10}  {'threshold':>10}  {'anomalies':>10}")
    print("  " + "-" * 68)
    for ntype in NODE_TYPES:
        scores = node_type_errors.get(ntype, [])
        if not scores:
            continue
        thresh  = ANOMALY_THRESHOLDS.get(ntype, 0.50)
        n_anom  = sum(1 for s in scores if s > thresh)
        print(f"  {ntype:<16} {len(scores):>6}  {statistics.mean(scores):>10.4f}  "
              f"{max(scores):>10.4f}  {thresh:>10.2f}  {n_anom:>9} ({100*n_anom/len(scores):.1f}%)")


def step_synthetic(model, gb):
    log.info("=== STEP 4: SYNTHETIC ANOMALY INFERENCE ===")
    snaps = sorted(SYNTH_DIR.glob("*.json"))
    if not snaps:
        log.warning("No synthetic snapshots found — run generate_anomalies.py first")
        return

    snap = json.load(open(snaps[-1]))
    log.info(f"Snapshot: {snap['timestamp']}  nodes={len(snap['nodes'])}  edges={len(snap['edges'])}")

    # Connectivity check
    flows    = [n for n in snap["nodes"] if n["type"] == "flow"]
    flow_ids = {f["id"] for f in flows}
    flow_edges = [e for e in snap["edges"] if e["source"] in flow_ids or e["target"] in flow_ids]
    connected  = {e["source"] for e in flow_edges if e["source"] in flow_ids} | \
                 {e["target"] for e in flow_edges if e["target"] in flow_ids}
    log.info(f"Flow connectivity: {len(flows)} flows, {len(flow_edges)} edges, "
             f"{len(flow_ids - connected)} disconnected")

    hetero_data = gb.process_snapshot(snap)
    node_id_map = hetero_data.node_id_map

    feat_names = {
        "router": ROUTER_FEATURES, "interface": INTERFACE_FEATURES,
        "vpn": VPN_FEATURES, "flow": FLOW_FEATURES,
    }

    model.eval()
    found = []
    with torch.no_grad():
        recon_dict, _ = model(hetero_data.x_dict, hetero_data.edge_index_dict)

    print("\n─── SYNTHETIC INFERENCE RESULTS ────────────────────────────────")
    for ntype, recon in recon_dict.items():
        if ntype not in hetero_data.x_dict:
            continue
        orig     = hetero_data[ntype].x
        feat_mse = (recon - orig) ** 2
        node_mse = feat_mse.mean(dim=1)
        thresh   = ANOMALY_THRESHOLDS.get(ntype, 0.50)
        fnames   = feat_names.get(ntype, [])
        for idx in range(node_mse.size(0)):
            score = node_mse[idx].item()
            if score > thresh:
                nid      = node_id_map[ntype][idx]
                top_i    = torch.argmax(feat_mse[idx]).item()
                top_f    = fnames[top_i] if top_i < len(fnames) else "unknown"
                found.append({"id": nid, "type": ntype, "score": score, "top_feature": top_f})
                print(f"[!] ANOMALY {ntype:<10} {nid:<40} MSE={score:.4f} (>{thresh:.2f})  top={top_f}")

    if not found:
        print("  No anomalies detected above threshold.")
    else:
        print(f"\n  {len(found)} anomal{'y' if len(found)==1 else 'ies'} detected.")


async def step_serve():
    log.info("=== STEP 5: SERVE (inference on live snapshot) ===")

    def mock_manifest():
        return {"run_id": "notebook_test_run"}

    def mock_artefacts(_):
        return OUTPUT_DIR

    # Drop any cached model from a previous run — the "notebook_test_run" key
    # is stable across executions, so without this _ensure_model_loaded() would
    # short-circuit and silently serve stale weights.
    serve._cache.clear()

    # Patch INTERVAL_MINUTES to match training cadence — rx_err_gradient is
    # normalised by interval_seconds, so a mismatch silently rescales the
    # feature (serve.py default is 5min; we train at 0.5min).
    with patch("serve._load_manifest",    side_effect=mock_manifest), \
         patch("serve._download_artefacts", side_effect=mock_artefacts), \
         patch("serve.SPANNER_INSTANCE",  SPANNER_INSTANCE), \
         patch("serve.SPANNER_DATABASE",  SPANNER_DATABASE), \
         patch("serve.GOOGLE_CLOUD_PROJECT", PROJECT_ID), \
         patch("serve.INTERVAL_MINUTES", INTERVAL_MINUTES):

        results = await serve._run_inference()

    print(f"\nSnapshot timestamp : {results['snapshot_timestamp']}")
    print(f"Total anomalies    : {results['anomaly_count']}")
    thresholds = serve._cache.get("thresholds", serve._FALLBACK_THRESHOLDS)
    print(f"\n  {'Node Type':<18} {'Nodes':>6}  {'Anomalies':>10}  Threshold")
    print("  " + "-" * 48)
    for ntype, scores in results["anomaly_scores"].items():
        thresh = thresholds.get(ntype, 0.50)
        n_anom = sum(1 for s in scores if s > thresh)
        print(f"  {ntype:<18} {len(scores):>6}  {n_anom:>10}  MSE>{thresh:.2f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--skip-train",  action="store_true")
    ap.add_argument("--skip-serve",  action="store_true")
    args = ap.parse_args()

    log.info(f"gnn_utils loaded from: {__import__('utils.gnn_utils', fromlist=['']).__file__}")

    # Ingest
    if args.skip_ingest:
        pkls_train = sorted((OUTPUT_DIR / "snapshots").glob("*.pkl"))
        pkls_test  = sorted((OUTPUT_DIR / "test_snapshots").glob("*.pkl"))
        train_snapshots = [pickle.load(open(p, "rb")) for p in pkls_train]
        test_snapshots  = [pickle.load(open(p, "rb")) for p in pkls_test]
        log.info(f"Skipping ingest — loaded {len(train_snapshots)} train / {len(test_snapshots)} test from cache")
    else:
        train_snapshots, test_snapshots = step_ingest()

    # Train
    if args.skip_train:
        log.info("Skipping training — loading existing model artifacts")
        ckpt     = torch.load(OUTPUT_DIR / "model.pth", map_location="cpu", weights_only=False)
        scalers  = joblib.load(OUTPUT_DIR / "scalers.pkl")
        gb       = GraphBuilder(scalers=scalers)
        _sample  = gb.process_snapshot(train_snapshots[0])
        model    = HetGNN(
            _sample.metadata(),
            hidden_channels=ckpt.get("hidden_channels", 64),
            out_channels=64,
            num_layers=ckpt.get("num_layers", 2),
        )
        model.set_input_dims(FEATURE_DIMS)
        model.load_state_dict(ckpt["model_state_dict"])
        model.eval()
        log.info(f"Loaded model (val_loss={ckpt.get('val_loss', float('nan')):.4f})")
    else:
        model, gb = step_train(train_snapshots)

    step_evaluate(model, gb, test_snapshots)
    step_synthetic(model, gb)

    if not args.skip_serve:
        asyncio.run(step_serve())


if __name__ == "__main__":
    main()
