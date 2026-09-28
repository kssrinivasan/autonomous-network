# local_monitor — Streamlit dashboard for HetGNN anomaly scores

Local visualisation of the `NodeEmbedding` Spanner table. Reproduces the four most-useful sections of `embedding_exploration.ipynb` as a live, point-and-click dashboard.

| Page section | Notebook origin |
|---|---|
| Threshold calibration | §4 |
| Anomaly score over time (per-type, threshold-normalised) | §5 |
| Score heatmap (max, threshold-normalised) | §6 |
| Anomaly deep-dive (top-K, with per-node drill-down) | §11 |

## Install

```bash
pip install -r gnn/tests/local_monitor/requirements.txt
```

`google-cloud-spanner`, `pandas`, `matplotlib`, and `seaborn` are likely already present from the rest of the repo — Streamlit is the only new dependency.

## Run

From the repo root:

```bash
source ./setenv.sh        # GOOGLE_PROJECT, SPANNER_INSTANCE, SPANNER_DATABASE
streamlit run gnn/tests/local_monitor/app.py
```

Streamlit opens [http://localhost:8501](http://localhost:8501) automatically. If you only have `GOOGLE_CLOUD_PROJECT` set (and it points somewhere other than `gnn-networks`), override on the command line:

```bash
GOOGLE_PROJECT=gnn-networks streamlit run gnn/tests/local_monitor/app.py
```

Credentials are picked up automatically from `domains/transport/gnn/src/networkagent.json` (git-ignored; not in the tree by default) if present, otherwise from Application Default Credentials (`gcloud auth application-default login`).

## Using the dashboard

- **Snapshots to load** (sidebar) — how many distinct timestamps to pull from `NodeEmbedding`, most recent first. Defaults to 300 (~5 h at a 1-min Cloud Scheduler interval).
- **🔄 Refresh data** — clears the in-process Spanner cache and re-runs the page. Use this when you know new inference rows have landed and want them now.
- **Top-K slider** (§4) — how many nodes to show in each of the two rankings (worst spike vs. persistence).
- **Inspect a node** (§4) — drill into one node's score timeseries and per-feature MSE breakdown at its worst snapshot.

## Live updates (auto-refresh)

The dashboard tracks `serve.py`'s output in near-real-time. Two pieces working together:

1. **`streamlit-autorefresh`** ticks the page on the interval set in the sidebar (default **60 s**, matching the Cloud Scheduler cadence that fires `serve.py`).
2. **`@st.cache_data(ttl=30)` on `latest_timestamp()`** — the cheap `SELECT MAX(timestamp)` aggregate is re-queried at most every 30 s. When `MAX(timestamp)` advances, the row-fetch cache (keyed on `(num_snapshots, latest_ts)`) misses and pulls the new window. When it hasn't moved, the page redraws against the in-memory cache instantly — no Spanner round-trip beyond the aggregate.

Net result: a new inference run lands → on the next auto-refresh tick (≤ 60 s) the dashboard re-queries `MAX(timestamp)` → sees a fresh value → re-fetches the rows → re-renders all four sections.

Toggles in the sidebar:

- **Auto-refresh** — pause/resume the ticker (useful when sharing a screenshot).
- **Interval (seconds)** — slider 15 – 300 s. Default 60 s to match `serve.py`'s default Cloud Scheduler cadence; bump up to 300 if you don't care about latency and want to be quiet on Spanner.
- **🔄 Refresh data now** — clears both caches and re-fetches immediately (use when you've manually triggered an inference and don't want to wait).

Streamlit's cache lives in process memory, so it resets when you stop the app. There is **no on-disk pickle cache** here (unlike the notebook) — Streamlit's own caching is enough for a long-running dashboard process.

## Files

```
local_monitor/
├── app.py              # Streamlit entrypoint + page layout
├── data.py             # Spanner fetch + cached helpers
├── plots.py            # Matplotlib renderers (one per page section)
├── requirements.txt    # Streamlit + Google Cloud + plotting deps
└── Readme.md           # This file
```

Schema constants (`NODE_TYPES`, `ANOMALY_THRESHOLDS`) are imported from `domains/transport/gnn/src/utils/gnn_utils.py` — the same source of truth used by `serve.py` and `train_hetgnn.py`. So if thresholds change, the dashboard picks them up on the next restart with no edits here.
