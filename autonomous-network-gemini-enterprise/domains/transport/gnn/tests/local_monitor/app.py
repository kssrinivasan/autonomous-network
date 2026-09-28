"""local_monitor — Streamlit dashboard over the NodeEmbedding table.

Sections (mirroring `embedding_exploration.ipynb`):
  1. Threshold calibration         (notebook §4)
  2. Score evolution               (notebook §5)
  3. Score heatmap                 (notebook §6)
  4. Anomaly deep-dive             (notebook §11)

Run from repo root:
  streamlit run gnn/tests/local_monitor/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh


def _fmt_ts(ts) -> str:
    """`2026-06-04 11:26:03 UTC` — drops microseconds and tz suffix noise."""
    return ts.strftime('%Y-%m-%d %H:%M:%S UTC')


def _fmt_window(t0, t1) -> str:
    """`2026-06-04 06:25:03 → 11:26:03 UTC  (5h 1m)` — collapses same-day endpoints.

    Keeps seconds so the right endpoint matches the "Latest snapshot" line above
    (cadence is ~60s, so seconds are meaningful).
    """
    delta_s = int((t1 - t0).total_seconds())
    h, m = divmod(delta_s // 60, 60)
    if t0.date() == t1.date():
        return f"{t0.strftime('%Y-%m-%d %H:%M:%S')} → {t1.strftime('%H:%M:%S')} UTC  ({h}h {m}m)"
    return f"{t0.strftime('%Y-%m-%d %H:%M:%S')} → {t1.strftime('%Y-%m-%d %H:%M:%S')} UTC  ({h}h {m}m)"


def _age(ts) -> str:
    """`12s ago` / `4m ago` / `2h 14m ago` — staleness check for the live dashboard."""
    now = pd.Timestamp.utcnow()
    if getattr(ts, 'tzinfo', None) is not None and now.tzinfo is None:
        now = now.tz_localize('UTC')
    s = max(0, int((now - ts).total_seconds()))
    if s < 60:    return f'{s}s ago'
    if s < 3600:  return f'{s // 60}m ago'
    return f'{s // 3600}h {(s % 3600) // 60}m ago'

# Make sibling modules importable when streamlit launches us
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import data
import plots
from utils.gnn_utils import NODE_TYPES  # noqa: E402

st.set_page_config(page_title='GNN Embedding Monitor', layout='wide', initial_sidebar_state='expanded')

# ── Sidebar ─────────────────────────────────────────────────────────────────
st.sidebar.title('GNN Embedding Monitor')
cfg = data.get_config()
st.sidebar.caption(f"`{cfg['project']}` / `{cfg['instance']}` / `{cfg['database']}`")

num_snapshots = st.sidebar.number_input(
    'Snapshots to load',
    min_value=10, max_value=2000, value=300, step=50,
    help='How many distinct timestamps to pull from NodeEmbedding (most recent).',
)

st.sidebar.divider()
st.sidebar.caption('**Live updates**')
live_on = st.sidebar.toggle('Auto-refresh', value=True,
                            help='Re-poll Spanner on serve.py\'s cadence so the dashboard tracks new inference runs.')
refresh_secs = st.sidebar.slider('Interval (seconds)', min_value=15, max_value=300, value=60, step=15,
                                 help='Match Cloud Scheduler\'s serve.py cadence (default 60 s).')
if live_on:
    tick = st_autorefresh(interval=refresh_secs * 1000, key='live_tick')
    st.sidebar.caption(f'tick #{tick}  ·  next in ≤ {refresh_secs}s')

if st.sidebar.button('🔄 Refresh data now', help='Clears the Spanner cache and re-fetches immediately'):
    data.latest_timestamp.clear()
    data.fetch_embeddings.clear()
    st.rerun()

st.sidebar.divider()
thresholds, thr_meta = data.load_thresholds()
st.sidebar.caption(f"**Thresholds — {thr_meta['scoring']}**")
thr_rows = []
for nt in NODE_TYPES:
    if nt not in thresholds:
        continue
    s = thr_meta['per_type_stats'].get(nt, {})
    thr_rows.append({
        'type':  nt,
        'thr':   f"{thresholds[nt]:.2f}",
        'mean':  f"{s.get('mean', float('nan')):.2f}",
        'σ':     f"{s.get('std',  float('nan')):.2f}",
        'n':     s.get('samples', '?'),
    })
if thr_rows:
    st.sidebar.dataframe(pd.DataFrame(thr_rows), hide_index=True, use_container_width=True)
else:
    st.sidebar.json(thresholds, expanded=True)
st.sidebar.caption(f"_formula:_ `{thr_meta['formula']}`")
st.sidebar.caption(f"_origin:_ `{thr_meta.get('origin', 'unknown')}`")
src = thr_meta['source']
if thr_meta.get('is_fallback'):
    st.sidebar.warning(f"⚠️ Using fallback thresholds — {src}")
else:
    st.sidebar.caption(f"_file:_ `{Path(src).name}`")
col_r, col_p = st.sidebar.columns(2)
with col_r:
    if st.button('Reload', help='Re-read whichever model_stats.pth is on disk'):
        data.load_thresholds.clear()
        st.rerun()
with col_p:
    if st.button('Pull deployed', help='Download model_stats.pth from the model currently serving on gnn-endpoint'):
        try:
            with st.spinner('Downloading deployed model_stats.pth from GCS…'):
                dest = data.pull_deployed_thresholds()
            data.load_thresholds.clear()
            st.sidebar.success(f'Pulled → {dest.name}')
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f'Pull failed: {exc}')

# ── Load data ───────────────────────────────────────────────────────────────
df, latest_ts = data.load(num_snapshots)

if df.empty:
    st.error('NodeEmbedding table is empty or unreachable. Check Spanner credentials and project.')
    st.stop()

st.sidebar.divider()
st.sidebar.metric('Rows',         f'{len(df):,}')
st.sidebar.metric('Snapshots',    f'{df["timestamp"].nunique():,}')
st.sidebar.metric('Unique nodes', f'{df["node_id"].nunique():,}')
st.sidebar.metric('Anomalous %',  f'{df["is_anomaly"].mean() * 100:.2f}%')

# ── Header ──────────────────────────────────────────────────────────────────
st.title('GNN Embedding Monitor')
hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    st.markdown(
        f'📡 &nbsp;**Latest snapshot:** &nbsp;`{_fmt_ts(latest_ts)}` &nbsp;·&nbsp; *{_age(latest_ts)}*'
    )
with hdr_r:
    st.caption(
        f'**Window:** {_fmt_window(df["timestamp"].min(), df["timestamp"].max())}  '
        f'·  embedding dim = `{len(df["embedding"].iloc[0])}`'
    )

# ── §1. Threshold calibration ───────────────────────────────────────────────
st.header('1. Threshold calibration')
st.markdown(
    f'Per-type score distribution with the **calibrated** threshold '
    f"(`{thr_meta['formula']}`) as a vertical line. Loaded from "
    f'`model_stats.pth` rather than hardcoded — re-runs of `test_model_training.py` '
    'or a fresh pipeline rebuild will shift the line automatically.'
)
st.pyplot(plots.plot_threshold_calibration(df), clear_figure=True)
st.subheader('Per-type summary')
st.dataframe(plots.threshold_summary(df), use_container_width=True)

# ── §2. Score evolution ─────────────────────────────────────────────────────
st.header('2. Anomaly score over time')
st.markdown(
    'Scores normalised by per-type calibrated threshold so `y=1.0` is the '
    'boundary across every node type. `symlog` y-axis handles the wide '
    'dynamic range of D_M values.'
)
st.pyplot(plots.plot_score_evolution(df), clear_figure=True)

# ── §3. Score heatmap ───────────────────────────────────────────────────────
st.header('3. Score heatmap (max, threshold-normalised)')

st.subheader('3a. Raw per-snapshot median — typical level')
st.markdown(
    'Median `score / threshold` per `(timestamp, node_type)`. Robust to single '
    "spikes — shows the *typical* score for each type per snapshot rather than the worst node. "
    'Use this view to spot population-wide drift or background-level shifts that would be hidden by the max view.'
)
st.pyplot(plots.plot_score_heatmap_median(df), clear_figure=True)

st.subheader('3b. Raw per-snapshot max — instantaneous spikes')
st.markdown(
    'Max `score / threshold` per `(timestamp, node_type)`. `max` rather than mean '
    "because anomalies are spikes — a single screaming node shouldn't be hidden by 99 healthy ones."
)
st.pyplot(plots.plot_score_heatmap(df), clear_figure=True)

st.subheader('3c. Rolling N=10 mean — sustained anomalies')
st.markdown(
    'Rolling mean over the last 10 snapshots of `max(score / threshold)` per '
    '`(timestamp, node_type)`. Smooths transient noise so persistent elevation '
    'stays visible — use this view to spot sustained anomalies vs one-off spikes.'
)
st.pyplot(plots.plot_score_heatmap_rolling(df, n=10), clear_figure=True)

# ── §4. Anomaly deep-dive ───────────────────────────────────────────────────
st.header('4. Anomaly deep-dive')
st.markdown(
    'Two rankings: worst single-snapshot spike vs. persistence above threshold. '
    'Pick a node below to see its score timeseries and top reconstruction-error features at its worst moment.'
)

top_k = st.slider('Top-K per ranking', min_value=3, max_value=25, value=5, step=1)
worst_df = plots.rank_worst_spike(df, top_k)
pers_df  = plots.rank_persistence(df, top_k)

c1, c2 = st.columns(2)
with c1:
    st.subheader(f'Top {top_k} by worst score ratio')
    st.dataframe(worst_df, use_container_width=True, hide_index=True)
with c2:
    st.subheader(f'Top {top_k} by # snapshots above threshold')
    st.dataframe(pers_df, use_container_width=True, hide_index=True)

# Candidate set = union of the two top-K lists
candidates = list(dict.fromkeys(worst_df['node_id'].tolist() + pers_df['node_id'].tolist()))
if not candidates:
    st.info('No anomalous nodes in the current window.')
else:
    node_id = st.selectbox('Inspect a node', candidates, index=0)
    fig, summary = plots.plot_node_timeseries(df, node_id)
    if fig is None:
        st.warning(f'No rows for {node_id}.'); st.stop()

    st.pyplot(fig, clear_figure=True)

    info_l, info_r = st.columns([1, 2])
    with info_l:
        st.metric('Type',           summary['type'])
        st.metric('Threshold (D_M)', f"{summary['threshold']:.2f}")
        st.metric('Worst ratio',    f"{summary['ratio']:.2f}×")
        st.metric('Snaps above thr', f"{summary['snaps_above']} / {summary['snaps_total']}")
        st.caption(f"Worst snapshot: `{summary['worst_ts']}`")
        st.caption(f"Worst score: `{summary['worst_score']:.4f}`  "
                   f"·  mean: `{summary['mean_score']:.4f}`")
    with info_r:
        st.subheader('Top reconstruction-error features at worst snapshot')
        feats = summary['top_features']
        if feats:
            st.dataframe(
                {'feature': [f.get('feature', '?') for f in feats],
                 'MSE':     [f.get('mse', float('nan')) for f in feats]},
                use_container_width=True, hide_index=True,
            )
        else:
            st.info('No `top_features` in the explanation JSON for this snapshot — '
                    'this means the node was NOT anomalous at its worst moment '
                    'in the loaded window (serve.py only emits feature breakdowns for anomalous nodes).')
