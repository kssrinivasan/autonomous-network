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

"""Host-to-router attribution across the infra/transport split.

Under the single-database schema this was one SQL join::

    FROM Device d LEFT JOIN PhysicalInterface i ON d.interface_id = i.id

``Device`` is now ``ComputeHost`` in ``infratopology-db`` while
``PhysicalInterface`` stayed in ``transporttopology-db``, and Spanner cannot
join across databases. ``ComputeHost`` has no ``interface_id`` either -- it has
``extra_interfaces``, a JSON list of ``{network_name, ip_address, gateway}``
that names *bridges*, not interfaces.

The relation is nonetheless fully recoverable, and **entirely inside infra**,
because ``LinuxNetwork.connected_routers`` records which routers attach to each
bridge and over which interface::

    ComputeHost.network_name ─┐
                              ├─→ LinuxNetwork.name
    extra_interfaces[].network_name ─┘
                                        └─→ connected_routers[]
                                              {router_name, interface, ip_address}

The only thing genuinely lost is that attribution is now via the shared bridge
rather than a point-to-point interface reference, so a host on a bridge with
several routers attaches to all of them. :func:`fetch_hosts` therefore returns
a *list* of attachments per host and leaves disambiguation to the caller.

This mirrors ``mcp_server_for_ge/re/src/utils/hosts.py``; see
``utils/database.py`` for why the dashboard cannot share that copy.
"""

import json
import logging

from google.cloud import spanner

from utils.database import get_database

logger = logging.getLogger(__name__)


def _as_list(value):
    """Spanner JSON columns come back as str, JsonObject, list or None.

    JsonObject is the case that bites. It subclasses ``dict`` even when the
    underlying value is an array, so ``isinstance(value, list)`` is False for
    every ``connected_routers`` and ``extra_interfaces`` column read out of
    Spanner -- and this helper used to answer ``[]`` for all of them. That gave
    every host an empty ``attachments`` list, which is why the dashboard drew
    the ``d*`` devices as isolated nodes with no edge to their CE router.
    ``serialize()`` is the documented way to recover the real value.
    """
    if value is None:
        return []
    serialize = getattr(value, 'serialize', None)
    if callable(serialize):
        value = serialize()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def _host_networks(network_name, extra_interfaces):
    """Every bridge a host is attached to: the primary plus any extras."""
    names = []
    if network_name:
        names.append(network_name)
    for iface in _as_list(extra_interfaces):
        if isinstance(iface, dict) and iface.get('network_name'):
            names.append(iface['network_name'])
    return names


def _validity(alias, target_timestamp):
    """SCD Type 2 predicate for ``alias``, current-only or as-of a timestamp."""
    if target_timestamp is None:
        return f"{alias}.valid_end_ts IS NULL"
    return (
        f"{alias}.valid_start_ts <= @target_timestamp "
        f"AND ({alias}.valid_end_ts > @target_timestamp OR {alias}.valid_end_ts IS NULL)"
    )


def router_id(router_name):
    """Graph id of ``router_name``.

    The ids are generated, not stored anywhere infra can see: the controller
    writes ``router:<name>`` and ``router:<name>:interface:<if-name>`` (see
    ``controller/src/graph/transport_tasks.py``). Deriving them here is what
    lets an infra-only read still hand the UI transport ids, with no
    cross-database query.
    """
    return f"router:{router_name}"


def interface_id(router_name, interface_name):
    """Graph id of ``interface_name`` on ``router_name``. See :func:`router_id`."""
    return f"router:{router_name}:interface:{interface_name}"


def fetch_hosts(target_timestamp=None, name=None, host_id=None):
    """Return every compute host with the routers it attaches to.

    Args:
        target_timestamp: Optional ``datetime`` for an SCD Type 2 time-travel
            read. ``None`` returns the currently valid rows.
        name: Optional host name to filter on.
        host_id: Optional ``ComputeHost.id`` to filter on.

    Returns:
        A list of dicts, one per host, each carrying the ``ComputeHost``
        columns plus ``attachments``: a list of ``{network, router_name,
        router_id, interface, interface_id, ip_address}`` describing every
        bridge/router the host reaches.
    """
    db = get_database('infra')

    params = {}
    param_types = {}
    if target_timestamp is not None:
        params['target_timestamp'] = target_timestamp
        param_types['target_timestamp'] = spanner.param_types.TIMESTAMP

    host_where = _validity('h', target_timestamp)
    if name:
        host_where += " AND h.name = @host_name"
        params['host_name'] = name
        param_types['host_name'] = spanner.param_types.STRING
    if host_id:
        host_where += " AND h.id = @host_id"
        params['host_id'] = host_id
        param_types['host_id'] = spanner.param_types.STRING

    host_sql = (
        "SELECT h.id, h.name, h.network_name, h.ip_address, h.mgmt_ip, h.gateway, "
        "h.phase, h.extra_interfaces, TO_JSON_STRING(h.config) "
        "FROM ComputeHost h WHERE " + host_where
    )
    net_sql = (
        "SELECT n.name, n.connected_routers FROM LinuxNetwork n WHERE "
        + _validity('n', target_timestamp)
    )

    with db.snapshot(multi_use=True) as snapshot:
        host_rows = list(snapshot.execute_sql(
            host_sql,
            params=params or None,
            param_types=param_types or None,
        ))
        # The bridge table is read whole rather than filtered to the hosts'
        # networks: it is small, and one read serves every host.
        net_rows = list(snapshot.execute_sql(
            net_sql,
            params={'target_timestamp': params['target_timestamp']} if target_timestamp is not None else None,
            param_types={'target_timestamp': param_types['target_timestamp']} if target_timestamp is not None else None,
        ))

    routers_by_network = {
        name: [r for r in _as_list(connected) if isinstance(r, dict)]
        for name, connected in net_rows
    }

    hosts = []
    for row in host_rows:
        hid, hname, network_name, ip_address, mgmt_ip, gateway, phase, extra, config = row
        attachments = []
        for net in _host_networks(network_name, extra):
            for entry in routers_by_network.get(net, []):
                rname = entry.get('router_name')
                iface = entry.get('interface')
                attachments.append({
                    'network': net,
                    'router_name': rname,
                    'router_id': router_id(rname) if rname else None,
                    'interface': iface,
                    'interface_id': interface_id(rname, iface) if rname and iface else None,
                    'ip_address': entry.get('ip_address'),
                })
        try:
            parsed_config = json.loads(config) if config else {}
        except (ValueError, TypeError):
            parsed_config = {}
        hosts.append({
            'id': hid,
            'name': hname,
            'network_name': network_name,
            'ip_address': ip_address,
            'mgmt_ip': mgmt_ip,
            'gateway': gateway,
            # 'phase' is the six-DB name for what the UI still calls 'status'.
            'status': phase,
            'config': parsed_config,
            'attachments': attachments,
        })
    return hosts
