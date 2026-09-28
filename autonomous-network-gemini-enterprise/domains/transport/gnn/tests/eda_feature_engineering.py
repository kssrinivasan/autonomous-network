"""
eda_feature_engineering.py — HetGNN training-data EDA + quality report.

Loads snapshots from the on-disk cache (preferred — same cache the training
notebooks write) or fetches a fresh window from Spanner via SpannerDataset.
Then emits a folder of reports/plots:

  - per-snapshot node/edge inventory                (console)
  - per-feature quality report                      (CSV + console)  null%, zero%, mean, std, skew, constant/low-var flags
  - per-feature distribution histograms             (PNG)            continuous + binary on-share bar charts
  - per-feature temporal evolution                  (PNG)            mean ± std + max across nodes per timestamp
  - per-node value heatmaps                         (PNG)            router + vpn full; interface + flow top-N by mean magnitude
  - per-node zero-rate heatmaps                     (PNG)            % zeros per feature per node — catches missing-collector cases
  - down-event visualisations                       (PNG)            router state=0 scatter timeline; interface state=0 timeline heatmap
  - topology inventory + degree distribution        (console + PNG)
  - per-router topology breakdown                   (console)        interface count and peer_of degree per router
  - topology visualisation                          (PNG)            router peer_of graph + router-interface bipartite
  - node-presence completeness                      (console)        nodes appearing in <100 % of snapshots

Schema source of truth: gnn/src/utils/gnn_utils.py
                        (NODE_TYPES, *_FEATURES, FEATURE_DIMS, ANOMALY_THRESHOLDS, EDGE_TYPES)
Snapshot fetch:         gnn/src/utils/data.py
                        (SpannerDataset, SpannerDataset.compute_temporal_features)

Usage:
    # Reuse the cache populated by test_model_training.py / test_kfp_training_local.py
    python gnn/tests/eda_feature_engineering.py \\
        --snapshots-dir gnn/tests/notebook_artifacts/snapshots

    # Fresh fetch by explicit window
    python gnn/tests/eda_feature_engineering.py \\
        --from-time 2026-05-30T20:00:00 --to-time 2026-05-30T21:00:00 \\
        --interval-minutes 0.5

    # Most-recent walk-back (no window)
    python gnn/tests/eda_feature_engineering.py --recent 1h
"""

import argparse
import datetime
import logging
import os
import pickle
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import seaborn as sns

_TESTS_DIR = Path(__file__).resolve().parent
_SRC_DIR   = _TESTS_DIR.parent / "src"
for p in [str(_SRC_DIR), str(_TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from utils.data import SpannerDataset
from utils.gnn_utils import (
    ANOMALY_THRESHOLDS,
    EDGE_TYPES,
    FEATURE_DIMS,
    FLOW_FEATURES,
    INTERFACE_FEATURES,
    NODE_TYPES,
    ROLE_ORDER,
    ROUTER_FEATURES,
    VPN_FEATURES,
    _role_onehot,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("eda")

pd.set_option("display.float_format", "{:.4f}".format)
sns.set_theme(style="whitegrid", palette="muted")

# ── Feature catalogue ────────────────────────────────────────────────────────
# Canonical lists (model inputs) keyed by node type
FEATURES: Dict[str, List[str]] = {
    "router":    ROUTER_FEATURES,
    "interface": INTERFACE_FEATURES,
    "vpn":       VPN_FEATURES,
    "flow":      FLOW_FEATURES,
}

# Subset that is binary {0,1} — treat differently in distribution plots
BINARY_FEATURES: Dict[str, List[str]] = {
    "router":    ["state", "role_P", "role_PE", "role_RR", "role_CE"],
    "interface": ["state", "carrier_state"],
    "vpn":       ["state"],
    "flow":      ["is_running", "is_bidirectional"],
}

def _continuous(ntype: str) -> List[str]:
    return [f for f in FEATURES[ntype] if f not in BINARY_FEATURES.get(ntype, [])]


# ── Snapshot loading ─────────────────────────────────────────────────────────

def _parse_recent(recent_str: str) -> int:
    """Parse a duration string like '2h', '30m', '1h30m' into total minutes."""
    total = 0
    for val, unit in re.findall(r"(\d+)([hm])", recent_str.lower()):
        total += int(val) * 60 if unit == "h" else int(val)
    if total == 0:
        raise ValueError(f"Cannot parse duration '{recent_str}'. Use e.g. '2h', '30m', '1h30m'.")
    return total


def load_snapshots(
    snapshots_dir: Optional[Path],
    from_time: Optional[datetime.datetime],
    to_time: Optional[datetime.datetime],
    interval_minutes: float,
    project: str,
    spanner_instance: str,
    spanner_database: str,
    recent_minutes: Optional[int] = None,
) -> List[Dict]:
    """Cache-first snapshot loader.

    1. If ``snapshots_dir`` exists and contains any ``snapshot_*.pkl``, load those.
    2. Otherwise fetch from Spanner using SpannerDataset and compute temporal features.
       The fetched snapshots are NOT written to disk — that's the training pipeline's
       job. EDA stays read-only with respect to the cache.
    """
    if snapshots_dir and snapshots_dir.exists():
        pkls = sorted(snapshots_dir.glob("snapshot_*.pkl"))
        if pkls:
            log.info(f"Loading {len(pkls)} cached snapshots from {snapshots_dir}")
            return [pickle.load(open(p, "rb")) for p in pkls]

    # Resolve fetch window
    if recent_minutes and not (from_time and to_time):
        num_snapshots = max(1, int(recent_minutes / interval_minutes))
        from_time = to_time = None
    elif from_time and to_time:
        num_snapshots = max(1, int((to_time - from_time).total_seconds() / (interval_minutes * 60)))
    else:
        num_snapshots = 60  # default fallback

    log.info(
        f"No cache — fetching {num_snapshots} snapshots from Spanner "
        f"({spanner_instance}/{spanner_database}, interval={interval_minutes}m)"
    )
    dataset = SpannerDataset(
        instance_id=spanner_instance,
        database_id=spanner_database,
        num_snapshots=num_snapshots,
        interval_minutes=interval_minutes,
        project_id=project,
        from_time=from_time,
        to_time=to_time,
    )
    snapshots = []
    for ts in dataset._get_timestamps():
        try:
            snap = dataset.fetch_snapshot(ts)
            if snap.get("nodes"):
                snapshots.append(snap)
        except Exception as exc:
            log.warning(f"Skipped snapshot at {ts.isoformat()}: {exc}")

    if not snapshots:
        raise RuntimeError("No snapshots fetched — check Spanner access and time window")

    SpannerDataset.compute_temporal_features(snapshots, interval_seconds=interval_minutes * 60)
    log.info(f"Fetched {len(snapshots)} snapshots; temporal features computed")
    return snapshots


# ── DataFrame extraction ─────────────────────────────────────────────────────

def extract_dataframes(snapshots: List[Dict]) -> Dict[str, pd.DataFrame]:
    """Long-form DataFrame per node type, one row per (node, snapshot).

    Columns: snapshot index `t`, ISO `timestamp`, node `id`, plus every feature
    in FEATURES[ntype]. Router rows include the four `role_*` one-hot columns.
    """
    rows: Dict[str, List[Dict]] = {nt: [] for nt in NODE_TYPES}
    for t, snap in enumerate(snapshots):
        ts = snap.get("timestamp", "")
        for n in snap.get("nodes", []):
            nt = n.get("type")
            if nt not in rows:
                continue
            row = {"t": t, "timestamp": ts, "id": n["id"]}
            if nt == "router":
                role_oh = _role_onehot(n.get("role", ""))
                for i, r in enumerate(ROLE_ORDER):
                    row[f"role_{r}"] = role_oh[i]
                for f in ROUTER_FEATURES:
                    if not f.startswith("role_"):
                        row[f] = n.get(f, np.nan)
                row["hostname"] = n.get("hostname", n["id"])
                row["role"]     = n.get("role", "")
            else:
                for f in FEATURES[nt]:
                    row[f] = n.get(f, np.nan)
                if nt == "interface":
                    row["name"] = n.get("name", "")
                    row["device_id"] = n.get("device_id", "")
            rows[nt].append(row)

    dfs = {nt: pd.DataFrame(rs) for nt, rs in rows.items()}
    for nt, df in dfs.items():
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return dfs


# ── 1. Snapshot inventory (console only) ─────────────────────────────────────

def print_snapshot_inventory(snapshots: List[Dict]) -> None:
    """Per-snapshot node-type counts (with 'up') and edge-relation counts."""
    print("\n" + "=" * 96)
    print("SNAPSHOT INVENTORY")
    print("=" * 96)

    header = f"{'#':>3}  {'timestamp':<26}  " + "  ".join(f"{nt:>13}" for nt in NODE_TYPES) + f"  {'edges':>7}"
    print(header)
    print("-" * len(header))
    for i, snap in enumerate(snapshots):
        ts = snap.get("timestamp", "")
        per_type_str = []
        for nt in NODE_TYPES:
            nodes = [n for n in snap["nodes"] if n["type"] == nt]
            ups   = sum(1 for n in nodes if n.get("state") == 1.0 or n.get("is_running") == 1.0)
            per_type_str.append(f"{ups}/{len(nodes):<3}up")
        rel_counts: Dict[str, int] = {}
        for e in snap["edges"]:
            rel_counts[e["relation"]] = rel_counts.get(e["relation"], 0) + 1
        print(f"{i+1:>3}  {ts[:26]:<26}  " + "  ".join(f"{s:>13}" for s in per_type_str)
              + f"  {len(snap['edges']):>7}")

    # Edge-relation summary across the whole window
    print(f"\n  Edge relations (totals across {len(snapshots)} snapshot(s)):")
    rel_totals: Dict[str, int] = {}
    for snap in snapshots:
        for e in snap["edges"]:
            rel_totals[e["relation"]] = rel_totals.get(e["relation"], 0) + 1
    for (s, rel, d) in EDGE_TYPES:
        total = rel_totals.get(rel, 0)
        flag = "" if total else "  ⚠ no edges"
        print(f"    {s:>10} --{rel:<18}--> {d:<10}  {total:>7}{flag}")


# ── 2. Feature quality report ────────────────────────────────────────────────

def report_feature_quality(dfs: Dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Per-feature: null%, zero%, mean, std, skew, kurtosis, constant flag,
    low-variance flag, outlier count (|z|>3). Writes feature_quality.csv plus
    a console summary highlighting flagged features.
    """
    print("\n" + "=" * 96)
    print("FEATURE QUALITY")
    print("=" * 96)

    rows = []
    flagged: List[str] = []
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty:
            print(f"\n  [{nt}] no data")
            continue
        n_rows  = len(df)
        n_nodes = df["id"].nunique()
        print(f"\n  [{nt}]  rows={n_rows:,}  unique_nodes={n_nodes}")
        for feat in FEATURES[nt]:
            if feat not in df.columns:
                continue
            s = pd.to_numeric(df[feat], errors="coerce")
            null_pct = s.isna().mean() * 100
            zero_pct = (s == 0).mean() * 100
            mean, std = s.mean(), s.std()
            skew = s.skew() if s.notna().any() else np.nan
            kurt = s.kurt() if s.notna().any() else np.nan
            constant  = (std is not np.nan) and (std < 1e-9)
            low_var   = (mean != 0) and (abs(std / mean) < 0.01) if not constant else False
            outliers  = int(((s - mean).abs() > 3 * std).sum()) if std and std > 0 else 0
            rows.append({
                "node_type": nt, "feature": feat,
                "null_%": round(null_pct, 2),
                "zero_%": round(zero_pct, 2),
                "mean":   round(mean, 6) if pd.notna(mean) else np.nan,
                "std":    round(std,  6) if pd.notna(std)  else np.nan,
                "skew":   round(skew, 3) if pd.notna(skew) else np.nan,
                "kurt":   round(kurt, 3) if pd.notna(kurt) else np.nan,
                "outliers_z>3": outliers,
                "constant":     bool(constant),
                "low_variance": bool(low_var),
            })
            if constant:           flagged.append(f"{nt}.{feat}: constant (std<1e-9)")
            elif low_var:          flagged.append(f"{nt}.{feat}: low variance (|std/mean|<0.01)")
            elif zero_pct > 95:    flagged.append(f"{nt}.{feat}: {zero_pct:.0f}% zeros")
            elif null_pct > 5:     flagged.append(f"{nt}.{feat}: {null_pct:.1f}% nulls")

    df_q = pd.DataFrame(rows)
    out_path = out_dir / "feature_quality.csv"
    df_q.to_csv(out_path, index=False)
    log.info(f"Wrote {out_path}")

    if flagged:
        print(f"\n  ⚠ {len(flagged)} feature(s) flagged:")
        for f in flagged:
            print(f"    - {f}")
    else:
        print("\n  ✓ No features flagged.")


# ── 3. Distribution plots ────────────────────────────────────────────────────

def plot_distributions(dfs: Dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Histograms per feature, per node type. Continuous features get mean and
    median lines. Note: ANOMALY_THRESHOLDS apply to per-node reconstruction MSE,
    not to raw features — so reference lines are intentionally omitted here.
    """
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty:
            continue
        cols = _continuous(nt)
        cols = [c for c in cols if c in df.columns]
        if not cols:
            continue
        ncols = 3
        nrows = (len(cols) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols, figsize=(14, 3.2 * nrows))
        axes = np.atleast_2d(axes).flatten()
        for ax, feat in zip(axes, cols):
            vals = pd.to_numeric(df[feat], errors="coerce").dropna()
            if vals.empty:
                ax.text(0.5, 0.5, "no data", ha="center", va="center",
                        transform=ax.transAxes); ax.set_title(feat); continue
            ax.hist(vals, bins=40, color="steelblue", alpha=0.85, edgecolor="none")
            ax.axvline(vals.mean(),   color="crimson", linestyle="--", linewidth=1.2,
                       label=f"mean={vals.mean():.4g}")
            ax.axvline(vals.median(), color="navy",    linestyle=":",  linewidth=1.0,
                       label=f"med={vals.median():.4g}")
            ax.set_title(feat, fontsize=10)
            ax.tick_params(labelsize=7)
            ax.legend(fontsize=6)
        for ax in axes[len(cols):]:
            ax.set_visible(False)
        fig.suptitle(f"{nt}: continuous-feature distributions", fontsize=12, y=1.005)
        plt.tight_layout()
        out = out_dir / f"distributions_{nt}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")

        bins_ = BINARY_FEATURES.get(nt, [])
        bins_ = [b for b in bins_ if b in df.columns]
        if bins_:
            fig, ax = plt.subplots(figsize=(max(4, 1.6 * len(bins_)), 3))
            shares = [pd.to_numeric(df[b], errors="coerce").mean() for b in bins_]
            ax.bar(bins_, shares, color="darkorange", alpha=0.85)
            ax.set_ylim(0, 1)
            ax.set_ylabel("fraction = 1")
            ax.set_title(f"{nt}: binary-feature on-share")
            ax.tick_params(axis="x", rotation=30, labelsize=8)
            for i, v in enumerate(shares):
                ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
            plt.tight_layout()
            out = out_dir / f"binary_{nt}.png"
            fig.savefig(out, dpi=120, bbox_inches="tight")
            plt.close(fig)
            log.info(f"Wrote {out}")


# ── 4. Temporal evolution ────────────────────────────────────────────────────

def plot_temporal_evolution(dfs: Dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Per-feature line plots — mean ± std band PLUS max line across all nodes.

    The max line is critical for anomaly hunting: averages can stay flat while a
    single interface or flow goes wildly out of bounds.
    """
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty or "timestamp" not in df.columns:
            continue
        cols = _continuous(nt)
        cols = [c for c in cols if c in df.columns]
        if not cols:
            continue
        ncols = 2
        nrows = (len(cols) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols, figsize=(15, 2.6 * nrows), sharex=True)
        axes = np.atleast_2d(axes).flatten()
        for ax, feat in zip(axes, cols):
            g = df.groupby("timestamp")[feat].agg(["mean", "std", "max"]).sort_index()
            ax.plot(g.index, g["mean"], color="steelblue", linewidth=1.3, label="mean")
            ax.fill_between(g.index, g["mean"] - g["std"], g["mean"] + g["std"],
                            alpha=0.25, color="steelblue", label="± std")
            ax.plot(g.index, g["max"],  color="crimson",  linewidth=0.9,
                    linestyle="--", label="max")
            ax.set_title(feat, fontsize=10)
            ax.tick_params(labelsize=7)
            ax.legend(fontsize=6, loc="upper right")
        for ax in axes[len(cols):]:
            ax.set_visible(False)
        fig.suptitle(f"{nt}: temporal evolution (mean ± std band + max line)", fontsize=12, y=1.005)
        plt.tight_layout()
        out = out_dir / f"temporal_{nt}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")


# ── 5b. Per-node zero-rate heatmaps ──────────────────────────────────────────

def plot_zero_rate_heatmaps(dfs: Dict[str, pd.DataFrame], out_dir: Path, top_n: int = 25) -> None:
    """Heatmap of zero-rate (% of snapshots a feature was exactly 0) per node × feature.

    A node with a column full of zeros is the loudest signal that a Prometheus
    scrape is broken for that target — louder than a small mean.
    """
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty:
            continue
        cols = _continuous(nt)
        cols = [c for c in cols if c in df.columns]
        if not cols:
            continue
        group_key = "hostname" if nt == "router" and "hostname" in df.columns else "id"

        zero = df.groupby(group_key)[cols].apply(lambda g: (g == 0).mean() * 100)
        if zero.empty:
            continue
        if nt in ("interface", "flow") and len(zero) > top_n:
            # Surface the noisiest nodes — those with the most-frequent zeros
            zero = zero.loc[zero.sum(axis=1).nlargest(top_n).index]
        if nt in ("interface", "flow"):
            zero.index = [str(x).rsplit(":", 1)[-1][:32] for x in zero.index]

        fig, ax = plt.subplots(figsize=(min(20, 1 + 0.7 * len(cols)),
                                        max(3, 0.35 * len(zero) + 1)))
        sns.heatmap(zero, annot=True, fmt=".0f", cmap="YlOrRd",
                    linewidths=0.3, vmin=0, vmax=100, ax=ax,
                    cbar_kws={"label": "% snapshots = 0"})
        title = f"{nt}: zero-rate per feature per node  (% of snapshots = 0)"
        if nt in ("interface", "flow") and len(df[group_key].unique()) > top_n:
            title += f"  — top {top_n} noisiest"
        ax.set_title(title, fontsize=11)
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.tick_params(axis="y", labelsize=7)
        plt.tight_layout()
        out = out_dir / f"zerorate_{nt}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")


# ── 5c. Down-event visualisations ────────────────────────────────────────────

def plot_down_events(dfs: Dict[str, pd.DataFrame], out_dir: Path, top_n: int = 25) -> None:
    """Render down events for any node type whose schema includes a `state` feature.

    - router → scatter (hostname × time, marker where state == 0)
    - interface → top-N most-down interfaces shown as a 0/1 timeline heatmap
    """
    # Router scatter
    df_r = dfs["router"]
    if not df_r.empty and "state" in df_r.columns:
        down = df_r[df_r["state"] == 0.0]
        hosts = sorted(df_r["hostname"].dropna().unique()) if "hostname" in df_r.columns \
                else sorted(df_r["id"].unique())
        y_map = {h: i for i, h in enumerate(hosts)}
        fig, ax = plt.subplots(figsize=(14, max(3, 0.3 * len(hosts) + 1)))
        if not down.empty:
            key = "hostname" if "hostname" in down.columns else "id"
            ax.scatter(down["timestamp"], down[key].map(y_map),
                       s=22, color="red", marker="x", zorder=3, label="state=0")
            ax.legend(loc="upper right", fontsize=8)
        else:
            ax.text(0.5, 0.5, "No router down events in this window",
                    ha="center", va="center", transform=ax.transAxes, color="gray")
        ax.set_yticks(range(len(hosts)))
        ax.set_yticklabels(hosts, fontsize=8)
        ax.set_title("Router down events (state == 0)", fontsize=11)
        ax.set_xlabel("Timestamp")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out = out_dir / "down_events_router.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")

    # Interface 0/1 timeline heatmap
    df_i = dfs["interface"]
    if not df_i.empty and "state" in df_i.columns:
        down_counts = df_i[df_i["state"] == 0.0].groupby("id").size()
        if down_counts.empty:
            log.info("No interface down events in this window — skipping heatmap")
            return
        worst_ids = down_counts.sort_values(ascending=False).head(top_n).index.tolist()
        pivot = (df_i[df_i["id"].isin(worst_ids)]
                 .pivot_table(index="id", columns="timestamp", values="state", aggfunc="min"))
        pivot.index = [str(x).rsplit(":", 1)[-1][:40] for x in pivot.index]
        pivot.columns = [str(c)[:19] for c in pivot.columns]

        fig, ax = plt.subplots(figsize=(min(20, 0.4 * len(pivot.columns) + 4),
                                        max(3, 0.35 * len(pivot) + 1)))
        sns.heatmap(pivot, cmap="RdYlGn", vmin=0, vmax=1, ax=ax,
                    cbar_kws={"label": "state (1=up, 0=down)"}, linewidths=0)
        ax.set_title(f"Interface state timeline (top {len(pivot)} most-down)", fontsize=11)
        ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right", fontsize=6)
        ax.set_yticklabels(ax.get_yticklabels(), fontsize=7)
        plt.tight_layout()
        out = out_dir / "down_events_interface.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")


# ── 5. Per-node heatmaps ─────────────────────────────────────────────────────

def plot_per_node_heatmaps(dfs: Dict[str, pd.DataFrame], out_dir: Path, top_n: int = 25) -> None:
    """Heatmap of mean(feature) per node × feature.

    - router / vpn → show every node (typically small).
    - interface / flow → show top-N by total magnitude (sum of |mean| values).
    """
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty:
            continue
        cols = _continuous(nt)
        cols = [c for c in cols if c in df.columns]
        if not cols:
            continue

        group_key = {
            "router":    "hostname",
            "interface": "id",
            "vpn":       "id",
            "flow":      "id",
        }[nt]
        if group_key not in df.columns:
            group_key = "id"

        mat = df.groupby(group_key)[cols].mean()
        if mat.empty:
            continue
        if nt in ("interface", "flow") and len(mat) > top_n:
            magnitude = mat.abs().sum(axis=1)
            mat = mat.loc[magnitude.nlargest(top_n).index]

        # Trim long ID labels for interface/flow
        if nt in ("interface", "flow"):
            mat.index = [str(x).rsplit(":", 1)[-1][:32] for x in mat.index]

        fig, ax = plt.subplots(figsize=(min(20, 1 + 0.7 * len(cols)),
                                        max(3, 0.35 * len(mat) + 1)))
        sns.heatmap(mat, annot=True, fmt=".3g", cmap="viridis",
                    linewidths=0.3, ax=ax,
                    cbar_kws={"label": "mean across snapshots"})
        title = f"{nt}: per-node mean feature value"
        if nt in ("interface", "flow") and len(df.groupby(group_key)) > top_n:
            title += f"  (top {top_n} by total magnitude)"
        ax.set_title(title, fontsize=11)
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.tick_params(axis="y", labelsize=7)
        plt.tight_layout()
        out = out_dir / f"heatmap_{nt}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")


# ── 6. Topology inventory + degree distribution ──────────────────────────────

def plot_topology(snapshots: List[Dict], out_dir: Path) -> None:
    """Edge counts by relation (latest snapshot) + degree distribution per node type."""
    latest = snapshots[-1]
    edges  = latest["edges"]
    nodes  = latest["nodes"]
    id_to_type = {n["id"]: n["type"] for n in nodes}

    rel_counts: Dict[str, int] = {}
    for e in edges:
        rel_counts[e["relation"]] = rel_counts.get(e["relation"], 0) + 1

    print("\n" + "=" * 96)
    print(f"TOPOLOGY  (latest snapshot: {latest.get('timestamp', '?')})")
    print("=" * 96)
    print(f"  Nodes: {len(nodes)}   Edges: {len(edges)}")
    print(f"\n  Edge relations:")
    for (s, rel, d) in EDGE_TYPES:
        cnt = rel_counts.get(rel, 0)
        flag = "" if cnt else "  ⚠"
        print(f"    {s:>10} --{rel:<18}--> {d:<10}  {cnt:>5}{flag}")

    # Degree per node, grouped by type
    degs: Dict[str, int] = {}
    for e in edges:
        degs[e["source"]] = degs.get(e["source"], 0) + 1
        degs[e["target"]] = degs.get(e["target"], 0) + 1
    deg_df = pd.DataFrame([
        {"id": nid, "type": id_to_type.get(nid, "?"), "degree": d}
        for nid, d in degs.items()
    ])

    fig, axes = plt.subplots(1, 2, figsize=(14, 4))
    if not deg_df.empty:
        sns.boxplot(data=deg_df, x="type", y="degree", order=NODE_TYPES, ax=axes[0],
                    palette="Set2")
        axes[0].set_yscale("symlog")
        axes[0].set_title("Degree distribution (latest snapshot)")
    axes[1].bar(list(rel_counts.keys()), list(rel_counts.values()), color="teal", alpha=0.8)
    axes[1].set_title("Edges per relation (latest snapshot)")
    axes[1].tick_params(axis="x", rotation=45, labelsize=8)
    plt.tight_layout()
    out = out_dir / "topology_degree.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    log.info(f"Wrote {out}")


def print_per_router_topology(snapshots: List[Dict]) -> None:
    """Per-router breakdown: interface count, peer_of degree, role, member_of VPNs."""
    latest = snapshots[-1]
    routers = {n["id"]: n for n in latest["nodes"] if n["type"] == "router"}
    if not routers:
        return

    iface_per_router: Dict[str, int] = {}
    peer_per_router:  Dict[str, int] = {}
    vpns_per_router:  Dict[str, List[str]] = {rid: [] for rid in routers}
    vpn_id_to_name = {n["id"]: n.get("name", n["id"])
                      for n in latest["nodes"] if n["type"] == "vpn"}

    for n in latest["nodes"]:
        if n["type"] == "interface" and n.get("device_id") in routers:
            iface_per_router[n["device_id"]] = iface_per_router.get(n["device_id"], 0) + 1

    for e in latest["edges"]:
        if e["relation"] == "peer_of" and e["source"] in routers:
            peer_per_router[e["source"]] = peer_per_router.get(e["source"], 0) + 1
        elif e["relation"] == "member_of" and e["source"] in routers and e["target"] in vpn_id_to_name:
            vpns_per_router[e["source"]].append(vpn_id_to_name[e["target"]])

    print("\n" + "=" * 96)
    print(f"PER-ROUTER TOPOLOGY  (latest snapshot: {latest.get('timestamp', '?')})")
    print("=" * 96)
    print(f"  {'hostname':<22}  {'role':<4}  {'state':>5}  {'#ifaces':>8}  {'#peers':>7}  vpns")
    print("  " + "-" * 86)
    for rid, r in sorted(routers.items(), key=lambda kv: kv[1].get("hostname", kv[0])):
        hn   = r.get("hostname", rid)
        role = r.get("role", "")
        st   = int(r.get("state", 1.0))
        ifc  = iface_per_router.get(rid, 0)
        prs  = peer_per_router.get(rid, 0)
        vpns = ",".join(sorted(set(vpns_per_router.get(rid, [])))) or "-"
        print(f"  {hn:<22}  {role:<4}  {st:>5}  {ifc:>8}  {prs:>7}  {vpns}")


# ── 7. Topology visualisation ────────────────────────────────────────────────

ROLE_COLOR = {"P": "#888888", "PE": "#4C9BE8", "RR": "#E85C4C", "CE": "#5CB85C"}


def plot_topology_graph(snapshots: List[Dict], out_dir: Path) -> None:
    """Two networkx views of the latest snapshot:
       (a) router peer_of graph — node size ∝ degree, colour ∝ role.
       (b) router-interface bipartite — routers top band, interfaces bottom.
    """
    latest = snapshots[-1]
    routers    = [n for n in latest["nodes"] if n["type"] == "router"]
    interfaces = [n for n in latest["nodes"] if n["type"] == "interface"]

    # ── (a) Router peer_of graph ──────────────────────────────────────────────
    R = nx.Graph()
    for r in routers:
        R.add_node(r["id"], hostname=r.get("hostname", r["id"]),
                   role=r.get("role", ""), state=r.get("state", 1.0))
    for e in latest["edges"]:
        if e["relation"] == "peer_of" and e["source"] in R and e["target"] in R:
            R.add_edge(e["source"], e["target"])

    if R.number_of_nodes() > 0:
        colors = [ROLE_COLOR.get(R.nodes[n]["role"], "#dddddd") for n in R.nodes()]
        alphas = [1.0 if R.nodes[n]["state"] == 1.0 else 0.25 for n in R.nodes()]
        sizes  = [400 + 120 * R.degree(n) for n in R.nodes()]
        labels = {n: R.nodes[n]["hostname"] for n in R.nodes()}
        pos = nx.spring_layout(R, seed=42, k=1.6)

        fig, ax = plt.subplots(figsize=(13, 9))
        nx.draw_networkx_edges(R, pos, ax=ax, alpha=0.35, width=1.4, edge_color="gray")
        for i, n in enumerate(R.nodes()):
            nx.draw_networkx_nodes(R, pos, nodelist=[n], ax=ax,
                                   node_color=[colors[i]], node_size=[sizes[i]],
                                   alpha=alphas[i])
        nx.draw_networkx_labels(R, pos, labels=labels, ax=ax, font_size=8, font_weight="bold")
        legend = [plt.Line2D([0], [0], marker="o", color="w",
                             markerfacecolor=c, markersize=12, label=role)
                  for role, c in ROLE_COLOR.items()]
        legend.append(plt.Line2D([0], [0], marker="o", color="w",
                                 markerfacecolor="gray", alpha=0.25, markersize=12,
                                 label="state=0 (down)"))
        ax.legend(handles=legend, title="Role", loc="upper left", fontsize=9)
        ax.set_title(f"Router peer_of topology — {latest.get('timestamp','?')}\n"
                     f"(node size ∝ degree, faded = state=0)", fontsize=12)
        ax.axis("off")
        plt.tight_layout()
        out = out_dir / "topology_router_peer.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")

    # ── (b) Router-Interface bipartite ────────────────────────────────────────
    if interfaces and routers:
        H = nx.Graph()
        for r in routers:
            H.add_node(r["id"], ntype="router", label=r.get("hostname", ""), role=r.get("role", ""))
        for i in interfaces:
            H.add_node(i["id"], ntype="interface", label=i.get("name", ""))
        for e in latest["edges"]:
            if (e["relation"] in ("has_interface", "connected_to")
                and e["source"] in H and e["target"] in H):
                H.add_edge(e["source"], e["target"], relation=e["relation"])

        routers_h = [n for n, d in H.nodes(data=True) if d["ntype"] == "router"]
        ifaces_h  = [n for n, d in H.nodes(data=True) if d["ntype"] == "interface"]

        pos = {}
        for i, n in enumerate(sorted(routers_h, key=lambda n: H.nodes[n]["label"])):
            pos[n] = (i * 2.5, 2.0)
        for i, n in enumerate(sorted(ifaces_h)):
            pos[n] = (i * 0.38, 0.0)

        r_colors    = [ROLE_COLOR.get(H.nodes[n]["role"], "#888888") for n in routers_h]
        edge_colors = ["lightgray" if H.edges[e]["relation"] == "has_interface" else "steelblue"
                       for e in H.edges()]

        fig, ax = plt.subplots(figsize=(20, 7))
        nx.draw_networkx_nodes(H, pos, nodelist=routers_h, node_color=r_colors,
                               node_size=900, ax=ax)
        nx.draw_networkx_nodes(H, pos, nodelist=ifaces_h, node_color="#B3D9F7",
                               node_size=60, ax=ax, alpha=0.85)
        nx.draw_networkx_edges(H, pos, ax=ax, edge_color=edge_colors,
                               alpha=0.35, width=0.6)
        nx.draw_networkx_labels(H, pos, labels={n: H.nodes[n]["label"] for n in routers_h},
                                ax=ax, font_size=7, font_weight="bold")
        ax.set_title("Router-Interface topology\n"
                     "(routers top, interfaces bottom; grey = has_interface, blue = connected_to)",
                     fontsize=11)
        ax.axis("off")
        plt.tight_layout()
        out = out_dir / "topology_bipartite.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        plt.close(fig)
        log.info(f"Wrote {out}")


# ── 8. Node-presence completeness ────────────────────────────────────────────

def report_node_presence(dfs: Dict[str, pd.DataFrame], snapshots: List[Dict]) -> None:
    """Lists nodes that appear in <100 % of snapshots — catches ephemeral
    or intermittently-collected entities (down routers, paused flows, etc.).
    """
    n_snaps = len(snapshots)
    print("\n" + "=" * 96)
    print(f"NODE PRESENCE  (n_snapshots = {n_snaps})")
    print("=" * 96)
    for nt in NODE_TYPES:
        df = dfs[nt]
        if df.empty:
            print(f"\n  [{nt}] no data"); continue
        presence = df.groupby("id")["t"].nunique()
        partial = presence[presence < n_snaps].sort_values()
        full    = (presence == n_snaps).sum()
        print(f"\n  [{nt}]  {full}/{len(presence)} nodes present in all snapshots")
        if not partial.empty:
            print(f"    Partial-presence (bottom 15):")
            for nid, cnt in partial.head(15).items():
                print(f"      {str(nid)[:60]:<60}  {cnt}/{n_snaps}  ({100*cnt/n_snaps:.0f}%)")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshots-dir", type=Path, default=None,
                   help="Cache directory of snapshot_*.pkl files (used first if present)")
    p.add_argument("--from-time",     type=_parse_dt, default=None)
    p.add_argument("--to-time",       type=_parse_dt, default=None)
    p.add_argument("--recent",        type=str,       default=None,
                   help="Walk back this far from the latest data (e.g. '30m', '2h', '1h30m')")
    p.add_argument("--interval-minutes", type=float, default=0.5,
                   help="Snapshot cadence in minutes (matches training; default 0.5)")
    p.add_argument("--project",          default=os.getenv("GOOGLE_CLOUD_PROJECT",
                                                            os.getenv("GOOGLE_PROJECT", "gnn-networks")))
    p.add_argument("--spanner-instance", default=os.getenv("SPANNER_INSTANCE", "networktopology-instance"))
    p.add_argument("--spanner-database", default=os.getenv("SPANNER_DATABASE", "transporttopology-db"))
    p.add_argument("--out-dir",          type=Path, default=Path("eda_output"),
                   help="Directory for CSV + PNG outputs")
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    recent_min = _parse_recent(args.recent) if args.recent else None
    snapshots = load_snapshots(
        snapshots_dir=args.snapshots_dir,
        from_time=args.from_time,
        to_time=args.to_time,
        interval_minutes=args.interval_minutes,
        project=args.project,
        spanner_instance=args.spanner_instance,
        spanner_database=args.spanner_database,
        recent_minutes=recent_min,
    )

    print(f"\nLoaded {len(snapshots)} snapshot(s).  "
          f"Anomaly thresholds (per-node MSE): {ANOMALY_THRESHOLDS}")

    dfs = extract_dataframes(snapshots)

    print_snapshot_inventory(snapshots)
    report_feature_quality(dfs, args.out_dir)
    plot_distributions(dfs, args.out_dir)
    plot_temporal_evolution(dfs, args.out_dir)
    plot_per_node_heatmaps(dfs, args.out_dir)
    plot_zero_rate_heatmaps(dfs, args.out_dir)
    plot_down_events(dfs, args.out_dir)
    plot_topology(snapshots, args.out_dir)
    print_per_router_topology(snapshots)
    plot_topology_graph(snapshots, args.out_dir)
    report_node_presence(dfs, snapshots)

    print(f"\n✓ Done. Outputs in {args.out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
