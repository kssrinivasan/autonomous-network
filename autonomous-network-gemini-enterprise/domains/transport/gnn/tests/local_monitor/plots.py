"""Matplotlib renderers for local_monitor.

Each function returns a `matplotlib.figure.Figure` that the Streamlit app
displays with `st.pyplot(fig)`. Logic is lifted directly from §4, §5, §6, §11
of `embedding_exploration.ipynb` to stay 1:1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

_SRC_DIR = Path(__file__).resolve().parent.parent.parent / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
from utils.gnn_utils import NODE_TYPES  # noqa: E402

sns.set_theme(style='whitegrid')
TYPE_PALETTE = {nt: c for nt, c in zip(NODE_TYPES, sns.color_palette('tab10', len(NODE_TYPES)))}


def _thresholds() -> dict:
    """Dynamic per-type thresholds from data.load_thresholds().
    Lazy local import to avoid a circular dependency at module load."""
    import data  # type: ignore[import-not-found]
    thrs, _ = data.load_thresholds()
    return thrs


# ── §4. Threshold calibration ────────────────────────────────────────────────

def plot_threshold_calibration(df: pd.DataFrame):
    thresholds = _thresholds()
    fig, axes = plt.subplots(1, len(NODE_TYPES), figsize=(5 * len(NODE_TYPES), 4), sharey=False)
    for ax, nt in zip(axes, NODE_TYPES):
        scores = df.loc[df['node_type'] == nt, 'score'].dropna()
        if scores.empty:
            ax.text(0.5, 0.5, f'no {nt} scores', ha='center', va='center', transform=ax.transAxes)
            ax.set_title(nt); continue
        thresh = thresholds.get(nt)
        if thresh is None:
            ax.text(0.5, 0.5, f'no threshold for {nt}', ha='center', va='center', transform=ax.transAxes)
            ax.set_title(nt); continue
        pct_anom = (scores > thresh).mean() * 100
        bins = np.linspace(0, max(scores.max(), thresh * 1.5), 60)
        ax.hist(scores.clip(upper=bins[-1]), bins=bins, color='steelblue', alpha=0.85, edgecolor='none')
        ax.axvline(thresh, color='crimson', linestyle='--', linewidth=1.5,
                   label=f'threshold={thresh:.2f}\n{pct_anom:.2f}% above')
        ax.set_title(f'{nt}  (n={len(scores):,})')
        ax.set_xlabel('hetgnn_score (D_M)')
        ax.set_ylabel('count')
        ax.set_yscale('log')
        ax.legend(fontsize=8, loc='upper right')
    fig.suptitle('Score distribution per node type with calibrated threshold', y=1.02)
    fig.tight_layout()
    return fig


def threshold_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Table accompanying the calibration plot."""
    thresholds = _thresholds()
    summary = (df.groupby('node_type')['score']
                 .agg(['count', 'mean', 'std',
                       lambda s: s.quantile(0.50),
                       lambda s: s.quantile(0.95),
                       lambda s: s.quantile(0.99),
                       'max'])
                 .rename(columns={'<lambda_0>': 'p50', '<lambda_1>': 'p95', '<lambda_2>': 'p99'}))
    summary['threshold']   = summary.index.map(thresholds)
    summary['%_above_thr'] = df.groupby('node_type')['is_anomaly'].mean() * 100
    return summary.reindex(NODE_TYPES).dropna(how='all')


# ── §5. Score evolution over time ────────────────────────────────────────────

def plot_score_evolution(df: pd.DataFrame):
    fig, axes = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    for nt in NODE_TYPES:
        sub = df[df['node_type'] == nt]
        if sub.empty: continue
        g = sub.groupby('timestamp')['score_norm']
        mx, p95, p50 = g.max(), g.quantile(0.95), g.quantile(0.50)
        axes[0].plot(mx.index,  mx.values,  color=TYPE_PALETTE[nt], label=f'{nt} max', linewidth=1.2)
        axes[0].plot(p95.index, p95.values, color=TYPE_PALETTE[nt], linestyle=':',  linewidth=0.8, alpha=0.8)
        axes[1].plot(p50.index, p50.values, color=TYPE_PALETTE[nt], label=f'{nt} p50', linewidth=1.0)
    for ax, title in zip(axes, ['Max (solid) + p95 (dotted) per type', 'Median per type']):
        ax.axhline(1.0, color='gray', linestyle='--', alpha=0.6,
                   label='threshold (= 1.0 normalised)')
        ax.set_yscale('symlog', linthresh=1e-4)
        ax.set_ylabel('score / threshold')
        ax.set_title(title)
        ax.legend(fontsize=8, ncol=len(NODE_TYPES) + 1, loc='upper right')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    axes[1].set_xlabel('time')
    fig.suptitle('Anomaly score over time (threshold-normalised; y=1 = at threshold)', y=1.005)
    fig.tight_layout()
    return fig


# ── §6. Score heatmap ────────────────────────────────────────────────────────

def _score_heatmap_pivot(df: pd.DataFrame, agg: str = 'max') -> pd.DataFrame:
    """Pivot helper for the heatmap views: per-(timestamp, node_type) aggregate
    of `score_norm`, sorted by time, columns in canonical NODE_TYPES order.

    `agg` is the pandas aggregation: 'max' (default — catches the worst node
    in each type) or 'median' (typical-level view, robust to single spikes).
    """
    grp = df.groupby(['timestamp', 'node_type'])['score_norm']
    pivot = grp.agg(agg).unstack().reindex(columns=NODE_TYPES)
    pivot.columns = [str(c) for c in pivot.columns]
    return pivot.sort_index()


def _draw_heatmap(pivot: pd.DataFrame, ax, cbar_label: str, title: str):
    vmax = max(2.0, np.nan_to_num(pivot.to_numpy(na_value=0)).max())
    # Green→yellow→red gradient: green = well below threshold, yellow = approaching,
    # red = at or above. `RdYlGn_r` is matplotlib's reverse Red-Yellow-Green diverging
    # palette, anchored at 0 (low/safe).
    sns.heatmap(pivot.T, cmap='RdYlGn_r', vmin=0, vmax=vmax, ax=ax,
                cbar_kws={'label': cbar_label})
    n_x = pivot.shape[0]
    step = max(1, n_x // 12)
    ax.set_xticks(range(0, n_x, step))
    ax.set_xticklabels([pivot.index[i].strftime('%H:%M') for i in range(0, n_x, step)],
                       rotation=45, ha='right')
    ax.set_xlabel('timestamp'); ax.set_ylabel('node type')
    ax.set_title(title)


def plot_score_heatmap_rolling(df: pd.DataFrame, n: int = 10):
    """Rolling mean over N snapshots of max(score/threshold). Smooths single-
    snapshot spikes so sustained elevation stays visible vs transient noise.
    `min_periods=1` so early timestamps don't go NaN before the window fills."""
    pivot = _score_heatmap_pivot(df)
    smoothed = pivot.rolling(window=n, min_periods=1).mean()
    fig, ax = plt.subplots(figsize=(15, 4))
    _draw_heatmap(
        smoothed, ax,
        cbar_label=f'rolling-{n} mean of max(score/threshold) — 1.0 = at threshold',
        title=f'Rolling-{n} mean of max threshold-normalised score per (timestamp, node type)',
    )
    fig.tight_layout()
    return fig


def plot_score_heatmap(df: pd.DataFrame):
    pivot = _score_heatmap_pivot(df, agg='max')
    fig, ax = plt.subplots(figsize=(15, 4))
    _draw_heatmap(
        pivot, ax,
        cbar_label='max(score / threshold) — 1.0 = at threshold',
        title='Max threshold-normalised score per (timestamp, node type)',
    )
    fig.tight_layout()
    return fig


def plot_score_heatmap_median(df: pd.DataFrame):
    """Median(score/threshold) per (timestamp, node_type). Robust to outliers —
    shows the typical level for each type per snapshot rather than the worst
    node. Use this view to spot population-wide drift; use the max view to
    catch single-node spikes; use the rolling-mean view for sustained anomalies.
    """
    pivot = _score_heatmap_pivot(df, agg='median')
    fig, ax = plt.subplots(figsize=(15, 4))
    _draw_heatmap(
        pivot, ax,
        cbar_label='median(score / threshold) — 1.0 = at threshold',
        title='Median threshold-normalised score per (timestamp, node type)',
    )
    fig.tight_layout()
    return fig


# ── §11. Deep-dive helpers ──────────────────────────────────────────────────

def rank_worst_spike(df: pd.DataFrame, top_k: int) -> pd.DataFrame:
    return (df.groupby(['node_id', 'node_type'])['score_norm'].max()
              .sort_values(ascending=False).head(top_k)
              .rename('worst_score_norm').reset_index())


def rank_persistence(df: pd.DataFrame, top_k: int) -> pd.DataFrame:
    return (df.groupby(['node_id', 'node_type'])['is_anomaly'].sum()
              .sort_values(ascending=False).head(top_k)
              .rename('snapshots_above_threshold').reset_index())


def plot_node_timeseries(df: pd.DataFrame, node_id: str):
    g = df[df['node_id'] == node_id].sort_values('timestamp')
    if g.empty:
        return None, None
    nt = g['node_type'].iloc[0]
    thr = _thresholds().get(nt, float('nan'))

    fig, ax = plt.subplots(figsize=(13, 3))
    ax.plot(g['timestamp'], g['score'], linewidth=1.2, color=TYPE_PALETTE[nt])
    above = g[g['is_anomaly']]
    if not above.empty:
        ax.scatter(above['timestamp'], above['score'], s=22, color='crimson',
                   label='above threshold', zorder=3)
    ax.axhline(thr, color='gray', linestyle='--', alpha=0.7, label=f'threshold={thr:.2f}')
    ax.set_yscale('symlog', linthresh=1e-4)
    ax.set_title(f'{node_id}  —  score timeseries')
    ax.set_ylabel('hetgnn_score (D_M)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.legend(fontsize=8, loc='upper right')
    fig.tight_layout()

    # Build a small summary dict for the right-hand panel
    worst = g.loc[g['score'].idxmax()]
    expl = worst['explanation'] or {}
    summary = {
        'type':          nt,
        'threshold':     thr,
        'worst_ts':      worst['timestamp'],
        'worst_score':   worst['score'],
        'ratio':         worst['score'] / thr,
        'snaps_above':   int(g['is_anomaly'].sum()),
        'snaps_total':   len(g),
        'mean_score':    g['score'].mean(),
        'max_score':     g['score'].max(),
        'top_features':  expl.get('top_features', []),
    }
    return fig, summary
