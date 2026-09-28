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

import datetime
import json
import logging
import re
from typing import Annotated, List
from google.cloud import spanner
from mcp.types import ToolAnnotations
from utils.globals import networkagent_mcp
from utils.database import LOG_DOMAINS, get_database

logger = logging.getLogger(__name__)

# KgLogEntryNode is no longer a single table: transport, core and radio each
# have their own copy in their own database, and Spanner cannot UNION across
# databases. Every read here therefore fans out and merges in Python.
#
# The columns changed too. 'node_name' and the 'labels' JSON are gone; 'source'
# and 'message' are now first-class columns, so most of the old digging through
# labels/content is unnecessary. 'content' is kept only as a fallback for rows
# whose message was never extracted at ingest time.
_COLUMNS = "timestamp, severity, source, message, content"

_STOPWORDS = {
    "the", "and", "for", "are", "any", "all", "was", "were", "what", "which",
    "show", "list", "find", "give", "tell", "get", "log", "logs", "entry",
    "entries", "from", "with", "that", "this", "have", "has", "had", "did",
    "there", "anything", "wrong", "about", "into", "over", "last", "recent",
    "please", "between", "during", "related",
}

def _keywords(request: str) -> List[str]:
    if not request:
        return []
    tokens = re.findall(r'[A-Za-z0-9_./:-]+', request.lower())
    seen = []
    for t in tokens:
        if len(t) >= 3 and t not in _STOPWORDS and t not in seen:
            seen.append(t)
    return seen[:12]

def _sort_key(row):
    """Sort merged rows newest-first regardless of timestamp representation."""
    ts = row[1][0]
    if isinstance(ts, str):
        try:
            ts = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    if isinstance(ts, datetime.datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return ts
    return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def _fan_out(where_sql, params, param_types, limit):
    """Run one log query per log-bearing domain and merge the results.

    Each domain is asked for its own ``limit`` newest rows; merging and
    re-truncating then yields the globally newest ``limit``. A domain that
    fails (database absent, table empty at deploy time) is logged and skipped
    rather than failing the whole query -- a transport answer with no radio
    rows is far more useful than no answer.

    Returns a list of ``(domain, row)`` tuples, newest first.
    """
    sql = f"SELECT {_COLUMNS} FROM KgLogEntryNode {where_sql} ORDER BY timestamp DESC LIMIT {limit}"
    merged = []
    for domain in LOG_DOMAINS:
        try:
            with get_database(domain).snapshot(multi_use=True) as snapshot:
                rows = snapshot.execute_sql(sql, params=params or None,
                                            param_types=param_types or None)
                merged.extend((domain, row) for row in rows)
        except Exception as e:
            logger.warning("Log query failed for %s domain: %s", domain, e)
    merged.sort(key=_sort_key, reverse=True)
    return merged[:limit]

def _message_of(message, content):
    """Prefer the extracted message column, falling back to the raw payload."""
    if message:
        return message
    try:
        return json.loads(content).get('textPayload', '')
    except Exception:
        return ''

@networkagent_mcp.tool()
def fetch_log_entries() -> str:
    """Fetch recent logs related to network and connectivity services."""
    log_entries = []
    for domain, row in _fan_out("", {}, {}, 50):
        timestamp, severity, source, message, content = row
        ts_str = timestamp if isinstance(timestamp, str) else timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
        log_entries.append({
            'timestamp': ts_str,
            'severity': severity,
            'message': _message_of(message, content),
            'source': source or '-',
            'domain': domain,
            'details': {}
        })
    return json.dumps(log_entries)

@networkagent_mcp.tool()
def query_log_entries(
    request: Annotated[str, "Natural language log query request"],
    severity_levels: Annotated[str, "Comma or space separated severity levels"] = None,
    start_time: Annotated[str, "Start time string YYYY-MM-DD HH:MM:SS"] = None,
    end_time: Annotated[str, "End time string YYYY-MM-DD HH:MM:SS"] = None
) -> str:
    """Query recent logs related to network services via keyword search."""
    logger.info(f"query_log_entries: {request}, {severity_levels}, {start_time}, {end_time}")
    format_str = "%Y-%m-%d %H:%M:%S"
    start_time_obj = datetime.datetime.strptime(start_time, format_str) if start_time else None
    end_time_obj = datetime.datetime.strptime(end_time, format_str) if end_time else None

    try:
        where_clauses = []
        params = {}
        param_types = {}

        keywords = _keywords(request)
        if keywords:
            kw_clauses = []
            for i, kw in enumerate(keywords):
                p = f"kw{i}"
                # 'node_name' is gone; 'source' is the column that now carries
                # the emitting component, and 'message' is worth matching too.
                kw_clauses.append(
                    f"(LOWER(content) LIKE @{p} OR LOWER(source) LIKE @{p} OR LOWER(message) LIKE @{p})"
                )
                params[p] = f"%{kw}%"
                param_types[p] = spanner.param_types.STRING
            where_clauses.append("(" + " OR ".join(kw_clauses) + ")")

        if severity_levels:
            levels = [level.strip().upper() for level in severity_levels.replace(',', ' ').split() if level.strip()]
            if levels:
                where_clauses.append("severity IN UNNEST(@severities)")
                params["severities"] = levels
                param_types["severities"] = spanner.param_types.Array(spanner.param_types.STRING)

        if start_time_obj:
            where_clauses.append("timestamp >= @start_time")
            params["start_time"] = start_time_obj
            param_types["start_time"] = spanner.param_types.TIMESTAMP

        if end_time_obj:
            where_clauses.append("timestamp <= @end_time")
            params["end_time"] = end_time_obj
            param_types["end_time"] = spanner.param_types.TIMESTAMP

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        formatted = []
        today = datetime.date.today()

        for domain, row in _fan_out(where_sql, params, param_types, 30):
            timestamp, severity, source, message, content = row
            message = _message_of(message, content)

            dt = datetime.datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else timestamp
            ts_str = dt.strftime('%H:%M:%S') if hasattr(dt, 'date') and dt.date() == today else dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(dt, 'strftime') else str(timestamp)
            formatted.append(f"`[{ts_str}] {domain} {severity}: {message}`")

        # Say "nothing matched" out loud. An empty string and the error return
        # below were both indistinguishable to the calling agent, which then had
        # to guess whether the log store was empty, the keywords missed, or the
        # query had failed -- and it guessed wrong. The keyword search is a
        # substring match over a small vocabulary, so a miss is the common case,
        # not an exceptional one.
        if not formatted:
            return (
                "No log entries matched that query. The search is a keyword "
                "substring match over the log source and message text, so try "
                "fewer or broader terms, or call fetch_log_entries for the most "
                "recent entries regardless of content."
            )
        return "\n".join(formatted)
    except Exception as e:
        logger.error(f"Log query error: {e}")
        return f"Log query failed: {e}"
