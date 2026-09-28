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

"""Log reads for the dashboard.

``KgLogEntryNode`` is no longer a single table: transport, core and radio each
have their own copy in their own database, and Spanner cannot UNION across
databases. Every read here therefore fans out and merges in Python.

The columns changed too. ``node_name`` is gone; ``source`` is the column that
now carries the emitting component, and ``message`` is a first-class column
alongside the raw ``content`` payload.
"""

import datetime
import logging
import os

from google.cloud import spanner

from utils.database import LOG_DOMAINS, get_database

logger = logging.getLogger(__name__)

#: Deployment name of the controller, which is also its container name and so
#: the ``source`` value the log collector records for automation logs.
NETWORK_OPERATOR = os.getenv('NETWORK_OPERATOR', 'vyosnetwork-operator')


_EPOCH = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _sort_key(entry):
    """Sort merged entries newest-first from their ISO timestamp string.

    Rows arrive already sorted within a domain but interleaved across domains,
    and an unparseable timestamp sorts last rather than raising.
    """
    try:
        ts = datetime.datetime.fromisoformat(entry['timestamp'])
    except (TypeError, ValueError):
        return _EPOCH
    return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)


def fetch_log_entries(source_filter: str = 'all', severity_filter: str = 'all', row_limit: int = 50, minutes_limit: int = None) -> list:
  """
  Fetch recent logs related to network and connectivity services.

  Args:
    source_filter: One of 'all', 'vyos_syslog', or 'automation'.
      - 'all'         : no source filter applied
      - 'vyos_syslog' : only rows where source starts with 'vyos:'
      - 'automation'  : only rows emitted by the controller
    severity_filter: One of 'all', 'critical', 'error', 'warning', 'info', 'debug'.
      - 'all'      : no severity filter applied
      - any other  : only rows where UPPER(severity) = UPPER(value)
    row_limit: maximum rows to fetch
    minutes_limit: optional time filter in minutes

  Returns:
    A list of dicts representing each log entry, newest first, merged across
    the transport, core and radio databases.
  """
  params = {}
  param_types = {}
  conditions = []

  if source_filter == 'vyos_syslog':
    conditions.append("source LIKE @source_prefix")
    params['source_prefix'] = 'vyos:%'
    param_types['source_prefix'] = spanner.param_types.STRING
  elif source_filter == 'automation':
    conditions.append("source = @source_exact")
    params['source_exact'] = NETWORK_OPERATOR
    param_types['source_exact'] = spanner.param_types.STRING

  if severity_filter and severity_filter != 'all':
    conditions.append("UPPER(severity) = @severity_val")
    params['severity_val'] = severity_filter.upper()
    param_types['severity_val'] = spanner.param_types.STRING

  if minutes_limit:
    conditions.append("timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @minutes MINUTE)")
    params['minutes'] = int(minutes_limit)
    param_types['minutes'] = spanner.param_types.INT64

  where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

  # Clamp row_limit to a safe maximum to avoid runaway queries. Each domain is
  # asked for its own safe_limit newest rows; merging and re-truncating then
  # yields the globally newest safe_limit.
  safe_limit = max(1, min(int(row_limit), 500))
  sql = (
    "SELECT timestamp, severity, source, message, content FROM KgLogEntryNode "
    f"{where_clause} ORDER BY timestamp DESC LIMIT {safe_limit}"
  )

  log_entries = []
  for domain in LOG_DOMAINS:
    try:
      with get_database(domain).snapshot() as snapshot:
        results = snapshot.execute_sql(
          sql,
          params=params if params else None,
          param_types=param_types if param_types else None,
        )
        for timestamp, severity, source, message, content in results:
          log_entries.append({
            'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
            'severity': severity,
            'source': source or '',
            'domain': domain,
            'message': message or content or '',
            'details': {}  # Empty details
          })
    except Exception as e:
      # A domain whose database is absent or whose table is still empty must
      # not sink the whole query: transport rows with no radio rows beats no
      # answer at all.
      logger.warning("Log query failed for %s domain: %s", domain, e)

  log_entries.sort(key=_sort_key, reverse=True)
  return log_entries[:safe_limit]

def delete_logs():
  """
  Deletes all records from the KgLogEntryNode table in every log-bearing
  database using Partitioned DML, which bypasses the 20,000 mutation-per-
  transaction limit and is safe for tables with large numbers of rows.

  Returns:
    bool: True if every domain was cleared, False if any of them failed.
  """
  ok = True
  for domain in LOG_DOMAINS:
    try:
      row_count = get_database(domain).execute_partitioned_dml(
        "DELETE FROM KgLogEntryNode WHERE TRUE"
      )
      logger.debug(f"Deleted ~{row_count} log entries from {domain} KgLogEntryNode")
    except Exception as e:
      logger.error("Spanner Delete error for %s domain: %s", domain, e)
      ok = False
  return ok
