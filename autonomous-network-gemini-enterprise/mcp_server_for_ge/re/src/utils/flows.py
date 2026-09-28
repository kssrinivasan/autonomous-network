# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Traffic flows and their measurements, out of ``servicetopology-db``.

Until this module existed no agent tool read the service database at all. The
flow tools answered from the ``TrafficTest`` custom resources in the cluster,
which is *intent* -- what someone asked for -- while the measured throughput,
loss and latency of those same flows accumulated in Spanner unread. An agent
asking "is this flow degraded?" could only be told what bandwidth was
requested.

**The two tables do not share an id space, and that is the whole difficulty.**

``TrafficFlow`` is one row per TrafficTest, written by the controller
(``controller/src/graph/service_tasks.py``)::

    id = "flow:<test-name>"          e.g. flow:d1-red-d2-red-bidir

``TrafficFlowSeries`` is one series per *direction per source agent per end*,
written by the metrics collector from the ``flow_id`` Prometheus label the
traffic agents export (``services/metricscollector/src/main.py``)::

    flow_id = "<test-name>_<source-device>"        forward direction
    flow_id = "<test-name>_<source-device>_rev"    reverse, bidirectional only

Both ends of a leg report under the same ``flow_id`` and the same
``node_name`` -- the collector derives ``node_name`` from the ``flow_id`` -- so
``role`` is the only column that tells the sender's readings from the
receiver's. It is part of the primary key of ``TrafficFlowSeries`` for exactly
that reason; in the superseded ``TrafficFlowMetrics`` it was not, and the two
ends silently overwrote each other (migration plan 7.8a).

So one ``TrafficFlow`` row corresponds to between one and 2N metric series for
a test with N source devices, and ``f.id = m.flow_id`` -- the join the
``FlowHasMetrics_Edge`` view shipped with -- matches nothing, ever. Splitting
on the first underscore is exact rather than a heuristic: a test name is a
Kubernetes object name, and RFC 1123 does not permit ``_`` in one, so the first
underscore in a metric ``flow_id`` is always the boundary the collector put
there.

:func:`fetch_traffic_flows` and :func:`fetch_flow_measurements` are the two
reads, and :func:`metric_flow_id` is the bridge.
"""

import json
import logging

from google.cloud import spanner

from utils.database import get_database

logger = logging.getLogger(__name__)

#: ``TrafficFlow.id`` prefix written by the controller.
FLOW_ID_PREFIX = 'flow:'

#: Prometheus prefix on every traffic-agent metric. Stripped from the keys of
#: the ``metrics`` dict so callers read ``throughput_bps`` rather than
#: ``traffic_agent_throughput_bps``.
_METRIC_PREFIX = 'traffic_agent_'

#: Which end of a flow leg actually measures each metric, by the short
#: (prefix-stripped) name. The agent exports the full set from both ends, but a
#: sender observes no loss, delay or jitter and a receiver observes nothing
#: offered; on the running lab the wrong end reports 0 for the directional
#: throughputs and a flat 50.0 for packet loss. A metric absent from this map is
#: taken from whichever end reported it most recently.
_ROLE_FOR_METRIC = {
    'throughput_bps':       'source',
    'throughput_sent_bps':  'source',
    'bytes_sent_total':     'source',
    'throughput_recv_bps':  'destination',
    'bytes_received_total': 'destination',
    'latency_ms':           'destination',
    'jitter_ms':            'destination',
    'packet_loss_pct':      'destination',
}

#: TrafficFlow phases that mean traffic is on the wire right now. The column
#: holds the CR phase lowercased; the controller also uses pending, deploying,
#: completed and failed, none of which carry user traffic.
ACTIVE_PHASES = frozenset({'running'})

#: How far back to look for a "current" measurement. The collector polls once a
#: minute, so anything older than this means the series has stopped reporting.
DEFAULT_WINDOW_MINUTES = 10


def metric_flow_id(test_name, source_device, reverse=False):
    """Build the ``TrafficFlowMetrics.flow_id`` for one direction of one source.

    The inverse of :func:`split_metric_flow_id`. This is the id the traffic
    agent exports and the collector stores -- not ``TrafficFlow.id``.
    """
    suffix = '_rev' if reverse else ''
    return f"{test_name}_{source_device}{suffix}"


def split_metric_flow_id(flow_id):
    """Decompose a ``TrafficFlowMetrics.flow_id``.

    Returns ``(test_name, source_device, direction)`` where direction is
    ``'forward'`` or ``'reverse'``. A ``flow_id`` with no underscore predates
    the per-source convention; it is reported as the test name with an unknown
    source rather than dropped.
    """
    direction = 'forward'
    rest = flow_id
    if rest.endswith('_rev'):
        direction = 'reverse'
        rest = rest[:-len('_rev')]
    test_name, sep, source_device = rest.partition('_')
    return test_name, (source_device if sep else None), direction


def _parse_json(raw):
    """Parse a ``TO_JSON_STRING`` column, tolerating nulls and junk."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def fetch_traffic_flows(names=None, active_only=False):
    """Return the currently valid ``TrafficFlow`` rows.

    Args:
        names: Optional list of test names (not ``flow:`` ids) to restrict to.
        active_only: Keep only flows whose phase is in :data:`ACTIVE_PHASES`.

    Returns:
        A list of dicts carrying the table columns plus ``spec`` and
        ``status``, unpacked from the ``config`` column -- the controller
        stores the whole sanitized TrafficTest body there, so everything the
        old Kubernetes read supplied is present without leaving Spanner.
    """
    where = ['valid_end_ts IS NULL']
    params = {}
    param_types = {}
    if names:
        where.append('name IN UNNEST(@names)')
        params['names'] = sorted(set(names))
        param_types['names'] = spanner.param_types.Array(spanner.param_types.STRING)
    if active_only:
        where.append('LOWER(phase) IN UNNEST(@phases)')
        params['phases'] = sorted(ACTIVE_PHASES)
        param_types['phases'] = spanner.param_types.Array(spanner.param_types.STRING)

    sql = (
        "SELECT id, name, src_device_id, dst_device_id, phase, valid_start_ts, "
        "TO_JSON_STRING(config) "
        "FROM TrafficFlow WHERE " + " AND ".join(where) + " ORDER BY name"
    )

    with get_database('service').snapshot() as snap:
        rows = list(snap.execute_sql(
            sql,
            params=params or None,
            param_types=param_types or None,
        ))

    flows = []
    for flow_id, name, src, dst, phase, started, config in rows:
        body = _parse_json(config)
        flows.append({
            'id': flow_id,
            'name': name,
            'src_device_id': src,
            'dst_device_id': dst,
            'phase': phase,
            'active': (phase or '').lower() in ACTIVE_PHASES,
            'started_at': started.isoformat() if started else None,
            'spec': body.get('spec', {}) or {},
            'status': body.get('status', {}) or {},
        })
    return flows


def fetch_flow_measurements(test_names=None, window_minutes=DEFAULT_WINDOW_MINUTES):
    """Return the most recent measurement of every live flow direction.

    Args:
        test_names: Optional list of TrafficTest names. Each is matched against
            the metric ``flow_id`` by prefix, so it picks up every source
            device and both directions.
        window_minutes: Only consider samples this recent.

    Returns:
        ``{test_name: [series, ...]}``, each series being ``{flow_id,
        source_device, direction, node_name, roles, protocol, timestamp,
        metrics}``. ``metrics`` maps the metric name with the
        ``traffic_agent_`` prefix stripped to its latest value, taken from the
        end of the leg that measures it. ``roles`` lists the ends that
        reported, so a leg heard from only one end is visible as such.

    The window is reduced in Python rather than with an ``ARRAY_AGG ... LIMIT
    1`` per group: at one poll per minute over ten metrics and a handful of
    flows this is a few thousand narrow rows, and the timestamp index bounds
    the scan.
    """
    where = ['timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window MINUTE)']
    params = {'window': int(window_minutes)}
    param_types = {'window': spanner.param_types.INT64}
    if test_names:
        where.append(
            'EXISTS (SELECT 1 FROM UNNEST(@prefixes) p WHERE STARTS_WITH(flow_id, p))'
        )
        params['prefixes'] = sorted({f"{n}_" for n in test_names})
        param_types['prefixes'] = spanner.param_types.Array(spanner.param_types.STRING)

    sql = (
        "SELECT flow_id, node_name, role, metric_name, value, timestamp, "
        "TO_JSON_STRING(labels) "
        "FROM TrafficFlowSeries WHERE " + " AND ".join(where)
    )

    with get_database('service').snapshot() as snap:
        rows = list(snap.execute_sql(sql, params=params, param_types=param_types))

    # (flow_id, node_name) -> series, keeping the newest sample per metric.
    #
    # One series is one *leg* of the flow, which both ends report on. The key
    # deliberately excludes role: a caller wants one reading per leg, not two
    # half-readings. Each metric within the leg is taken from the end that
    # measures it, per _ROLE_FOR_METRIC.
    series_by_key = {}
    for flow_id, node_name, role, metric_name, value, ts, labels in rows:
        key = (flow_id, node_name)
        series = series_by_key.get(key)
        if series is None:
            test_name, source_device, direction = split_metric_flow_id(flow_id)
            label_map = _parse_json(labels)
            series = series_by_key[key] = {
                'flow_id': flow_id,
                'test_name': test_name,
                'source_device': source_device,
                'direction': direction,
                'node_name': node_name,
                'roles': set(),
                'protocol': label_map.get('protocol'),
                'timestamp': None,
                'metrics': {},
                '_ts': {},
            }
        if role:
            series['roles'].add(role)
        short = metric_name[len(_METRIC_PREFIX):] if metric_name.startswith(_METRIC_PREFIX) else metric_name
        wanted = _ROLE_FOR_METRIC.get(short)
        if wanted is not None and role != wanted:
            continue
        if series['_ts'].get(short) is None or ts > series['_ts'][short]:
            series['_ts'][short] = ts
            series['metrics'][short] = value

    by_test = {}
    for series in series_by_key.values():
        newest = max(series.pop('_ts').values(), default=None)
        series['timestamp'] = newest.isoformat() if newest else None
        series['roles'] = sorted(series['roles'])
        by_test.setdefault(series['test_name'], []).append(series)

    for entries in by_test.values():
        entries.sort(key=lambda s: (s['source_device'] or '', s['direction']))
    return by_test


def summarize_measurement(series_list):
    """Collapse the directions of one flow into a headline reading.

    Agents mostly want two numbers per flow: is it moving traffic, and is it
    losing any. Returns ``None`` when nothing reported in the window, which is
    itself the answer -- a flow the controller calls running with no series is
    a flow whose agents have gone quiet.

    **Loss, latency and jitter are read only from the directions that received
    something.** A traffic agent exports the full metric set for every series
    it owns, including the send-only ones, and a send-only series reports
    ``packet_loss_pct = 100`` because it received no packets back -- it is
    measuring nothing, not measuring total loss. Taking the worst reading
    across all directions therefore marked every healthy bidirectional flow as
    100% lost. Only a receiving series has a loss figure that means anything.

    :func:`fetch_flow_measurements` now resolves each metric to the end that
    measures it, so the send-only readings never reach this function. The
    ``_receiving`` filter is kept because a leg whose receiver never reported
    still has no loss figure worth quoting -- it is missing, not zero.
    """
    if not series_list:
        return None

    def _receiving(series):
        return (series['metrics'].get('bytes_received_total') or 0) > 0

    def _agg(name, fn, over=None):
        values = [s['metrics'].get(name) for s in (over if over is not None else series_list)]
        values = [v for v in values if v is not None]
        return fn(values) if values else None

    receiving = [s for s in series_list if _receiving(s)]

    return {
        'directions': len(series_list),
        # Every direction is measured exactly once, so the sum is the total
        # the flow is putting on the wire and the peak is its busiest leg.
        'throughput_bps': _agg('throughput_bps', sum),
        'peak_direction_bps': _agg('throughput_bps', max),
        'packet_loss_pct': _agg('packet_loss_pct', max, over=receiving),
        'latency_ms': _agg('latency_ms', max, over=receiving),
        'jitter_ms': _agg('jitter_ms', max, over=receiving),
        'measured_from_directions': len(receiving),
        'active_sessions': _agg('active_sessions', max),
        'reporting': any(s['metrics'].get('flow_running') for s in series_list),
        'last_seen': max((s['timestamp'] for s in series_list if s['timestamp']), default=None),
    }
