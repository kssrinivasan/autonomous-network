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

"""Spanner connection routing across the six-database schema.

The topology used to live in one database (``networktopology-db``) with one
property graph (``networkGraph``). It is now split across six databases, five
of which carry their own property graph:

    transport  transporttopology-db   transportGraph
    infra      infratopology-db       infraGraph
    core       coretopology-db        coreGraph
    radio      radiotopology-db       radioGraph
    service    servicetopology-db     serviceGraph
    ops        agentops-db            (no graph)

Two consequences matter for anyone writing a query here:

* **A SQL join cannot cross a database boundary.** If a query needs rows from
  two domains, fan out with one call per domain and join in Python.
* **There is no ``networkGraph``.** Graph queries must name the graph of the
  domain they are addressing; use :func:`graph_name`.

``get_database()`` defaults to ``transport`` so that transport-only tools read
naturally, but pass the domain explicitly wherever the answer is not obvious.

This mirrors ``mcp_server_for_ge/re/src/utils/database.py``. The two are
separate because the dashboard backend builds from the repo root and the tool
servers build from their own directory, so neither can import the other.
"""

import logging
import os

from agent_library import get_credentials
from google.cloud import spanner

logger = logging.getLogger(__name__)

SPANNER_INSTANCE = os.getenv('GOOGLE_SPANNER_INSTANCE', 'networktopology-instance')

# domain -> (env var, default database name, property graph name or None)
_DOMAINS = {
    'transport': ('GOOGLE_SPANNER_TRANSPORT_DATABASE', 'transporttopology-db', 'transportGraph'),
    'infra':     ('GOOGLE_SPANNER_INFRA_DATABASE',     'infratopology-db',     'infraGraph'),
    'core':      ('GOOGLE_SPANNER_CORE_DATABASE',      'coretopology-db',      'coreGraph'),
    'radio':     ('GOOGLE_SPANNER_RADIO_DATABASE',     'radiotopology-db',     'radioGraph'),
    'service':   ('GOOGLE_SPANNER_SERVICE_DATABASE',   'servicetopology-db',   'serviceGraph'),
    'ops':       ('GOOGLE_SPANNER_OPS_DATABASE',       'agentops-db',          None),
}

#: Every routable domain, in a stable order.
DOMAINS = tuple(_DOMAINS)

#: Domains whose log tables are worth fanning out across (see tools/logs.py).
LOG_DOMAINS = ('transport', 'core', 'radio')

_client = None
_databases = {}


def _spec(domain):
    try:
        return _DOMAINS[domain]
    except KeyError:
        raise ValueError(
            f"unknown Spanner domain {domain!r}; expected one of {', '.join(DOMAINS)}"
        ) from None


def database_name(domain):
    """Return the configured database name for ``domain``."""
    env_var, default, _ = _spec(domain)
    return os.getenv(env_var, default)


def graph_name(domain):
    """Return the property graph name for ``domain``.

    Raises ``ValueError`` for ``ops``, which has no graph -- catching that at
    the call site is better than emitting a query against a graph that does
    not exist.
    """
    _, _, graph = _spec(domain)
    if graph is None:
        raise ValueError(f"domain {domain!r} has no property graph")
    return graph


def get_database(domain='transport'):
    """Return the Spanner database handle for ``domain``, creating it once.

    The Spanner client is shared; the database handles are cached per domain,
    so a long-lived process reuses one gRPC channel and one session pool per
    database instead of rebuilding them on every poll.
    """
    _spec(domain)  # validate before touching any global state

    global _client
    if _client is None:
        credentials, _project = get_credentials()
        _client = spanner.Client(credentials=credentials)

    if domain not in _databases:
        db_name = database_name(domain)
        _databases[domain] = _client.instance(SPANNER_INSTANCE).database(db_name)
        logger.info("Connected to %s database: %s/%s", domain, SPANNER_INSTANCE, db_name)

    return _databases[domain]


def reset_database(domain='transport'):
    """Drop the cached handle for ``domain`` and reconnect.

    Used by the not-found reconnect path in tools/metrics.py: a database that
    was recreated underneath us leaves a stale handle whose sessions all fail,
    and only a fresh handle recovers.
    """
    _spec(domain)
    _databases.pop(domain, None)
    return get_database(domain)
