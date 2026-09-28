#!/usr/bin/env python3
# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
test_kfp_training_local.py — Local equivalent of the GNN KFP training pipeline.

Runs all pipeline steps directly (no Vertex AI Custom Jobs, no KFP
orchestration, no Docker containers) so you can iterate quickly on a laptop
that has Spanner access and the Python dependencies installed.

Steps mirrored from the KFP pipeline:
  1. Ingest  — fetch snapshots from Spanner → local .pkl files
  2. Train   — fit scalers + train HetGNN in-process via train_hetgnn_on_snapshots()
               (same logic used by the Vertex AI training container)
  3. Manifest— write latest_run.json locally (and optionally push to GCS)

Steps NOT run locally (require cloud services):
  - deploy_endpoint   (Vertex AI Endpoint)
  - update_scheduler  (Cloud Scheduler)

Usage (from repo root, with gnn/requirements.txt installed):
  python gnn/tests/test_kfp_training_local.py \\
      --project my-project \\
      --spanner-instance networktopology-instance \\
      --spanner-database transporttopology-db

  # Skip ingest to reuse existing snapshots (faster iteration):
  python gnn/tests/test_kfp_training_local.py \\
      --skip-ingest \\
      --snapshots-dir /tmp/gnn_local/snapshots \\
      --output-dir /tmp/gnn_local

  # After training, push artifacts to GCS so serve.py can pick them up:
  python gnn/tests/test_kfp_training_local.py \\
      --project my-project \\
      --gcs-bucket my-project-gnn-artifacts \\
      --push-to-gcs

Prerequisites:
  pip install -r gnn/requirements.txt
  gcloud auth application-default login
"""

import importlib.metadata as _im
if not hasattr(_im, "packages_distributions"):
    _im.packages_distributions = lambda: {}

import argparse
import json
import logging
import os
import pickle
import sys
import uuid
from datetime import datetime
from pathlib import Path

import torch

# ── Add gnn/src to path (this file lives at gnn/tests/) ───────────────
_GNN_SRC = Path(__file__).resolve().parent.parent / "src"  # gnn/src/
sys.path.insert(0, str(_GNN_SRC))

# Import the shared training function — same code the Vertex AI container runs
from train_hetgnn import train_hetgnn_on_snapshots, HIDDEN_CHANNELS, NUM_LAYERS, EPOCHS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("local_pipeline")


def _debug_check_snapshot(snap: dict, label: str = "snapshot") -> None:
    """Log counts and warn about any zero numeric values in a snapshot dict."""
    def _iter_numbers(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from _iter_numbers(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from _iter_numbers(v, f"{path}[{i}]")
        elif isinstance(obj, (int, float)):
            yield path, obj

    fields = list(_iter_numbers(snap))
    zeros  = [(k, v) for k, v in fields if v == 0]
    logger.debug("[debug] %s — %d numeric fields, %d zero, %d non-zero",
                 label, len(fields), len(zeros), len(fields) - len(zeros))

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Ingest
# ─────────────────────────────────────────────────────────────────────────────

def step_ingest(args, snapshots_dir: Path) -> list:
    """Fetch snapshots from Spanner and save them to snapshots_dir as pkl files."""
    from utils.data import SpannerDataset

    logger.info("=" * 60)
    logger.info("STEP 1 — INGEST")
    logger.info("=" * 60)

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    interval = args.interval_minutes or 5  # default: 5-minute snapshot cadence

    logger.info(
        f"Fetching {args.num_snapshots} snapshots from "
        f"{args.spanner_instance}/{args.spanner_database} (interval={interval}m)"
    )

    dataset = SpannerDataset(
        instance_id=args.spanner_instance,
        database_id=args.spanner_database,
        num_snapshots=args.num_snapshots,
        interval_minutes=interval,
        project_id=args.project,
        from_time=getattr(args, "from_time", None) or None,
        to_time=getattr(args, "to_time", None) or None,
    )

    timestamps = dataset._get_timestamps()
    logger.info(f"Found {len(timestamps)} timestamps in Spanner")

    snapshots = []
    for ts in timestamps:
        try:
            snapshot = dataset.fetch_snapshot(ts)
            if snapshot.get("nodes"):
                snapshots.append(snapshot)
            else:
                logger.warning(f"Empty snapshot at {ts.isoformat()} — skipped")
        except Exception as exc:
            logger.error(f"Failed to fetch snapshot at {ts.isoformat()}: {exc}")

    logger.info(f"Fetched {len(snapshots)} non-empty snapshots")

    if len(snapshots) < args.num_snapshots:
        logger.warning(
            f"Retrieved {len(snapshots)} snapshots out of {args.num_snapshots} requested. "
            f"Training will proceed with available data. "
            f"This may occur if Spanner does not have enough historical data for the requested time range."
        )

    if not snapshots:
        raise RuntimeError(
            "No snapshots fetched. Check that the network operator is running "
            "and has written topology data to Spanner."
        )

    # Compute temporal gradient/delta features across the ordered snapshot sequence
    SpannerDataset.compute_temporal_features(snapshots, interval_seconds=interval * 60)
    logger.info("Temporal features (rx_err_gradient) computed")

    for i, snap in enumerate(snapshots):
        # ── Debug: check values are non-zero before serialisation ─────────
        _debug_check_snapshot(snap, label=f"snapshot_{i:04d}")

        with open(snapshots_dir / f"snapshot_{i:04d}.pkl", "wb") as f:
            pickle.dump(snap, f)
        json_path = snapshots_dir / f"snapshot_{i:04d}.json"
        with open(json_path, "w") as f:
            json.dump(snap, f, indent=2)

    logger.info(f"Saved {len(snapshots)} snapshots (pkl + json) to {snapshots_dir}")
    return snapshots


def load_snapshots_from_dir(snapshots_dir: Path) -> list:
    """Load snapshot pkl files from a local directory."""
    paths = sorted(snapshots_dir.glob("snapshot_*.pkl"))
    if not paths:
        raise FileNotFoundError(
            f"No snapshot_*.pkl files found in {snapshots_dir}. "
            "Run without --skip-ingest first."
        )
    snapshots = [pickle.load(open(p, "rb")) for p in paths]
    logger.info(f"Loaded {len(snapshots)} snapshots from {snapshots_dir}")
    for i, snap in enumerate(snapshots):
        _debug_check_snapshot(snap, label=f"snapshot_{i:04d}.pkl")
    return snapshots


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Manifest
# ─────────────────────────────────────────────────────────────────────────────

def step_manifest(run_id: str, output_dir: Path, val_loss: float, args) -> Path:
    """Write latest_run.json locally and optionally push all artifacts to GCS."""
    logger.info("=" * 60)
    logger.info("STEP 3 — MANIFEST")
    logger.info("=" * 60)

    manifest = {
        "run_id":        run_id,
        "local_run":     True,
        "registered_at": datetime.utcnow().isoformat(),
        "models": {
            "hetgnn": {
                "gcs_path":   str(output_dir),
                "val_loss":   val_loss,
                "model_path": str(output_dir / "model.pth"),
            }
        },
    }
    manifest_path = output_dir / "latest_run.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written to {manifest_path}")

    if args.push_to_gcs and args.gcs_bucket:
        _push_to_gcs(run_id, output_dir, val_loss, args)
    elif args.gcs_bucket and not args.push_to_gcs:
        logger.info(
            f"Tip: add --push-to-gcs to upload artifacts to "
            f"gs://{args.gcs_bucket} so serve.py can use them."
        )

    return manifest_path


def _push_to_gcs(run_id: str, output_dir: Path, val_loss: float, args):
    """Upload model artifacts to GCS so serve.py can load them."""
    from google.cloud import storage as gcs

    client = gcs.Client(project=args.project)
    bucket = client.bucket(args.gcs_bucket)

    uploads = [
        (output_dir / "model.pth",       f"models/hetgnn/{run_id}/model.pth"),
        (output_dir / "model_stats.pth", f"models/hetgnn/{run_id}/model_stats.pth"),
        (output_dir / "scalers.pkl",     f"models/hetgnn/{run_id}/scalers.pkl"),
    ]
    for local_path, gcs_key in uploads:
        if local_path.exists():
            bucket.blob(gcs_key).upload_from_filename(str(local_path))
            logger.info(f"  Uploaded {local_path.name} → gs://{args.gcs_bucket}/{gcs_key}")

    manifest = {
        "run_id":        run_id,
        "local_run":     False,
        "registered_at": datetime.utcnow().isoformat(),
        "models": {
            "hetgnn": {
                "gcs_path": f"gs://{args.gcs_bucket}/models/hetgnn/{run_id}",
                "val_loss": val_loss,
            }
        },
    }
    bucket.blob("models/latest/latest_run.json").upload_from_string(
        json.dumps(manifest, indent=2), content_type="application/json"
    )
    logger.info(f"  Uploaded latest_run.json → gs://{args.gcs_bucket}/models/latest/")
    logger.info(f"serve.py / Cloud Scheduler will pick up run {run_id[:8]}.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string (e.g. '2026-05-19T10:30:00')."""
    return datetime.fromisoformat(s)


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # GCP / Spanner
    p.add_argument("--project",          default=os.getenv("GOOGLE_CLOUD_PROJECT",
                                                           os.getenv("GOOGLE_PROJECT", "gnn-networks")),
                   help="GCP project ID (or set GOOGLE_CLOUD_PROJECT / GOOGLE_PROJECT)")
    p.add_argument("--spanner-instance", default=os.getenv("SPANNER_INSTANCE", "networktopology-instance"),
                   help="Spanner instance ID (or set SPANNER_INSTANCE)")
    p.add_argument("--spanner-database", default=os.getenv("SPANNER_DATABASE", "transporttopology-db"),
                   help="Spanner database ID (or set SPANNER_DATABASE)")
    p.add_argument("--interval-minutes", type=float, default=0.5,
                   help="Snapshot interval in minutes (default: 0.5 = 30s, matching notebook cadence)")

    # Data — training window
    p.add_argument("--from-time",       type=_parse_dt, default=None,
                   help="Training window start (ISO 8601, e.g. 2026-05-19T10:30:00). "
                        "When set, --to-time and --interval-minutes determine the snapshot count.")
    p.add_argument("--to-time",         type=_parse_dt, default=None,
                   help="Training window end (ISO 8601). Defaults to latest data in Spanner.")
    p.add_argument("--num-snapshots",   type=int, default=200,
                   help="Max snapshots to fetch when no --from-time is given (default: 200)")
    p.add_argument("--skip-ingest",     action="store_true",
                   help="Skip ingest; load snapshots from --snapshots-dir")
    p.add_argument("--snapshots-dir",   type=Path,
                   help="Directory of snapshot pkl files (used with --skip-ingest)")

    # Data — test window (mirrors notebook TEST_FROM / TEST_TO)
    p.add_argument("--test-from-time",  type=_parse_dt, default=None,
                   help="Test evaluation window start (ISO 8601). Skipped if not set.")
    p.add_argument("--test-to-time",    type=_parse_dt, default=None,
                   help="Test evaluation window end (ISO 8601).")
    p.add_argument("--test-snapshots-dir", type=Path, default=None,
                   help="Cache directory for test snapshots (defaults to <output-dir>/test_snapshots)")

    # Output
    p.add_argument("--output-dir",      type=Path, default=Path("/tmp/gnn_local_run"),
                   help="Local root for all artifacts")

    # Training hyperparameters (override train_hetgnn.py defaults)
    p.add_argument("--epochs",          type=int, default=50,
                   help="Max training epochs (default: 50, matching notebook)")
    p.add_argument("--hidden-channels", type=int, default=HIDDEN_CHANNELS)
    p.add_argument("--num-layers",      type=int, default=NUM_LAYERS)

    # Quality gate (mirrors the pipeline condition)
    p.add_argument("--max-val-loss",    type=float, default=1.0,
                   help="Quality gate threshold (pipeline only deploys below this)")

    # GCS push
    p.add_argument("--gcs-bucket",      default=None)
    p.add_argument("--push-to-gcs",     action="store_true",
                   help="Upload artifacts to GCS after training (requires --gcs-bucket)")

    return p.parse_args()


def step_test_eval(model, gb, test_snapshots: list) -> None:
    """Run inference on held-out test snapshots and log per-node-type MSE stats.

    Mirrors the anomaly detection assessment cell in the notebook.
    """
    from utils.gnn_utils import NODE_TYPES, ANOMALY_THRESHOLDS

    logger.info("=" * 60)
    logger.info("STEP 4 — TEST EVALUATION")
    logger.info("=" * 60)

    criterion = torch.nn.MSELoss(reduction="none")
    node_errors: dict = {nt: [] for nt in NODE_TYPES}

    model.eval()
    with torch.no_grad():
        for snap in test_snapshots:
            hdata = gb.process_snapshot(snap)
            recons, _ = model(hdata.x_dict, hdata.edge_index_dict)
            for ntype, recon in recons.items():
                if ntype in hdata.x_dict:
                    mse = criterion(recon, hdata.x_dict[ntype]).mean(dim=1)
                    node_errors[ntype].extend(mse.cpu().tolist())

    logger.info(f"  Evaluated {len(test_snapshots)} test snapshot(s)")
    for ntype in NODE_TYPES:
        errs = node_errors[ntype]
        if not errs:
            logger.info(f"  {ntype:12s}: no data")
            continue
        t = torch.tensor(errs)
        threshold = ANOMALY_THRESHOLDS.get(ntype, 0.5)
        pct_anomalous = (t > threshold).float().mean().item() * 100
        logger.info(
            f"  {ntype:12s}: n={len(errs):4d}  "
            f"mean={t.mean():.4f}  p50={t.median():.4f}  "
            f"p95={t.quantile(0.95):.4f}  threshold={threshold}  "
            f"anomalous={pct_anomalous:.1f}%"
        )


def main():
    args = parse_args()

    run_id       = f"local-{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    output_dir   = args.output_dir / run_id
    snapshots_dir = args.snapshots_dir or (output_dir / "snapshots")

    logger.info("=" * 60)
    logger.info(f"GNN LOCAL PIPELINE  run_id={run_id}")
    logger.info(f"Output directory:   {output_dir}")
    logger.info(f"Interval:           {args.interval_minutes}m")
    if args.from_time:
        logger.info(f"Training window:    {args.from_time} → {args.to_time or 'latest'}")
    if args.test_from_time:
        logger.info(f"Test window:        {args.test_from_time} → {args.test_to_time or 'latest'}")
    logger.info("=" * 60)

    # ── Step 1 — Ingest ──────────────────────────────────────────────────────
    if args.skip_ingest:
        if not args.snapshots_dir:
            raise ValueError("--skip-ingest requires --snapshots-dir")
        snapshots = load_snapshots_from_dir(args.snapshots_dir)
    else:
        if not args.project:
            raise ValueError("--project is required (or set GOOGLE_CLOUD_PROJECT / GOOGLE_PROJECT env var)")
        snapshots = step_ingest(args, snapshots_dir)

    # ── Step 2 — Train (scalers + model + cluster stats) ─────────────────────
    logger.info("=" * 60)
    logger.info("STEP 2 — TRAIN HetGNN")
    logger.info("=" * 60)

    model, val_loss, gb, history = train_hetgnn_on_snapshots(
        snapshot_objects=snapshots,
        output_dir=str(output_dir),
        hidden_channels=args.hidden_channels,
        num_layers=args.num_layers,
        epochs_override=args.epochs,
    )

    # ── Step 3 — Manifest ────────────────────────────────────────────────────
    step_manifest(run_id, output_dir, val_loss, args)

    # ── Step 4 — Test evaluation (optional, mirrors notebook cell 4) ─────────
    if args.test_from_time:
        test_snapshots_dir = args.test_snapshots_dir or (output_dir / "test_snapshots")
        pkls = sorted(test_snapshots_dir.glob("snapshot_*.pkl")) if test_snapshots_dir.exists() else []
        if pkls:
            logger.info(f"Loading cached test snapshots from {test_snapshots_dir}")
            test_snapshots = load_snapshots_from_dir(test_snapshots_dir)
        else:
            test_args = argparse.Namespace(
                project=args.project,
                spanner_instance=args.spanner_instance,
                spanner_database=args.spanner_database,
                interval_minutes=args.interval_minutes,
                num_snapshots=args.num_snapshots,
                from_time=args.test_from_time,
                to_time=args.test_to_time,
            )
            test_snapshots = step_ingest(test_args, test_snapshots_dir)
        step_test_eval(model, gb, test_snapshots)

    # ── Quality gate summary ──────────────────────────────────────────────────
    gate_pass = val_loss < args.max_val_loss
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  run_id:       {run_id}")
    logger.info(f"  val_loss:     {val_loss:.4f}")
    logger.info(f"  max_val_loss: {args.max_val_loss}")
    logger.info(f"  quality gate: {'PASS ✓' if gate_pass else 'FAIL ✗'}")
    logger.info(f"  artifacts:    {output_dir}")

    if not gate_pass:
        logger.warning(
            f"val_loss ({val_loss:.4f}) exceeds threshold ({args.max_val_loss}). "
            "The cloud pipeline would NOT deploy this model."
        )
    else:
        logger.info(
            "Model passes quality gate. "
            "Add --push-to-gcs --gcs-bucket <bucket> to promote to serve.py."
        )

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
