"""Spanner ingest helpers for the local_monitor Streamlit app.

Mirrors §3 of `embedding_exploration.ipynb` but uses Streamlit's `st.cache_data`
so refresh logic lives in the framework rather than a pickle file. The cache
key is `(num_snapshots, latest_ts)` — exactly the notebook's invariant.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import torch
from google.cloud import spanner

# Make gnn/src importable so we share the canonical schema constants
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR  = _THIS_DIR.parent.parent / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils.gnn_utils import ANOMALY_THRESHOLDS, NODE_TYPES  # noqa: E402

# Auto-set credentials when the local SA key exists (same convention as notebook)
_creds = str(_SRC_DIR / 'networkagent.json')
if os.path.exists(_creds):
    os.environ.setdefault('GOOGLE_APPLICATION_CREDENTIALS', _creds)

# Threshold source files in priority order:
#   1. deployed_model_stats.pth — pulled from the model currently serving on
#      gnn-endpoint (matches what production actually flags). The "Pull from
#      deployed model" sidebar button refreshes it.
#   2. model_stats.pth — written by test_model_training.py / KFP local. Useful
#      for synthetic / sensitivity testing against your locally trained model.
# GNN_MODEL_STATS env var overrides both.
_DEPLOYED_STATS_PATH = _THIS_DIR.parent / 'notebook_artifacts' / 'deployed_model_stats.pth'
_LOCAL_STATS_PATH    = _THIS_DIR.parent / 'notebook_artifacts' / 'model_stats.pth'

# Vertex AI endpoint that runs the production model (same id used elsewhere).
GNN_ENDPOINT_ID = os.getenv('GNN_ENDPOINT_ID', '8084605644943917056')


def pull_deployed_thresholds(endpoint_id: str = None, dest: Path = None) -> Path:
    """Download model_stats.pth from the artefact dir of the model currently
    deployed on `gnn-endpoint`. Used by the "Pull from deployed model" sidebar
    button to refresh the dashboard's thresholds when production changes.

    Returns the local path the file was written to.
    """
    from google.cloud import aiplatform, storage  # lazy import (heavy)
    cfg = get_config()
    endpoint_id = endpoint_id or GNN_ENDPOINT_ID
    dest = Path(dest or _DEPLOYED_STATS_PATH)
    aiplatform.init(project=cfg['project'], location=os.getenv('GOOGLE_REGION', 'europe-west1'))
    ep = aiplatform.Endpoint(endpoint_name=endpoint_id)
    deployed = ep.gca_resource.deployed_models
    if not deployed:
        raise RuntimeError(f'No models deployed on endpoint {endpoint_id}')
    model_resource = deployed[0].model  # e.g. projects/.../models/<id>
    model_obj = aiplatform.Model(model_name=model_resource)
    artefact_uri = model_obj.gca_resource.artifact_uri
    if not artefact_uri or not artefact_uri.startswith('gs://'):
        raise RuntimeError(f'Deployed model has no GCS artefactUri (got {artefact_uri!r})')
    bucket_name, _, prefix = artefact_uri[5:].partition('/')
    blob_path = f"{prefix.rstrip('/')}/model_stats.pth"
    storage.Client(project=cfg['project']).bucket(bucket_name).blob(blob_path).download_to_filename(str(dest))
    return dest


def get_config() -> dict:
    return {
        'project':  os.getenv('GOOGLE_PROJECT',    'gnn-networks'),
        'instance': os.getenv('SPANNER_INSTANCE',  'networktopology-instance'),
        'database': os.getenv('SPANNER_DATABASE',  'transporttopology-db'),
    }


@st.cache_resource(show_spinner=False)
def load_thresholds() -> tuple[dict, dict]:
    """Load per-type calibrated thresholds. Priority: GNN_MODEL_STATS env →
    deployed_model_stats.pth (matches production) → model_stats.pth (local
    train) → hardcoded ANOMALY_THRESHOLDS (legacy fallback).

    Returns (thresholds, meta):
      thresholds = {node_type: float}
      meta       = {'source', 'origin', 'formula', 'scoring',
                    'per_type_stats', 'is_fallback'}
    """
    env_path = os.getenv('GNN_MODEL_STATS')
    if env_path:
        path, origin = Path(env_path), 'env GNN_MODEL_STATS'
    elif _DEPLOYED_STATS_PATH.exists():
        path, origin = _DEPLOYED_STATS_PATH, 'deployed model (GCS snapshot)'
    elif _LOCAL_STATS_PATH.exists():
        path, origin = _LOCAL_STATS_PATH, 'local train (notebook_artifacts)'
    else:
        path, origin = _LOCAL_STATS_PATH, 'missing'

    def _fallback(reason: str) -> tuple[dict, dict]:
        return dict(ANOMALY_THRESHOLDS), {
            'source':         f'fallback ({reason})',
            'origin':         'hardcoded',
            'formula':        'gnn_utils.ANOMALY_THRESHOLDS',
            'scoring':        'MSE (legacy)',
            'per_type_stats': {},
            'is_fallback':    True,
        }

    if not path.exists():
        return _fallback(f'no model_stats.pth on disk — run test_model_training.py or pull from GCS')

    try:
        stats = torch.load(str(path), map_location='cpu', weights_only=False)
        cs = stats.get('cluster_stats') or {}
        if not cs or not any('calibrated_threshold' in d for d in cs.values()):
            return _fallback(f'{path.name} has no cluster_stats — older MSE model')

        thresholds = {nt: float(cs[nt]['calibrated_threshold']) for nt in cs}
        per_type_stats = {
            nt: {
                'mean':    float(cs[nt].get('healthy_d_mean', float('nan'))),
                'std':     float(cs[nt].get('healthy_d_std',  float('nan'))),
                'max':     float(cs[nt].get('healthy_d_max',  float('nan'))),
                'samples': int(cs[nt].get('sample_count', 0)),
            }
            for nt in cs
        }
        # Best-effort formula inference: if every threshold is close to
        # max_healthy * 1.2, that's the old `max(P99.73, max_healthy*1.2)`
        # formula. Otherwise we assume the newer mean+K*std with per-type floor.
        ratios = [thresholds[nt] / per_type_stats[nt]['max']
                  for nt in thresholds if per_type_stats[nt]['max'] > 0]
        if ratios and all(abs(r - 1.2) < 0.05 for r in ratios):
            formula = 'max(P99.73, max_healthy × 1.2)  [legacy]'
        else:
            formula = 'max(mean + 2σ, max_healthy × floor_mult)  [floor_mult: vpn=1.5, others=1.2]'
        return thresholds, {
            'source':         str(path),
            'origin':         origin,
            'formula':        formula,
            'scoring':        'Mahalanobis D_M (encoder embedding)',
            'per_type_stats': per_type_stats,
            'is_fallback':    False,
        }
    except Exception as exc:  # noqa: BLE001
        return _fallback(f'failed to load {path.name}: {exc}')


def _database():
    cfg = get_config()
    client = spanner.Client(project=cfg['project'])
    return client.instance(cfg['instance']).database(cfg['database'])


def _parse_explanation(raw):
    """Spanner JSON columns can come back as str or dict depending on driver version."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


# ttl=30 so the auto-refresh loop (default 60 s) always sees a non-stale value of
# MAX(timestamp). When latest_ts hasn't changed, the row-fetch cache below still
# hits — so re-checking is cheap (one tiny aggregate query, no row scan).
@st.cache_data(show_spinner=False, ttl=30)
def latest_timestamp() -> pd.Timestamp | None:
    """Cheap aggregate query — used as part of the cache key for the row fetch."""
    db = _database()
    with db.snapshot() as snap:
        for row in snap.execute_sql('SELECT MAX(timestamp) FROM NodeEmbedding'):
            return row[0]
    return None


# The cache key is (num_snapshots, latest_ts) — same invariant as the notebook.
# When latest_ts changes (new inference rows land in Spanner), this cache misses
# and we re-fetch automatically.
# NB: do NOT prefix latest_ts with an underscore — Streamlit's @st.cache_data
# excludes underscore-prefixed args from the cache key (intended for non-hashable
# args like DB connections). With the underscore, this function would return
# stale data forever even as latest_ts advances.
@st.cache_data(show_spinner='Fetching NodeEmbedding rows from Spanner…')
def fetch_embeddings(num_snapshots: int, latest_ts) -> pd.DataFrame:
    db = _database()
    query = f"""
        SELECT timestamp, node_id, node_type, hetgnn_score, hetgnn_embedding, anomaly_explanation
        FROM NodeEmbedding
        WHERE timestamp IN (
            SELECT DISTINCT timestamp
            FROM NodeEmbedding
            ORDER BY timestamp DESC
            LIMIT {int(num_snapshots)}
        )
        ORDER BY timestamp ASC, node_id ASC
    """
    rows = []
    with db.snapshot() as snap:
        for r in snap.execute_sql(query):
            rows.append({
                'timestamp':   r[0],
                'node_id':     r[1],
                'node_type':   r[2],
                'score':       float(r[3]) if r[3] is not None else np.nan,
                'embedding':   np.asarray(r[4], dtype=np.float32),
                'explanation': _parse_explanation(r[5]),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Sanity checks consistent with the notebook
    emb_dims = {len(e) for e in df['embedding']}
    if len(emb_dims) != 1:
        raise ValueError(f'Inconsistent embedding dims across rows: {emb_dims}')

    return df


def _annotate_thresholds(df: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """Attach `threshold`, `score_norm`, `is_anomaly` columns from the current
    threshold dict. Kept outside the cached fetch so a threshold reload (new
    model_stats.pth) takes effect without re-pulling Spanner."""
    if df.empty:
        return df
    df = df.copy()
    df['threshold']  = df['node_type'].map(thresholds)
    df['score_norm'] = df['score'] / df['threshold']
    df['is_anomaly'] = df['score'] > df['threshold']
    return df


def load(num_snapshots: int) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Convenience wrapper: returns (df, latest_ts). Honours both Streamlit caches."""
    ts = latest_timestamp()
    if ts is None:
        return pd.DataFrame(), None
    df = fetch_embeddings(num_snapshots, ts)
    thresholds, _ = load_thresholds()
    return _annotate_thresholds(df, thresholds), ts
