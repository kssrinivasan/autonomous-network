# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
GNN Serving — Vertex AI Endpoint

HTTP prediction server deployed to Vertex AI after each successful training
pipeline run.  Triggered periodically by Cloud Scheduler AND available for
on-demand queries from network agents / the dashboard.

Each POST /predict request:
  1. Loads model weights + scalers from GCS (cached in /tmp across requests;
     reloads automatically when a new training run updates latest_run.json)
  2. Fetches the latest network snapshot from Spanner
  3. Runs inference → per-node embeddings + anomaly scores
  4. Writes results to the Spanner NodeEmbedding table
  5. Returns a JSON summary of anomalies detected

GET /health returns {"status": "healthy"}.

The scoring itself lives in ``gnn.serve.inference`` and operates on numpy, so
this file is backend-agnostic: the variant recorded in ``model_stats.pth``
decides whether the weights are loaded by PyTorch Geometric or by DGF, and
nothing downstream of ``score_snapshot`` changes either way.
"""

import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

from aiohttp import web
import aiohttp_cors

from gnn.backends import load_backend, parse_variant
from gnn.core.schema import ANOMALY_THRESHOLDS
from gnn.core.snapshot import build_arrays
from gnn.data import SpannerDataset
from gnn.serve import score_snapshot
from gnn.train import artifacts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("gnn.serve")

# ── Environment ───────────────────────────────────────────────────────────────
GCS_BUCKET_NAME      = os.getenv("GCS_BUCKET_NAME",     "gnn-networks-gnn-artifacts")
SPANNER_INSTANCE     = os.getenv("SPANNER_INSTANCE",     "networktopology-instance")
SPANNER_DATABASE     = os.getenv("SPANNER_DATABASE",     "transporttopology-db")
SERVICE_DATABASE     = os.getenv("SERVICE_DATABASE",     "servicetopology-db")
INFRA_DATABASE       = os.getenv("INFRA_DATABASE",       "infratopology-db")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", None)
INTERVAL_MINUTES     = float(os.getenv("INTERVAL_MINUTES", "0.5"))
CACHE_DIR            = Path(tempfile.gettempdir()) / "gnn_model_cache"

# Used only when model_stats.pth predates the multi-backend refactor and so
# carries no `variant` key. Everything trained before then was pyg.sage.
DEFAULT_VARIANT = os.getenv("MODEL_VARIANT", "pyg.sage")

# Per-type thresholds come from model_stats.pth at load time; these are the hard
# fallback defaults for callers that read the module before a model is cached.
# Retained under its original name because external code (notebooks,
# tests/test_model_training.py) reads `serve._FALLBACK_THRESHOLDS` directly.
#
# Now aliased to ANOMALY_THRESHOLDS rather than restating the numbers: the
# pre-refactor copy had drifted to flow=0.10 against ANOMALY_THRESHOLDS'
# flow=0.20, despite a comment claiming the two matched.
_FALLBACK_THRESHOLDS = dict(ANOMALY_THRESHOLDS)

# ── In-process model cache (warm across concurrent requests) ──────────────────
_cache: dict = {}


# ── GCS helpers ───────────────────────────────────────────────────────────────

def _gcs_client():
    from google.cloud import storage
    return storage.Client()


def _load_manifest() -> dict:
    """Resolve the current model artefact GCS dir.

    Prefers `AIP_STORAGE_URI` (Vertex AI Endpoint sets this env var to the
    deployed model's artifact dir, e.g. `gs://.../models/hetgnn/<run_id>`).
    Using it means a model swap is purely a Vertex AI endpoint deploy — no
    side-channel manifest update needed, so scalers and weights can never
    drift out of sync the way they did when this code read latest_run.json.

    Falls back to `models/latest/latest_run.json` for backwards compatibility
    with older deployments that don't set AIP_STORAGE_URI (local dev, batch
    prediction, anything not running behind a Vertex AI Endpoint).
    """
    aip_uri = os.getenv("AIP_STORAGE_URI", "").rstrip("/")
    if aip_uri.startswith("gs://"):
        # The artifact dir's last path segment is the training run id.
        run_id = aip_uri.rsplit("/", 1)[-1]
        logger.info(f"Loading model from AIP_STORAGE_URI={aip_uri} (run_id={run_id})")
        return {
            "run_id": run_id,
            "models": {"hetgnn": {"gcs_path": aip_uri}},
        }

    logger.info("AIP_STORAGE_URI not set; falling back to legacy latest_run.json manifest")
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob("models/latest/latest_run.json")
    if not blob.exists():
        raise FileNotFoundError(
            f"AIP_STORAGE_URI not set and "
            f"gs://{GCS_BUCKET_NAME}/models/latest/latest_run.json not found. "
            "Run the GNN training pipeline first."
        )
    return json.loads(blob.download_as_text())


def _download_artefacts(manifest: dict) -> Path:
    """Download model.pth and scalers.pkl for the current run into /tmp.

    Parses bucket + prefix from the manifest's ``gcs_path`` rather than
    assuming everything lives in GCS_BUCKET_NAME — Vertex AI Endpoint sets
    ``AIP_STORAGE_URI`` to its own internal staging bucket (e.g.
    ``gs://caip-tenant-…/<deployment_id>/artifacts``), which is NOT the same
    bucket as the GNN pipeline's artefact bucket.
    """
    run_id = manifest["run_id"]
    local_dir = CACHE_DIR / "hetgnn" / run_id
    local_dir.mkdir(parents=True, exist_ok=True)

    model_info = manifest.get("models", {}).get("hetgnn", {})
    gcs_path = model_info.get(
        "gcs_path",
        f"gs://{GCS_BUCKET_NAME}/models/hetgnn/{run_id}",
    )
    if not gcs_path.startswith("gs://"):
        raise ValueError(f"manifest gcs_path is not a gs:// URI: {gcs_path!r}")
    # Parse "gs://<bucket>/<prefix>" — bucket may differ from GCS_BUCKET_NAME
    without_scheme = gcs_path[len("gs://"):]
    bucket_name, _, prefix = without_scheme.partition("/")
    if not bucket_name:
        raise ValueError(f"manifest gcs_path missing bucket: {gcs_path!r}")

    client = _gcs_client()
    bucket = client.bucket(bucket_name)
    logger.info(f"Downloading artefacts from gs://{bucket_name}/{prefix} → {local_dir}")

    n = 0
    for blob in bucket.list_blobs(prefix=prefix):
        filename = Path(blob.name).name
        if filename.endswith(".pth") or filename.endswith(".pkl"):
            local_path = local_dir / filename
            if not local_path.exists():
                logger.info(f"Downloading {blob.name} → {local_path}")
                blob.download_to_filename(str(local_path))
                n += 1
    if n == 0:
        logger.warning(
            f"No .pth/.pkl artefacts found at gs://{bucket_name}/{prefix} — "
            "serve.py will fail to load the model."
        )

    return local_dir


# ── Model loader (lazy, cached, auto-refreshes on new run_id) ─────────────────

def _read_variant(local_dir: Path) -> str:
    """Which backend/arch produced this bundle."""
    stats_pth = local_dir / artifacts.STATS_FILENAME
    if stats_pth.exists():
        try:
            return artifacts.load_stats(stats_pth).get("variant") or DEFAULT_VARIANT
        except Exception as exc:
            logger.warning(f"Could not read variant from {stats_pth}: {exc}")
    return DEFAULT_VARIANT


def _ensure_model_loaded(manifest: dict) -> None:
    """Load or refresh the trained model + normalizer into _cache.

    Unlike the pre-refactor version this does **not** need a live Spanner
    snapshot: the checkpoint records the node and edge types it was trained
    with, so the graph metadata is reconstructed from the artefact alone. That
    removes a Spanner round-trip from cold start and, more importantly, removes
    a failure mode where a transient snapshot missing an edge type would have
    produced a model with the wrong shape.
    """
    run_id = manifest["run_id"]
    if _cache.get("run_id") == run_id and _cache.get("model") is not None:
        return  # already loaded for this run

    logger.info(f"Loading model for run_id={run_id}...")
    local_dir = _download_artefacts(manifest)

    variant = _read_variant(local_dir)
    backend_name, arch = parse_variant(variant)
    backend = load_backend(backend_name)
    logger.info(f"Bundle variant={variant} (backend={backend_name}, arch={arch})")

    normalizer = artifacts.load_normalizer(local_dir)
    model = backend.load(local_dir)

    # Per-type anomaly scoring config. cluster_stats[ntype] carries the healthy
    # embedding centroid, its regularised inverse covariance and the calibrated
    # threshold; when present, scoring is Mahalanobis distance in embedding
    # space. When absent (older artefacts, or a node type whose entry is
    # malformed) that type falls back to reconstruction MSE against the
    # hardcoded thresholds — degraded, but it still serves.
    thresholds = dict(ANOMALY_THRESHOLDS)
    cluster_stats = {}
    stats_pth = local_dir / artifacts.STATS_FILENAME
    if stats_pth.exists():
        try:
            stats = artifacts.load_stats(stats_pth)
            thresholds.update(stats.get("anomaly_thresholds", {}) or {})
            cluster_stats = artifacts.load_cluster_stats(stats)
            if cluster_stats:
                th_summary = ", ".join(
                    f"{k}={v.calibrated_threshold:.3f}" for k, v in cluster_stats.items()
                )
                logger.info(
                    f"Loaded Mahalanobis cluster_stats for {sorted(cluster_stats)}: "
                    f"thresholds={{ {th_summary} }}"
                )
            else:
                logger.info(
                    "No usable cluster_stats in model_stats.pth — falling back to "
                    f"MSE thresholds: {thresholds}"
                )
        except Exception as exc:
            logger.warning(f"Could not load model_stats.pth: {exc}")

    _cache.update({
        "run_id":        run_id,
        "variant":       variant,
        "backend":       backend,
        "model":         model,
        "normalizer":    normalizer,
        "thresholds":    thresholds,
        "cluster_stats": cluster_stats,
    })
    logger.info(f"Model loaded (run_id={run_id}, variant={variant})")


# ── Spanner writer ────────────────────────────────────────────────────────────

def _write_results_to_spanner(database, node_id_map: dict, snapshot: dict, results: dict):
    """Write one NodeEmbedding row per node (embeddings + anomaly scores + explanation)."""
    from google.cloud import spanner as gspanner

    node_names = {
        node["id"]: node.get("hostname", node.get("name", node["id"]))
        for node in snapshot.get("nodes", [])
    }

    rows = []
    for ntype, emb_list in results["embeddings"].items():
        scores      = results["anomaly_scores"].get(ntype, [])
        explanations = results.get("anomaly_explanations", {}).get(ntype, [])
        id_list     = node_id_map.get(ntype, [])
        for idx, embedding in enumerate(emb_list):
            node_id = id_list[idx] if idx < len(id_list) else None
            if node_id is None:
                continue
            score = float(scores[idx]) if idx < len(scores) else 0.0
            expl  = explanations[idx] if idx < len(explanations) else {}
            rows.append((
                str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{node_id}:{ntype}")),
                node_id,
                ntype,
                embedding,
                score,
                json.dumps({"name": node_names.get(node_id, node_id), **expl}),
                gspanner.COMMIT_TIMESTAMP,
            ))

    def _write(transaction):
        transaction.insert_or_update(
            table="NodeEmbedding",
            columns=[
                "id", "node_id", "node_type",
                "hetgnn_embedding", "hetgnn_score",
                "anomaly_explanation", "timestamp",
            ],
            values=rows,
        )

    database.run_in_transaction(_write)
    logger.info(f"Wrote {len(rows)} NodeEmbedding rows to Spanner")


# ── Core inference ────────────────────────────────────────────────────────────

async def _run_inference() -> dict:
    """Run one inference cycle and return a result summary dict."""
    manifest = _load_manifest()
    _ensure_model_loaded(manifest)

    backend       = _cache["backend"]
    model         = _cache["model"]
    normalizer    = _cache["normalizer"]
    thresholds    = _cache["thresholds"]
    cluster_stats = _cache["cluster_stats"]

    # Fetch the two most-recent snapshots so that temporal gradient / delta
    # features (rx_err_gradient, prefix_count_delta, vrf_route_count_delta,
    # throughput_delta) can be computed via compute_temporal_features().
    # Only the latest snapshot is passed to the model; the earlier one is only
    # used as the "previous" reference for finite-difference computation.
    dataset = SpannerDataset(
        instance_id=SPANNER_INSTANCE,
        database_id=SPANNER_DATABASE,
        service_database_id=SERVICE_DATABASE,
        infra_database_id=INFRA_DATABASE,
        num_snapshots=2,
        interval_minutes=INTERVAL_MINUTES,
        project_id=GOOGLE_CLOUD_PROJECT,
    )
    timestamps = dataset._get_timestamps()
    pair = [dataset.fetch_snapshot(ts) for ts in timestamps[-2:]]
    SpannerDataset.compute_temporal_features(pair, interval_seconds=INTERVAL_MINUTES * 60)
    latest_ts = timestamps[-1]
    snapshot  = pair[-1]   # the second snapshot has its deltas populated
    logger.info(f"Fetched snapshot pair ending at {latest_ts.isoformat()} (temporal features computed)")

    arrays = normalizer.transform(build_arrays(snapshot))
    graph  = backend.build_graph(arrays)

    result = score_snapshot(
        model, graph, arrays,
        cluster_stats=cluster_stats,
        mse_thresholds=thresholds,
    )

    results = {
        "snapshot_timestamp":   latest_ts.isoformat(),
        "embeddings":           result.embeddings,
        "anomaly_scores":       result.anomaly_scores,
        "anomaly_explanations": result.anomaly_explanations,
        "anomaly_count":        result.anomaly_count,
    }

    _write_results_to_spanner(dataset.database, result.node_id_map, snapshot, results)

    logger.info(f"Inference complete — {result.anomaly_count} anomalies detected.")
    return results


# ── HTTP handlers ─────────────────────────────────────────────────────────────

async def predict_handler(request: web.Request) -> web.Response:
    logger.info("POST /predict")
    try:
        results = await _run_inference()
        return web.json_response({
            "predictions": [{
                "snapshot_timestamp": results["snapshot_timestamp"],
                "anomaly_count":      results["anomaly_count"],
                "anomaly_scores":     results["anomaly_scores"],
            }]
        })
    except Exception as exc:
        logger.exception(f"Inference failed: {exc}")
        return web.json_response({"error": str(exc)}, status=500)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})


# ── App setup ─────────────────────────────────────────────────────────────────

app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})

predict_route_path = os.environ.get("AIP_PREDICT_ROUTE", "/predict")
health_route_path  = os.environ.get("AIP_HEALTH_ROUTE",  "/health")

predict_route = app.router.add_post(predict_route_path, predict_handler)
health_route  = app.router.add_get(health_route_path,  health_handler)
cors.add(predict_route)
cors.add(health_route)

if __name__ == "__main__":
    port = int(os.environ.get("AIP_HTTP_PORT", 8080))
    logger.info(f"Starting GNN serving endpoint on :{port}")
    web.run_app(app, port=port)
