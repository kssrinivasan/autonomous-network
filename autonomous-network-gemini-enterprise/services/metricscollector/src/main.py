import time
import itertools
import json
import os
import datetime
import threading
from google.api import metric_pb2 as ga_metric
from google.cloud import bigquery
from google.cloud import monitoring_v3
from google.cloud import spanner

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.environ.get("GOOGLE_PROJECT")
INSTANCE_ID = os.environ.get("GOOGLE_SPANNER_INSTANCE")
# Router metrics go to transport database
TRANSPORT_DATABASE_ID = os.environ.get("GOOGLE_SPANNER_TRANSPORT_DATABASE", "transporttopology-db")
# Traffic flow metrics go to service database
SERVICE_DATABASE_ID = os.environ.get("GOOGLE_SPANNER_SERVICE_DATABASE", "servicetopology-db")

# Must be greater than the ops agent scrape interval so that we always
# get the same number of data points at each polling interval.
# See ops agent config file for more details:
#   operator/src/vyosvm/playbooks/templates/ops-agent-config.yaml.j2
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 20))

# Spanner write batching
# RouterSystemMetrics has 7 columns + 1 secondary index:
#   Base table: 7 cols → 7 mutations per row
#   RouterSystemMetricsByTimestamp: ~5 mutations per index row
#   Total: ~12 mutations per row → safe ceiling ~6,666 rows
#
# RouterRoutingSeries has 13 columns + 1 secondary index:
#   Base table: 13 cols → 13 mutations per row
#   RouterRoutingSeriesByTimestamp: ~11 mutations per index row
#   Total: ~24 mutations per row → safe ceiling ~3,300 rows
#
# TrafficFlowSeries has 8 columns + 1 secondary index:
#   Base table: 8 cols → 8 mutations
#   TrafficFlowSeriesByTimestamp: ~6 mutations per index row
#   Total: ~14 mutations per row → safe ceiling ~5,700 rows
#
# RouterRoutingSeries has the widest key, so it sets the floor. The old default
# of 3,000 was chosen against the 9-column RouterRoutingMetrics and would leave
# only 10% headroom here; 2,000 restores the margin at the cost of one extra
# round trip per poll on a large network.
SPANNER_BATCH_SIZE = int(os.environ.get("SPANNER_BATCH_SIZE", 2000))

# BigQuery analytics sink. Spanner is the operational store with a four-hour
# retention window (see RETENTION_HOURS); BigQuery keeps the same rows for
# analysis past that window. The dataset and its tables are created by
# gcp/bigquery.tpl.yaml, and their schemas are column-for-column the Spanner
# ones, so each BigQuery table name is just the Spanner table lowercased.
# Set BQ_DATASET to the empty string to turn the sink off.
BQ_DATASET = os.environ.get("BQ_DATASET", "serviceperformance")
# The streaming insert API caps a request at 10,000 rows and 10 MB; 500 keeps
# each request comfortably small without making many round trips per poll.
BQ_BATCH_SIZE = int(os.environ.get("BQ_BATCH_SIZE", 500))

# Retention settings
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", 4))
# Metrics clean up frequency
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", 1200))
# Spanner DB connection check interval
DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))

# ── VyOS router metrics (job=vyos-lab) ────────────────────────────────────────
SELECTED_METRICS = [
  # SYSTEM metrics
  'prometheus.googleapis.com/node_load1/gauge',
  'prometheus.googleapis.com/node_memory_SwapFree_bytes/gauge',
  'prometheus.googleapis.com/node_memory_MemTotal_bytes/gauge',
  'prometheus.googleapis.com/node_memory_MemAvailable_bytes/gauge',
  'prometheus.googleapis.com/node_network_up/gauge',
  'prometheus.googleapis.com/node_network_carrier/gauge',
  'prometheus.googleapis.com/node_network_mtu_bytes/gauge',
  'prometheus.googleapis.com/node_network_carrier_changes_total/counter',
  'prometheus.googleapis.com/node_network_receive_bytes_total/counter',
  'prometheus.googleapis.com/node_network_receive_drop_total/counter',
  'prometheus.googleapis.com/node_network_receive_errs_total/counter',
  'prometheus.googleapis.com/node_network_receive_packets_total/counter',
  'prometheus.googleapis.com/node_network_transmit_bytes_total/counter',
  'prometheus.googleapis.com/node_network_transmit_drop_total/counter',
  'prometheus.googleapis.com/node_network_transmit_errs_total/counter',
  'prometheus.googleapis.com/node_network_transmit_packets_total/counter',
  'prometheus.googleapis.com/node_network_transmit_queue_length/gauge',
  # ROUTING metrics
  'prometheus.googleapis.com/frr_bfd_peer_count/gauge',
  'prometheus.googleapis.com/frr_collector_up/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbor_adjacencies/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbors/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_state/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_prefixes_advertised_count_total/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_prefixes_received_count_total/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_uptime_seconds/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_message_received_total/counter',
  'prometheus.googleapis.com/frr_bgp_peer_message_sent_total/counter',
  'prometheus.googleapis.com/frr_bgp_peers_count_total/gauge',
  'prometheus.googleapis.com/frr_bgp_peers_memory_bytes/gauge',
  'prometheus.googleapis.com/frr_bgp_rib_count_total/gauge',
  'prometheus.googleapis.com/frr_bgp_rib_memory_bytes/gauge',
  'prometheus.googleapis.com/frr_route_total/gauge',
  'prometheus.googleapis.com/frr_route_total_fib/gauge',
  # Deliberately NOT collected: process_open_fds,
  # process_network_receive_bytes_total, process_network_transmit_bytes_total.
  # These are client_golang exporter self-metrics, not router metrics. Each
  # router is scraped on two ports — :9100 (node_exporter) and :9101
  # (frr_exporter) — and both processes emit them under the same router_name,
  # so they collided on every poll (54 rows = 3 metrics x 18 routers). The only
  # thing separating the two emitters is the Cloud Monitoring *resource* label
  # `instance`, which nothing here reads. Nothing consumed them either: no GNN
  # feature, no MCP tool, no dashboard panel. Exporter liveness is already
  # covered by frr_collector_up. See 7.8b in MIGRATION_PLAN.md.
]

# ── Traffic-agent device metrics (job=traffic-agents) ─────────────────────────
# Scraped from :9091/metrics on each device container.
# Labels per series: flow_id, role, protocol
# Stored in TrafficFlowSeries keyed by (flow_id, timestamp, node_name,
# metric_name, role). role must be in the key: every flow is reported from both
# ends, node_name is derived from flow_id and so is identical for both, and
# several of these metrics are only meaningful from one end (see 7.8a in
# MIGRATION_PLAN.md).
#
# Note: bytes_sent_total and bytes_received_total are separate counters — there
# is no combined traffic_agent_bytes_total metric.
TRAFFIC_AGENT_METRICS = [
  # Bidirectional throughput (sent + received) — kept for backward compatibility.
  # Prefer throughput_sent_bps / throughput_recv_bps for unidirectional accounting.
  'prometheus.googleapis.com/traffic_agent_throughput_bps/gauge',
  # Directional throughput gauges (added to fix 2× double-counting on bidir flows).
  'prometheus.googleapis.com/traffic_agent_throughput_sent_bps/gauge',
  'prometheus.googleapis.com/traffic_agent_throughput_recv_bps/gauge',
  'prometheus.googleapis.com/traffic_agent_bytes_sent_total/counter',
  'prometheus.googleapis.com/traffic_agent_bytes_received_total/counter',
  'prometheus.googleapis.com/traffic_agent_latency_ms/gauge',
  'prometheus.googleapis.com/traffic_agent_jitter_ms/gauge',
  'prometheus.googleapis.com/traffic_agent_packet_loss_pct/gauge',
  'prometheus.googleapis.com/traffic_agent_active_sessions/gauge',
  'prometheus.googleapis.com/traffic_agent_flow_running/gauge',
]

#: VyOS metric names seen that are neither node_* nor frr_*. Warned about once
#: each rather than on every poll — 20-second cycles turn a real signal into
#: noise nobody reads.
_unclassified_metrics: set = set()


# ── Metric descriptor cache ────────────────────────────────────────────────────
# Cloud Monitoring metric descriptors are static (they only change when a new
# metric is registered). Fetching them on every 20-second poll cycle wastes
# 24+ API calls per cycle. We cache them here and refresh hourly.
#
# _descriptor_cache maps metric_type (str) → descriptor object.
# _descriptor_cache_lock guards the dict for thread-safe refresh.
# _descriptor_cache_updated_at is a monotonic timestamp (time.monotonic()).

DESCRIPTOR_CACHE_TTL_SECONDS = int(os.environ.get("DESCRIPTOR_CACHE_TTL", 3600))

_descriptor_cache: dict = {}
_descriptor_cache_updated_at: float = 0.0
# RLock (reentrant) is required because _get_descriptor() acquires this lock
# and then calls _refresh_descriptor_cache() which also acquires it.  A plain
# threading.Lock() would deadlock when the TTL fires after 1 hour.
_descriptor_cache_lock = threading.RLock()


def _refresh_descriptor_cache(client) -> None:
    """Fetch all metric descriptors for known metric types and populate the cache.

    Called once at startup and whenever the cache TTL has expired.  Any metric
    that cannot be fetched is silently omitted (it will be skipped at poll time).
    """
    all_metrics = SELECTED_METRICS + TRAFFIC_AGENT_METRICS
    new_cache: dict = {}
    logger.info(
        "Refreshing metric descriptor cache for %d metric types …", len(all_metrics)
    )
    for metric_type in all_metrics:
        try:
            descriptor = client.get_metric_descriptor(
                name=f"projects/{PROJECT_ID}/metricDescriptors/{metric_type}"
            )
            new_cache[metric_type] = descriptor
        except Exception as e:
            logger.warning("Could not get descriptor for %s: %s", metric_type, e)

    with _descriptor_cache_lock:
        _descriptor_cache.clear()
        _descriptor_cache.update(new_cache)
        global _descriptor_cache_updated_at
        _descriptor_cache_updated_at = time.monotonic()

    logger.info(
        "Descriptor cache refreshed: %d/%d metrics cached.",
        len(new_cache), len(all_metrics),
    )


def _get_descriptor(metric_type: str):
    """Return the cached descriptor for *metric_type*, or None if not cached.

    This is a pure cache lookup — it never triggers a refresh.  Refreshes are
    managed by _fetch_metrics (once per call if any metrics are missing) and by
    the TTL logic in _fetch_metrics, keeping refresh calls out of the hot
    per-metric loop.
    """
    return _descriptor_cache.get(metric_type)


def get_metric_aggregation(descriptor, window_seconds=60):
    """
    Determines the correct aligner based on the metric's Kind and Value Type.
    """
    kind = descriptor.metric_kind  # GAUGE, CUMULATIVE, or DELTA

    if kind == descriptor.MetricKind.CUMULATIVE:
        # Counters need a rate (delta / time interval)
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_RATE
    elif kind == descriptor.MetricKind.GAUGE:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_MEAN
    else:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_NEXT_OLDER

    return monitoring_v3.Aggregation({
        "alignment_period": {"seconds": window_seconds},
        "per_series_aligner": aligner,
        "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_NONE,
    })


def _fetch_metrics(client, project_name, metric_list, label_gate_key=None):
    """
    Generic helper: fetch a list of Cloud Monitoring metric types and return a
    chained iterator of time-series results.

    Descriptors are resolved from the module-level cache (_descriptor_cache).
    This function never triggers a descriptor refresh — refreshes happen in a
    dedicated background thread (_descriptor_refresh_worker) so the main poll
    loop is never blocked by Cloud Monitoring API calls.  Missing metrics are
    silently skipped; all other metrics in the list are still fetched and
    written to Spanner normally.

    Args:
        metric_list: list of fully-qualified metric type strings.
        label_gate_key: if set, skip descriptors that lack this label key.
    """
    # ── Per-metric loop — pure cache lookups only, no refresh inside ──────────
    offset = 2  # safety offset so data has arrived before we query
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - offset
    start_ts = now_ts - POLL_INTERVAL

    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": now_ts},
        "start_time": {"seconds": start_ts},
    })

    generators = []

    for metric_type in metric_list:
        descriptor = _get_descriptor(metric_type)
        if descriptor is None:
            logger.debug("No cached descriptor for %s — skipping", metric_type)
            continue

        # Optional: skip descriptors that don't carry the expected label
        if label_gate_key:
            has_label = any(lbl.key == label_gate_key for lbl in descriptor.labels)
            if not has_label:
                logger.debug(f"Skipping {metric_type} — missing label '{label_gate_key}'")
                continue

        aggregation = get_metric_aggregation(descriptor, POLL_INTERVAL)
        metric_filter = f'metric.type = "{descriptor.type}"'

        try:
            results = client.list_time_series(request={
                "name": project_name,
                "filter": metric_filter,
                "interval": interval,
                "aggregation": aggregation,
            })
            generators.append(results)
        except Exception as e:
            logger.error(f"Error fetching {descriptor.type}: {e}")

    return itertools.chain.from_iterable(generators)


def fetch_all_vyos_metrics(client, project_name, _start_time):
    """Fetches every metric belonging to the VyOS job (router_name label required)."""
    return _fetch_metrics(client, project_name, SELECTED_METRICS, label_gate_key="router_name")


def fetch_traffic_agent_metrics(client, project_name):
    """Fetches traffic-agent metrics (flow_id label required)."""
    return _fetch_metrics(client, project_name, TRAFFIC_AGENT_METRICS, label_gate_key="flow_id")


def check_spanner_connection(db_container, lock):
    """Checks if the Spanner databases exist and reconnects if necessary."""
    try:
        # Check transport database
        if not db_container['transport_db'].exists():
            with lock:
                if not db_container['transport_db'].exists():
                    logger.warning("Transport database not found. Attempting to reconnect...")
                    db_container['transport_db'] = spanner_connect(TRANSPORT_DATABASE_ID)
                    logger.warning("Reconnected to transport database.")

        # Check service database
        if not db_container['service_db'].exists():
            with lock:
                if not db_container['service_db'].exists():
                    logger.warning("Service database not found. Attempting to reconnect...")
                    db_container['service_db'] = spanner_connect(SERVICE_DATABASE_ID)
                    logger.warning("Reconnected to service database.")

        logger.info("Both Spanner DB connections alive!")
    except Exception as e:
        with lock:
            logger.error(f"Error checking Spanner connection: {e}. Attempting to reconnect...")
            try:
                db_container['transport_db'] = spanner_connect(TRANSPORT_DATABASE_ID)
                db_container['service_db'] = spanner_connect(SERVICE_DATABASE_ID)
                logger.warning("Reconnected to both databases after error.")
            except Exception as re:
                logger.error(f"Failed to reconnect to Spanner: {re}")


def spanner_connection_worker(db_container, lock):
    """Background worker that regularly checks the Spanner connection."""
    logger.info(f"Spanner connection worker started. Checking every {DB_CHECK_SECONDS} seconds.")
    while True:
        check_spanner_connection(db_container, lock)
        time.sleep(DB_CHECK_SECONDS)


def retention_worker(db_container, lock):
    """Background worker that removes old metrics from Spanner."""
    logger.info(
        f"Retention thread started. Retention period: {RETENTION_HOURS} hours. "
        f"Cleanup interval: {CLEANUP_INTERVAL_SECONDS}s"
    )
    while True:
        try:
            time.sleep(CLEANUP_INTERVAL_SECONDS)
            cutoff_time = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=RETENTION_HOURS)
            )
            logger.debug(f"Retention thread: Cleanup started. Removing data older than {cutoff_time}")
            with lock:
                logger.debug("Retention thread: Acquired lock")
                # Clean up RouterSystemMetrics and RouterRoutingSeries (transport database)
                sm_count = db_container['transport_db'].execute_partitioned_dml(
                    "DELETE FROM RouterSystemMetrics WHERE timestamp < @cutoff_time",
                    params={'cutoff_time': cutoff_time},
                    param_types={'cutoff_time': spanner.param_types.TIMESTAMP}
                )
                logger.info(f"Retention thread: Deleted {sm_count} old RouterSystemMetrics rows.")
                rm_count = db_container['transport_db'].execute_partitioned_dml(
                    "DELETE FROM RouterRoutingSeries WHERE timestamp < @cutoff_time",
                    params={'cutoff_time': cutoff_time},
                    param_types={'cutoff_time': spanner.param_types.TIMESTAMP}
                )
                logger.info(f"Retention thread: Deleted {rm_count} old RouterRoutingSeries rows.")
                # Clean up TrafficFlowSeries rows (service database)
                tfm_count = db_container['service_db'].execute_partitioned_dml(
                    "DELETE FROM TrafficFlowSeries WHERE timestamp < @cutoff_time",
                    params={'cutoff_time': cutoff_time},
                    param_types={'cutoff_time': spanner.param_types.TIMESTAMP}
                )
                logger.info(f"Retention thread: Deleted {tfm_count} old TrafficFlowSeries rows.")
                logger.debug("Retention thread: Released lock")
        except Exception as e:
            logger.error(f"Error in retention worker: {e}")


def spanner_connect(database_id):
    """Connect to a specific Spanner database."""
    span_client = spanner.Client(project=PROJECT_ID, disable_builtin_metrics=True)
    db = span_client.instance(INSTANCE_ID).database(database_id)
    return db


# ── Minimum seconds between background descriptor refreshes for missing metrics.
# The background thread checks every POLL_INTERVAL seconds but only calls
# _refresh_descriptor_cache when this interval has elapsed since the last refresh.
_MISSING_METRIC_REFRESH_INTERVAL = int(os.environ.get("MISSING_METRIC_REFRESH_INTERVAL", 60))


def _descriptor_refresh_worker(client) -> None:
    """Background thread: periodically refreshes the descriptor cache.

    Runs two kinds of refresh:
    1. TTL-based full refresh (every DESCRIPTOR_CACHE_TTL_SECONDS, default 1 h)
       — keeps known-good descriptors up to date.
    2. Missing-metric refresh (every _MISSING_METRIC_REFRESH_INTERVAL, default 60 s)
       — discovers newly-deployed metrics (e.g. a new traffic-agent gauge) without
       waiting a full hour.

    By running in a background thread, neither refresh ever blocks the main poll
    loop or delays Spanner writes.
    """
    all_metrics = SELECTED_METRICS + TRAFFIC_AGENT_METRICS
    while True:
        time.sleep(POLL_INTERVAL)  # check every poll cycle
        age = time.monotonic() - _descriptor_cache_updated_at
        missing = [m for m in all_metrics if m not in _descriptor_cache]

        should_refresh = (
            age >= DESCRIPTOR_CACHE_TTL_SECONDS          # full TTL expired
            or (missing and age >= _MISSING_METRIC_REFRESH_INTERVAL)  # new metrics waiting
        )

        if should_refresh:
            if _descriptor_cache_lock.acquire(blocking=False):
                try:
                    # Re-check inside the lock (another thread may have just refreshed).
                    age = time.monotonic() - _descriptor_cache_updated_at
                    missing = [m for m in all_metrics if m not in _descriptor_cache]
                    if age >= DESCRIPTOR_CACHE_TTL_SECONDS or (missing and age >= _MISSING_METRIC_REFRESH_INTERVAL):
                        if missing:
                            logger.info(
                                "Descriptor refresh worker: %d missing metric(s) — refreshing: %s",
                                len(missing), missing,
                            )
                        _refresh_descriptor_cache(client)
                finally:
                    _descriptor_cache_lock.release()


_bq_client = None
def _get_bq_client():
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


def _write_bigquery(table, columns, data):
    """Stream the same rows into BigQuery that just went to Spanner.

    The two sinks are deliberately independent: BigQuery is analytics, Spanner
    is what the tools and the dashboard read, so a BigQuery outage must never
    cost an operational write. Every failure here is logged and swallowed.
    """
    if not BQ_DATASET or not data:
        return

    rows = []
    for values in data:
        row = {}
        for column, value in zip(columns, values):
            # Cloud Monitoring hands back a DatetimeWithNanoseconds, which
            # Spanner takes as-is but the JSON streaming API cannot serialize.
            row[column] = value.isoformat() if isinstance(value, datetime.datetime) else value
        rows.append(row)

    table_id = f"{PROJECT_ID}.{BQ_DATASET}.{table.lower()}"
    try:
        client = _get_bq_client()
    except Exception as e:
        logger.error(f"Could not create the BigQuery client, skipping {table_id}: {e}")
        return

    for i in range(0, len(rows), BQ_BATCH_SIZE):
        chunk = rows[i:i + BQ_BATCH_SIZE]
        try:
            errors = client.insert_rows_json(table_id, chunk)
            if errors:
                # insert_rows_json reports per-row failures rather than raising,
                # so a partial write looks like success unless we check.
                logger.error(f"BigQuery rejected {len(errors)} of {len(chunk)} rows for {table_id}: {errors[:3]}")
        except Exception as e:
            logger.error(f"Error streaming {len(chunk)} rows to BigQuery {table_id}: {e}")


def _warn_on_key_collisions(table, columns, data, key_columns):
    """Log the rows that will silently overwrite each other in *table*.

    insert_or_update is last-writer-wins, so two rows sharing a primary key are
    not an error -- Spanner keeps one and says nothing. That is how the OSPF
    per-interface and BGP per-safi series were lost for as long as they were: a
    dimension the exporter emits was not in the key, and the only symptom was a
    dashboard panel that looked oddly sparse.

    This does not repair anything. It exists so that the *next* label the
    exporter grows shows up as a log line instead of as missing data.
    """
    if not key_columns:
        return
    seen, collided = set(), {}
    for values in data:
        key = tuple(values[:key_columns])
        if key in seen:
            collided[key] = collided.get(key, 1) + 1
        else:
            seen.add(key)
    if not collided:
        return
    # One example is enough to identify the offending dimension; the caller can
    # diff its labels against the key columns to see which one is missing.
    example_key, example_count = next(iter(collided.items()))
    logger.warning(
        f"{table}: {len(collided)} primary keys have more than one row in this "
        f"poll, so {sum(collided.values()) - len(collided)} of {len(data)} rows "
        f"will be overwritten. The key is missing a dimension the exporter emits. "
        f"Example ({example_count} rows): "
        f"{dict(zip(columns[:key_columns], example_key))}"
    )


def _write_batches(db_container, lock, table, columns, data, current_poll_time,
                   db_key='transport_db', key_columns=0):
    """Write *data* to *table* in batches, respecting SPANNER_BATCH_SIZE.

    The same rows are then streamed to the matching BigQuery table.

    Args:
        db_key: 'transport_db' for router metrics, 'service_db' for traffic metrics
        key_columns: how many leading elements of each tuple form the table's
            primary key. Non-zero turns on collision reporting; see
            _warn_on_key_collisions.
    """
    if not data:
        return
    _warn_on_key_collisions(table, columns, data, key_columns)
    total = len(data)
    chunks = [
        data[i:i + SPANNER_BATCH_SIZE]
        for i in range(0, total, SPANNER_BATCH_SIZE)
    ]
    logger.info(
        f"Inserting {total} {table} points in {len(chunks)} Spanner "
        f"batch(es) of up to {SPANNER_BATCH_SIZE} rows at {current_poll_time} (db={db_key})"
    )
    for chunk_idx, chunk in enumerate(chunks):
        try:
            with lock:
                logger.debug(
                    f"Main thread: writing {table} chunk {chunk_idx + 1}/{len(chunks)} "
                    f"({len(chunk)} rows) to {db_key}"
                )
                with db_container[db_key].batch() as batch:
                    batch.insert_or_update(
                        table=table,
                        columns=columns,
                        values=chunk,
                    )
        except Exception as e:
            logger.error(
                f"Error writing to Spanner {table} (chunk {chunk_idx + 1}/{len(chunks)}, db={db_key}): {e}"
            )

    _write_bigquery(table, columns, data)


def run_worker():
    mon_client = monitoring_v3.MetricServiceClient()
    # Initialize both databases
    transport_db = spanner_connect(TRANSPORT_DATABASE_ID)
    service_db = spanner_connect(SERVICE_DATABASE_ID)
    db_container = {
        'transport_db': transport_db,
        'service_db': service_db
    }
    project_name = f"projects/{PROJECT_ID}"

    lock = threading.Lock()

    # Warm the descriptor cache before starting the poll loop so the very first
    # iteration does not incur individual get_metric_descriptor() calls.
    _refresh_descriptor_cache(mon_client)

    # Start background descriptor refresh thread.
    # This handles both TTL-based full refreshes and missing-metric discovery
    # without ever blocking the main poll loop.
    descriptor_thread = threading.Thread(
        target=_descriptor_refresh_worker,
        args=(mon_client,),
        daemon=True,
    )
    descriptor_thread.start()

    # Start retention thread
    retention_thread = threading.Thread(
        target=retention_worker,
        args=(db_container, lock),
        daemon=True
    )
    retention_thread.start()

    # Start Spanner connection worker thread
    connection_thread = threading.Thread(
        target=spanner_connection_worker,
        args=(db_container, lock),
        daemon=True
    )
    connection_thread.start()

    logger.info(f"Worker Pool started. Polling all metrics every {POLL_INTERVAL}s...")
    last_poll_time = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=POLL_INTERVAL)
    )

    while True:
        current_poll_time = datetime.datetime.now(datetime.timezone.utc)
        # VyOS router metrics — split by category
        system_data = []   # node_* metrics → RouterSystemMetrics
        routing_data = []  # frr_* metrics  → RouterRoutingSeries
        # Traffic-agent metrics → TrafficFlowSeries
        traffic_data = []

        try:
            # Chain VyOS and traffic-agent series into one stream
            series_data = itertools.chain(
                fetch_all_vyos_metrics(mon_client, project_name, last_poll_time),
                fetch_traffic_agent_metrics(mon_client, project_name),
            )

            for series in series_data:
                job_name = series.resource.labels.get("job", "")

                # ── VyOS router metrics ────────────────────────────────────
                if job_name == "vyos-lab":
                    router_name = series.metric.labels.get("router_name")
                    if not router_name:
                        continue

                    logger.debug(f"Processing VyOS series: {series.metric.type}")

                    parts = series.metric.type.split('/')
                    raw_metric_name = parts[-2]
                    prom_type = parts[-1]
                    # Classify explicitly. This used to be `is_system =
                    # startswith("node_")` with everything else falling through
                    # to the routing table, which is how three exporter
                    # self-metrics ended up keyed as routing metrics and
                    # colliding — 7.8b. A metric that is neither node_* nor
                    # frr_* has no home here, so skip it and say so once rather
                    # than filing it under whichever branch happens to be last.
                    is_system = raw_metric_name.startswith("node_")
                    is_routing = raw_metric_name.startswith("frr_")
                    if not (is_system or is_routing):
                        if raw_metric_name not in _unclassified_metrics:
                            _unclassified_metrics.add(raw_metric_name)
                            logger.warning(
                                "Skipping %s: neither a node_* system metric nor an "
                                "frr_* routing metric, so it has no table. Remove it "
                                "from SELECTED_METRICS or give it one.",
                                raw_metric_name,
                            )
                        continue
                    labels_json = json.dumps(dict(series.metric.labels))

                    for point in series.points:
                        # After ALIGN_MEAN (gauge) or ALIGN_RATE (counter), Cloud
                        # Monitoring always returns a DOUBLE value regardless of the
                        # original metric's value type.  Using double_value directly
                        # avoids the proto-plus incompatibility with WhichOneof and
                        # correctly stores genuine zero readings (0.0 ≠ "no value").
                        val = float(point.value.double_value)
                        ts = point.interval.end_time

                        if is_system:
                            # OSPF/system metrics use "device" label for the interface
                            iface = series.metric.labels.get("device") or series.metric.labels.get("iface", "")
                            system_data.append((
                                ts,
                                router_name,
                                raw_metric_name,
                                iface or '',
                                prom_type,
                                val,
                                labels_json,
                            ))
                        else:
                            # FRR routing metrics. Every label below is part of
                            # the RouterRoutingSeries primary key, because every
                            # one of them distinguishes two series that would
                            # otherwise overwrite each other on write:
                            #   safi      unicast vs vpn, on all frr_bgp_* metrics
                            #   iface     one OSPF adjacency per interface
                            #   area      OSPF area (single-area today, not always)
                            #   collector which FRR sub-collector reported up
                            routing_data.append((
                                ts,
                                router_name,
                                raw_metric_name,
                                series.metric.labels.get("vrf", ""),
                                series.metric.labels.get("afi", ""),
                                series.metric.labels.get("safi", ""),
                                series.metric.labels.get("peer", ""),
                                series.metric.labels.get("iface", ""),
                                series.metric.labels.get("area", ""),
                                series.metric.labels.get("collector", ""),
                                prom_type,
                                val,
                                labels_json,
                            ))

                # ── Traffic-agent device metrics ───────────────────────────
                elif job_name == "traffic-agents":
                    flow_id  = series.metric.labels.get("flow_id")
                    role     = series.metric.labels.get("role")
                    protocol = series.metric.labels.get("protocol")

                    if not flow_id:
                        continue

                    # flow_id convention: "{traffictest_name}_{source_device}"
                    # e.g. "dev1-to-hub-tcp_dev1"      → node_name = "dev1"
                    #      "dev1-hub-tcp-bidir_dev1_rev" → node_name = "dev1"
                    # Strip the "_rev" suffix (bidirectional reverse flows) before
                    # splitting, so we always extract the device name correctly.
                    clean_flow_id = flow_id[:-4] if flow_id.endswith("_rev") else flow_id
                    node_name = clean_flow_id.rsplit("_", 1)[-1]

                    logger.debug(
                        f"Processing traffic-agent series: {series.metric.type} "
                        f"flow={flow_id} role={role} protocol={protocol}"
                    )

                    parts = series.metric.type.split('/')
                    raw_metric_name = parts[-2]
                    prom_type = parts[-1]

                    for point in series.points:
                        val = float(point.value.double_value)
                        traffic_data.append((
                            flow_id,            # flow_id — leading PK column
                            point.interval.end_time,
                            node_name,          # e.g. "dev1"
                            raw_metric_name,    # e.g. "traffic_agent_throughput_bps"
                            role or '',         # "source" | "destination" — PK column
                            prom_type,          # "gauge" or "counter"
                            val,
                            json.dumps({
                                "flow_id":  flow_id,
                                "role":     role,
                                "protocol": protocol,
                            }),
                        ))

                else:
                    logger.debug(f"Skipping series from unknown job '{job_name}'")

        except Exception as e:
            logger.error(f"Error during API poll: {e}")

        # Batch write VyOS system metrics (node_*) to RouterSystemMetrics (transport database)
        _write_batches(
            db_container, lock,
            table="RouterSystemMetrics",
            columns=("timestamp", "router_name", "metric_name", "interface", "metric_type", "value", "labels"),
            data=system_data,
            current_poll_time=current_poll_time,
            db_key='transport_db',
            key_columns=4,
        )

        # Batch write VyOS routing metrics (frr_*) to RouterRoutingSeries (transport database)
        _write_batches(
            db_container, lock,
            table="RouterRoutingSeries",
            columns=(
                "timestamp", "router_name", "metric_name", "vrf", "afi", "safi",
                "peer_ip", "iface", "area", "collector", "metric_type", "value", "labels",
            ),
            data=routing_data,
            current_poll_time=current_poll_time,
            db_key='transport_db',
            key_columns=10,
        )

        # Batch write traffic-agent metrics to TrafficFlowSeries (service database)
        _write_batches(
            db_container, lock,
            table="TrafficFlowSeries",
            columns=(
                "flow_id", "timestamp", "node_name", "metric_name", "role",
                "metric_type", "value", "labels",
            ),
            data=traffic_data,
            current_poll_time=current_poll_time,
            db_key='service_db',
            key_columns=5,
        )

        last_poll_time = current_poll_time
        logger.debug(f"Sleeping for {POLL_INTERVAL} seconds")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_worker()
