# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import kopf
import logging
import json
import os
import threading
import time
from google.cloud import spanner
from utils.compute import sanitize_k8s_body

logger = logging.getLogger(__name__)

SQL_TEMPLATES = {
    # ComputeHost SCD
    'get_active_host': (
        "SELECT name, network_name, ip_address, mgmt_ip, gateway, phase, config, "
        "attached_router, attached_router_interface "
        "FROM ComputeHost WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_host': (
        "UPDATE ComputeHost SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_host': (
        "INSERT ComputeHost "
        "(id, name, network_name, ip_address, mgmt_ip, gateway, extra_interfaces, "
        "phase, config, attached_router, attached_router_interface, "
        "valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @network_name, @ip_address, @mgmt_ip, @gateway, "
        "@extra_interfaces, @phase, @config, @attached_router, "
        "@attached_router_interface, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_host': (
        "UPDATE ComputeHost SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),

    # Device -> CE-router attribution (migration plan 7.1). Both sides live in
    # infratopology-db, so this is a plain read inside the same transaction.
    # The extraction is done in SQL rather than in Python on purpose. The
    # Spanner client hands a JSON column back as a JsonObject, which subclasses
    # dict even when the underlying value is an array -- so an isinstance(...,
    # list) test on it silently yields nothing, and the device ends up with no
    # router. JSON_VALUE returns plain STRINGs and there is nothing to guess.
    'get_network_routers': (
        "SELECT ARRAY_LENGTH(JSON_QUERY_ARRAY(connected_routers)) AS router_count, "
        "JSON_VALUE(connected_routers, '$[0].router_name') AS router_name, "
        "JSON_VALUE(connected_routers, '$[0].interface') AS iface "
        "FROM LinuxNetwork WHERE name = @name AND valid_end_ts IS NULL LIMIT 1"
    ),
    'get_hosts_on_network': (
        "SELECT id, name, network_name, ip_address, mgmt_ip, gateway, "
        "extra_interfaces, phase, config, attached_router, attached_router_interface "
        "FROM ComputeHost WHERE network_name = @network_name AND valid_end_ts IS NULL"
    ),

    # LinuxNetwork SCD
    'get_active_linuxnetwork': (
        "SELECT name, namespace, network_type, cidr, gateway, bandwidth, phase, config "
        "FROM LinuxNetwork WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'close_linuxnetwork': (
        "UPDATE LinuxNetwork SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
    'insert_linuxnetwork': (
        "INSERT LinuxNetwork "
        "(id, name, namespace, network_type, cidr, gateway, bandwidth, connected_routers, "
        "phase, config, valid_start_ts, valid_end_ts) "
        "VALUES (@id, @name, @namespace, @network_type, @cidr, @gateway, @bandwidth, "
        "@connected_routers, @phase, @config, PENDING_COMMIT_TIMESTAMP(), NULL)"
    ),
    'delete_linuxnetwork': (
        "UPDATE LinuxNetwork SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() "
        "WHERE id = @id AND valid_end_ts IS NULL"
    ),
}

DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))


def spanner_connect():
    spanner_client = spanner.Client(disable_builtin_metrics=True)
    instance = spanner_client.instance('networktopology-instance')
    db_name = os.environ.get('GOOGLE_SPANNER_INFRA_DATABASE', 'infratopology-db')
    return instance.database(db_name)


lock = threading.Lock()
db_container = {'db': spanner_connect()}


def _check_connection():
    try:
        if not db_container['db'].exists():
            with lock:
                if not db_container['db'].exists():
                    db_container['db'] = spanner_connect()
    except Exception as e:
        with lock:
            try:
                db_container['db'] = spanner_connect()
            except Exception:
                pass


# ------------------------------------------
# Device -> CE-router attribution (migration plan 7.1)
# ------------------------------------------
def _derive_attachment(connected_routers):
    """Pick the single router terminating a device LAN, if there is one.

    `LinuxNetwork.connected_routers` is a JSON list of
    ``{router_name, interface, ip_address}``. Every `lan-*` bridge has exactly
    one entry -- the CE router serving those devices -- and that is the case
    this denormalization exists for.

    A bridge with several routers on it (`mgmt` has eighteen, and a
    point-to-point core link has two) has no single terminating router, so this
    returns nothing rather than picking one. A scalar column cannot honestly
    represent that relationship; callers who need the full set read
    connected_routers, which is what the MCP servers' utils/hosts.py does.

    This takes the list straight off the CR spec. The Spanner-side equivalent is
    `get_network_routers`, which does the same reduction in SQL.
    """
    if isinstance(connected_routers, str):
        try:
            connected_routers = json.loads(connected_routers)
        except (ValueError, TypeError):
            return '', ''
    if not isinstance(connected_routers, list):
        return '', ''

    entries = [r for r in connected_routers
               if isinstance(r, dict) and r.get('router_name')]
    if len(entries) != 1:
        return '', ''
    return entries[0].get('router_name', ''), entries[0].get('interface', '')


def _network_attachment(transaction, network_name):
    """Read a LinuxNetwork's connected_routers and reduce it to one router."""
    if not network_name:
        return '', ''
    rows = list(transaction.execute_sql(
        SQL_TEMPLATES['get_network_routers'],
        params={'name': network_name},
        param_types={'name': spanner.param_types.STRING}
    ))
    if not rows:
        # The Device was synced before its LinuxNetwork. Not an error: the next
        # LinuxNetwork sync back-fills every host sitting on it.
        return '', ''

    router_count, router_name, iface = rows[0]
    if router_count != 1:
        return '', ''
    return router_name or '', iface or ''


def _reattach_hosts_on_network(transaction, network_name, router, interface):
    """Re-stamp attached_router on the active hosts of one network.

    Runs on every LinuxNetwork sync, not only when the network row changed. It
    is what closes the ordering hole in the other direction: a Device synced
    before its LinuxNetwork existed has no attachment, and this is what gives it
    one. Writes only where the value actually differs, so a steady-state
    reconcile costs one read of a table with a handful of rows.
    """
    rows = list(transaction.execute_sql(
        SQL_TEMPLATES['get_hosts_on_network'],
        params={'network_name': network_name},
        param_types={'network_name': spanner.param_types.STRING}
    ))

    for row in rows:
        (host_id, host_name, net_name, ip_address, mgmt_ip, gateway,
         extra_interfaces, phase, config, ex_router, ex_interface) = row
        if (ex_router or '') == router and (ex_interface or '') == interface:
            continue

        transaction.execute_update(
            SQL_TEMPLATES['close_host'],
            params={'id': host_id},
            param_types={'id': spanner.param_types.STRING}
        )
        transaction.execute_update(
            SQL_TEMPLATES['insert_host'],
            params={
                'id': host_id,
                'name': host_name,
                'network_name': net_name,
                'ip_address': ip_address,
                'mgmt_ip': mgmt_ip,
                'gateway': gateway,
                'extra_interfaces': extra_interfaces,
                'phase': phase,
                'config': config,
                'attached_router': router,
                'attached_router_interface': interface,
            },
            param_types=_HOST_PARAM_TYPES
        )
        logger.info(
            f"Reattached ComputeHost {host_id} on {network_name}: "
            f"{ex_router or '(none)'} -> {router or '(none)'}"
        )


_HOST_PARAM_TYPES = {
    'id': spanner.param_types.STRING,
    'name': spanner.param_types.STRING,
    'network_name': spanner.param_types.STRING,
    'ip_address': spanner.param_types.STRING,
    'mgmt_ip': spanner.param_types.STRING,
    'gateway': spanner.param_types.STRING,
    'extra_interfaces': spanner.param_types.JSON,
    'phase': spanner.param_types.STRING,
    'config': spanner.param_types.JSON,
    'attached_router': spanner.param_types.STRING,
    'attached_router_interface': spanner.param_types.STRING,
}


# ------------------------------------------
# Sync ComputeHost (SCD Type 2)
# Written by Device CRD events.
# ------------------------------------------
async def sync_compute_host(body, spec, name, uid, logger):
    """Sync a Device CRD to the ComputeHost Spanner table (SCD Type 2)."""
    logger.debug(f"Syncing ComputeHost for Device {name}")

    host_id = f"host:{name}"

    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown')
    mgmt_ip = status_obj.get('mgmt_ip') or spec.get('mgmt_ip', '')

    network_name = spec.get('network_name', '')
    ip_address = spec.get('ip_address', '')
    gateway = spec.get('gateway', '')
    vlan = spec.get('vlan')

    extra_interfaces = json.dumps(spec.get('extra_interfaces', []))

    body_dict = dict(body) if not isinstance(body, dict) else body
    config_json = json.dumps(sanitize_k8s_body(body_dict))

    def sql_upsert(transaction):
        # The CE router terminating this device's LAN, read from the
        # LinuxNetwork row in this same database (migration plan 7.1).
        attached_router, attached_iface = _network_attachment(
            transaction, network_name)

        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_host'],
            params={'id': host_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            (ex_name, ex_net, ex_ip, ex_mgmt, ex_gw, ex_phase, ex_config,
             ex_router, ex_iface) = row
            if (ex_name == name and ex_net == network_name and
                    ex_ip == ip_address and ex_mgmt == mgmt_ip and
                    ex_gw == gateway and ex_phase == phase and
                    ex_config == config_json and
                    (ex_router or '') == attached_router and
                    (ex_iface or '') == attached_iface):
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_host'],
                    params={'id': host_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_host'],
                params={
                    'id': host_id,
                    'name': name,
                    'network_name': network_name,
                    'ip_address': ip_address,
                    'mgmt_ip': mgmt_ip,
                    'gateway': gateway,
                    'extra_interfaces': extra_interfaces,
                    'phase': phase,
                    'config': config_json,
                    'attached_router': attached_router,
                    'attached_router_interface': attached_iface,
                },
                param_types=_HOST_PARAM_TYPES
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced ComputeHost {host_id} (phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync ComputeHost {host_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def delete_compute_host(uid, name=None):
    """Close the active ComputeHost row in Spanner (SCD Type 2)."""
    host_id = f"host:{name}" if name else uid
    logger.debug(f"Deleting ComputeHost {host_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_host'],
            params={'id': host_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed ComputeHost {host_id}")
    except Exception as e:
        error_msg = f"Failed to delete ComputeHost {host_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


# ------------------------------------------
# Sync LinuxNetwork (SCD Type 2)
# Written by LinuxNetwork CRD events.
# ------------------------------------------
async def sync_linux_network(body, spec, name, uid, logger):
    """Sync a LinuxNetwork CRD to the LinuxNetwork Spanner table (SCD Type 2)."""
    logger.debug(f"Syncing LinuxNetwork {name}")

    network_id = f"linuxnetwork:{name}"
    namespace = body.get('metadata', {}).get('namespace', '')

    status_obj = body.get('status', {})
    phase = status_obj.get('phase', 'unknown')

    network_type = spec.get('network_type', '')
    cidr = spec.get('cidr', '')
    gateway = spec.get('gateway', '')
    bandwidth = spec.get('bandwidth', '')
    connected_routers = json.dumps(spec.get('connected_routers', []))

    body_dict = dict(body) if not isinstance(body, dict) else body
    config_json = json.dumps(sanitize_k8s_body(body_dict))

    def sql_upsert(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_linuxnetwork'],
            params={'id': network_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            (ex_name, ex_ns, ex_type, ex_cidr,
             ex_gw, ex_bw, ex_phase, ex_config) = row
            if (ex_name == name and ex_ns == namespace and
                    ex_type == network_type and ex_cidr == cidr and
                    ex_gw == gateway and ex_bw == bandwidth and
                    ex_phase == phase and ex_config == config_json):
                need_insert = False
            else:
                transaction.execute_update(
                    SQL_TEMPLATES['close_linuxnetwork'],
                    params={'id': network_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_linuxnetwork'],
                params={
                    'id': network_id,
                    'name': name,
                    'namespace': namespace,
                    'network_type': network_type,
                    'cidr': cidr,
                    'gateway': gateway,
                    'bandwidth': bandwidth,
                    'connected_routers': connected_routers,
                    'phase': phase,
                    'config': config_json,
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'namespace': spanner.param_types.STRING,
                    'network_type': spanner.param_types.STRING,
                    'cidr': spanner.param_types.STRING,
                    'gateway': spanner.param_types.STRING,
                    'bandwidth': spanner.param_types.STRING,
                    'connected_routers': spanner.param_types.JSON,
                    'phase': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON,
                }
            )

        # Push the attribution down to the hosts on this bridge. Unconditional:
        # a Device synced before this LinuxNetwork existed has no attachment
        # yet, and this is the only thing that gives it one (7.1).
        router, iface = _derive_attachment(spec.get('connected_routers', []))
        _reattach_hosts_on_network(transaction, name, router, iface)

    try:
        db_container['db'].run_in_transaction(sql_upsert)
        logger.info(f"Synced LinuxNetwork {network_id} (phase={phase})")
    except Exception as e:
        error_msg = f"Failed to sync LinuxNetwork {network_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


async def delete_linux_network(uid, name=None):
    """Close the active LinuxNetwork row in Spanner (SCD Type 2)."""
    network_id = f"linuxnetwork:{name}" if name else uid
    logger.debug(f"Deleting LinuxNetwork {network_id}")

    def sql_close(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_linuxnetwork'],
            params={'id': network_id},
            param_types={'id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_close)
        logger.info(f"Closed LinuxNetwork {network_id}")
    except Exception as e:
        error_msg = f"Failed to delete LinuxNetwork {network_id}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)
