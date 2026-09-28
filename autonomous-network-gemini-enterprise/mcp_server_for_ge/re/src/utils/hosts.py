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

    FROM Device d JOIN PhysicalInterface pi ON pi.id = d.interface_id

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

So no cross-database query is needed. This is also strictly more informative
than the join it replaces: the old one yielded only the interface id, whereas
``connected_routers`` carries the router name *and* the interface name.

The only thing genuinely lost is that attribution is now via the shared bridge
rather than a point-to-point interface reference, so a host on a bridge with
several routers attaches to all of them. Both accessors below therefore return
*lists* of routers, and it is the caller's decision how to disambiguate.
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
    Spanner -- and this helper used to answer ``[]`` for all of them, which made
    ``hosts_on_router`` and ``routers_for_hosts`` return nothing at all without
    raising. ``serialize()`` is the documented way to recover the real value.
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


def _network_router_map(network_names):
    """Map bridge name -> list of {router_name, interface, ip_address}."""
    if not network_names:
        return {}

    sql = ("SELECT name, connected_routers FROM LinuxNetwork "
           "WHERE name IN UNNEST(@names) AND valid_end_ts IS NULL")
    with get_database('infra').snapshot() as snap:
        rows = list(snap.execute_sql(
            sql,
            params={'names': sorted(set(network_names))},
            param_types={'names': spanner.param_types.Array(spanner.param_types.STRING)},
        ))

    out = {}
    for name, connected in rows:
        out[name] = [r for r in _as_list(connected) if isinstance(r, dict)]
    return out


def hosts_on_router(router_name):
    """Return the compute hosts reachable from ``router_name``.

    Replaces the ``Device JOIN PhysicalInterface WHERE i.router_id = @rid``
    traversal. ``router_name`` is the bare name (``p1``), not the graph id
    (``router:p1``) -- ``connected_routers`` stores bare names.

    Each result carries ``via``, the bridge and router interface it was matched
    through, which the original join could not report.
    """
    db = get_database('infra')

    with db.snapshot() as snap:
        net_rows = list(snap.execute_sql(
            "SELECT name, connected_routers FROM LinuxNetwork WHERE valid_end_ts IS NULL"
        ))

    # bridge name -> the interface this router uses to reach it
    bridges = {}
    for name, connected in net_rows:
        for entry in _as_list(connected):
            if isinstance(entry, dict) and entry.get('router_name') == router_name:
                bridges[name] = entry.get('interface')

    if not bridges:
        return []

    with db.snapshot() as snap:
        host_rows = list(snap.execute_sql(
            "SELECT id, name, network_name, phase, extra_interfaces FROM ComputeHost "
            "WHERE valid_end_ts IS NULL"
        ))

    hosts = []
    for host_id, name, network_name, phase, extra in host_rows:
        attached = [n for n in _host_networks(network_name, extra) if n in bridges]
        if not attached:
            continue
        hosts.append({
            "id": host_id,
            "name": name,
            "network": network_name,
            # 'phase' is the six-DB name for what the UI still calls 'status'.
            "status": phase,
            "via": [{"network": n, "router_interface": bridges[n]} for n in attached],
        })
    return hosts


def routers_for_hosts(host_names):
    """Map each host name to the routers it attaches to.

    Replaces the ``Device -> PhysicalInterface -> PhysicalRouter`` resolution
    in ``simulate_traffic_addition``. Returns
    ``{host_name: [router_name, ...]}``; hosts that resolve to nothing are
    omitted, so callers can detect "not found" by absence.
    """
    if not host_names:
        return {}

    sql = ("SELECT name, network_name, extra_interfaces FROM ComputeHost "
           "WHERE name IN UNNEST(@names) AND valid_end_ts IS NULL")
    with get_database('infra').snapshot() as snap:
        rows = list(snap.execute_sql(
            sql,
            params={'names': list(host_names)},
            param_types={'names': spanner.param_types.Array(spanner.param_types.STRING)},
        ))

    per_host = {name: _host_networks(net, extra) for name, net, extra in rows}
    net_map = _network_router_map([n for nets in per_host.values() for n in nets])

    out = {}
    for name, nets in per_host.items():
        routers = []
        for n in nets:
            for entry in net_map.get(n, []):
                r = entry.get('router_name')
                if r and r not in routers:
                    routers.append(r)
        if routers:
            out[name] = routers
    return out
