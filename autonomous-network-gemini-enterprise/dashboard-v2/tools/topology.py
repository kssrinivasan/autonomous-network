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

from google.cloud import spanner
import logging
import json as json
import datetime
import re
import kubernetes
from kubernetes.client.rest import ApiException
from agent_library import get_client as get_k8s_client

from utils.database import get_database, graph_name
from utils.hosts import fetch_hosts

# Everything below reads the transport domain, which owns PhysicalRouter,
# PhysicalInterface, PhysicalLink and the VPN tables. The two exceptions are
# routed explicitly: compute hosts come from infra via utils/hosts.py, and
# clear_topology() reaches into infra and service as well.
GRAPH_NAME = graph_name('transport')

logger = logging.getLogger(__name__)


def _parse_iso_timestamp(timestamp_str: str) -> datetime.datetime:
    """
    Parse an ISO-8601 timestamp, tolerant of a UTC offset whose '+' was decoded
    to a space in transit.

    A query string like '?timestamp=2026-06-03T21:25:09+00:00' arrives with the
    '+' decoded to ' ' (URL '+' means space), yielding '...21:25:09 00:00', which
    datetime.fromisoformat() rejects. We restore the '+' before a trailing HH:MM
    offset. Also normalises a trailing 'Z' to '+00:00'.
    """
    s = timestamp_str.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    # Restore a numeric UTC offset whose '+' became a space (e.g. '... 00:00').
    s = re.sub(r'\s+(\d{2}:?\d{2})$', r'+\1', s)
    return datetime.datetime.fromisoformat(s)


def _parse_bandwidth_bps(bw_str: str):
    """Convert a CRD bandwidth string to bits-per-second (int), or None.

    Handles the VyOSInfrastructure CRD pattern: ^([0-9]+[kmg]?bit|unlimited)$
    Examples:
        '1gbit'     → 1_000_000_000
        '100mbit'   → 100_000_000
        '10kbit'    → 10_000
        '500bit'    → 500
        'unlimited' → None
    """
    if not bw_str or bw_str.lower() == 'unlimited':
        return None
    s = bw_str.lower().strip()
    try:
        if s.endswith('gbit'):
            return int(s[:-4]) * 1_000_000_000
        if s.endswith('mbit'):
            return int(s[:-4]) * 1_000_000
        if s.endswith('kbit'):
            return int(s[:-4]) * 1_000
        if s.endswith('bit'):
            return int(s[:-3])
    except ValueError:
        pass
    logger.warning("Could not parse bandwidth string '%s'", bw_str)
    return None

#####################################################################################
# Graph stuff
#####################################################################################

# utils/database.py caches one handle per domain, so repeated calls reuse the
# same gRPC channel and session pool instead of rebuilding them on every poll.
_database = get_database('transport')

_use_legacy_embedding_schema = False
try:
    with _database.snapshot() as snapshot:
        # Try to query node_type to see if it exists
        snapshot.execute_sql("SELECT node_type FROM NodeEmbedding LIMIT 1").to_dict_list()
    logger.info("Spanner: detected modern NodeEmbedding schema (with node_type)")
except Exception as e:
    logger.info("Spanner: node_type not found in NodeEmbedding. Using legacy schema fallback.")
    _use_legacy_embedding_schema = True

#####################################################################################
# Physical Topology
#####################################################################################

def fetch_physical_topology(timestamp_str: str = None):
    """
    Fetch the physical network topology including routers, their interfaces, 
    links, connectivity, and embeddings data.
    
    Args:
        timestamp_str: Optional ISO-8601 timestamp string. If provided, fetches historical
                      snapshot at that point in time. If None, fetches latest.
    
    Returns:
        dict: Physical topology with nodes (routers) and connections (links),
              including embeddings data (MSE and RCA) for each router and interface
    """
    logger.debug(f"Fetching physical network topology (timestamp={timestamp_str})")
    
    topology = {
        'nodes': [],
        'connections': []
    }
    
    try:
        database = _database

        # Determine the timestamp to use for queries
        target_timestamp = None
        if timestamp_str:
            try:
                target_timestamp = _parse_iso_timestamp(timestamp_str)
                logger.debug(f"Using historical timestamp: {target_timestamp}")
            except ValueError as e:
                logger.error(f"Invalid timestamp format: {e}")
                return {'nodes': [], 'connections': [], 'error': 'Invalid timestamp format'}
        
        # GQL query to get all routers with their interfaces and links
        # Supports both CURRENT (target_timestamp=None) and HISTORICAL (SCD Type 2) topology
        if target_timestamp:
            # Historical mode: Use SCD Type 2 time-travel
            ts_filter = f"""
                router.valid_start_ts <= TIMESTAMP('{target_timestamp.isoformat()}')
                AND (router.valid_end_ts > TIMESTAMP('{target_timestamp.isoformat()}') OR router.valid_end_ts IS NULL)
            """
            interface_filter = f"""
                interface.valid_start_ts <= TIMESTAMP('{target_timestamp.isoformat()}')
                AND (interface.valid_end_ts > TIMESTAMP('{target_timestamp.isoformat()}') OR interface.valid_end_ts IS NULL)
                AND interface.name != 'eth0'
            """
            link_filter = f"""
                link.valid_start_ts <= TIMESTAMP('{target_timestamp.isoformat()}')
                AND (link.valid_end_ts > TIMESTAMP('{target_timestamp.isoformat()}') OR link.valid_end_ts IS NULL)
            """
        else:
            # Current mode: Only get currently valid entities
            ts_filter = "router.valid_end_ts IS NULL"
            interface_filter = "interface.valid_end_ts IS NULL AND interface.name != 'eth0'"
            link_filter = "link.valid_end_ts IS NULL"
        
        gql_query = f"""
            GRAPH {GRAPH_NAME}
            MATCH (router:PhysicalRouter)
            WHERE {ts_filter}
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            WHERE {interface_filter}
            OPTIONAL MATCH (interface) -[:ConnectsTo]-> (link:PhysicalLink)
            WHERE {link_filter}
            OPTIONAL MATCH (link) -[:LinkedTo]-> (remote_interface:PhysicalInterface)
            WHERE {interface_filter}
            OPTIONAL MATCH (remote_interface) <-[:HasInterface]- (remote_router:PhysicalRouter)
            WHERE {ts_filter}
            RETURN 
                router.id AS router_id,
                router.name AS router_name,
                router.role AS router_role,
                router.status AS router_status,
                router.location_city AS router_city,
                router.location_lat AS router_lat,
                router.location_lon AS router_lon,
                interface.id AS interface_id,
                interface.name AS interface_name,
                link.id AS link_id,
                link.name AS link_name,
                remote_router.id AS remote_router_id,
                remote_router.name AS remote_router_name,
                remote_interface.name AS remote_interface_name
        """
        
        logger.debug("Executing GQL query for physical topology")
        
        routers = {}
        connections_set = set()
        
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            
            for row in results:
                router_id = row[0]
                router_name = row[1]
                router_role = row[2]
                router_status = row[3]
                router_city = row[4]
                router_lat = row[5]
                router_lon = row[6]
                interface_id = row[7]
                interface_name = row[8]
                link_id = row[9]
                link_name = row[10]
                remote_router_id = row[11]
                remote_router_name = row[12]
                remote_interface_name = row[13]
                
                # Add router to nodes if not already present
                if router_id not in routers:
                    router_location = {}
                    if router_city:
                        router_location['city'] = router_city
                    if router_lat is not None:
                        router_location['latitude'] = router_lat
                    if router_lon is not None:
                        router_location['longitude'] = router_lon
                    
                    routers[router_id] = {
                        'id': router_id,
                        'name': router_name,
                        'role': router_role if router_role else 'unknown',
                        'status': router_status if router_status else 'unknown',
                        'location': router_location if router_location else None,
                        'interfaces': []
                    }
                
                # Add interface info to router
                if interface_id and interface_id not in [iface['id'] for iface in routers[router_id]['interfaces']]:
                    routers[router_id]['interfaces'].append({
                        'id': interface_id,
                        'name': interface_name
                    })
                
                # Add connection if we have a link to another router
                if link_id and remote_router_id and router_id != remote_router_id:
                    # Create a sorted tuple to avoid duplicate connections
                    connection_key = tuple(sorted([router_id, remote_router_id]))
                    if connection_key not in connections_set:
                        connections_set.add(connection_key)
                        topology['connections'].append({
                            'id': link_id,
                            'name': link_name if link_name else f"link-{link_id[:8]}",
                            'source_router_id': router_id,
                            'source_router_name': router_name,
                            'source_interface': interface_name,
                            'target_router_id': remote_router_id,
                            'target_router_name': remote_router_name,
                            'target_interface': remote_interface_name,
                            'link_bandwidth_bps': None,  # populated below via _add_link_bandwidths
                        })
        
        # Fetch link bandwidths and attach to connections
        logger.debug("Fetching link bandwidths from Spanner")
        _add_link_bandwidths(database, topology['connections'], target_timestamp)

        # Now fetch embeddings for all routers and their interfaces
        logger.debug(f"Fetching embeddings for {len(routers)} routers")
        _add_embeddings_to_routers(database, routers, target_timestamp)
        
        # Convert routers dict to list
        topology['nodes'] = list(routers.values())
        
        # Now fetch devices connected to routers
        logger.debug("Fetching devices connected to routers")
        _add_devices_to_topology(topology, target_timestamp)
        
        logger.debug(f"Retrieved {len(topology['nodes'])} nodes (routers + devices) and {len(topology['connections'])} connections with embeddings")
        return topology
        
    except Exception as e:
        logger.error(f"Error fetching physical topology: {e}", exc_info=True)
        return {'nodes': [], 'connections': [], 'error': str(e)}


def _add_embeddings_to_routers(database, routers, target_timestamp=None):
    """
    Add embeddings data to routers dict in-place.
    
    Args:
        database: Spanner database connection
        routers: Dict of routers keyed by router_id
        target_timestamp: Optional datetime for exact snapshot timestamp match
    """
    try:
        # Determine schema-specific column names
        score_col = "anomaly_score" if _use_legacy_embedding_schema else "hetgnn_score"
        expl_col = "labels" if _use_legacy_embedding_schema else "anomaly_explanation"
        node_type_filter = "" if _use_legacy_embedding_schema else "AND e.node_type = 'PhysicalRouter'"
        node_type_filter_iface = "" if _use_legacy_embedding_schema else "AND e.node_type = 'PhysicalInterface'"

        if target_timestamp:
            # Historical: Use EXACT timestamp match for snapshot
            router_embedding_query = f"""
                SELECT 
                    e.node_id,
                    e.{score_col},
                    TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                WHERE e.node_id IN UNNEST(@router_ids)
                  {node_type_filter}
                  AND e.timestamp = @target_timestamp
            """
            
            interface_embedding_query = f"""
                SELECT 
                    i.router_id,
                    e.node_id AS interface_id,
                    i.name AS interface_name,
                    e.{score_col},
                    TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE i.router_id IN UNNEST(@router_ids)
                  {node_type_filter_iface}
                  AND e.timestamp = @target_timestamp
            """
        else:
            # Latest embeddings
            router_embedding_query = f"""
                SELECT 
                    e.node_id,
                    e.{score_col},
                    TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                WHERE e.node_id IN UNNEST(@router_ids)
                  {node_type_filter}
                  AND e.timestamp = (
                      SELECT MAX(timestamp) FROM NodeEmbedding 
                      WHERE node_id = e.node_id
                  )
            """
            
            interface_embedding_query = f"""
                SELECT 
                    i.router_id,
                    e.node_id AS interface_id,
                    i.name AS interface_name,
                    e.{score_col},
                    TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE i.router_id IN UNNEST(@router_ids)
                  {node_type_filter_iface}
                  AND i.valid_end_ts IS NULL
                  AND e.timestamp = (
                      SELECT MAX(timestamp) FROM NodeEmbedding 
                      WHERE node_id = e.node_id
                  )
            """
        
        router_ids = list(routers.keys())
        params = {"router_ids": router_ids}
        param_types = {"router_ids": spanner.param_types.Array(spanner.param_types.STRING)}
        
        if target_timestamp:
            params["target_timestamp"] = target_timestamp
            param_types["target_timestamp"] = spanner.param_types.TIMESTAMP
        
        # Fetch router embeddings
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(router_embedding_query, params=params, param_types=param_types)
            seen_routers = set()
            
            for row in results:
                router_id = row[0]
                if target_timestamp and router_id in seen_routers:
                    continue  # Skip older entries, we want the latest before target
                seen_routers.add(router_id)
                
                if router_id in routers:
                    anomaly_explanation = None
                    if row[2]:  # anomaly_explanation at index 2
                        try:
                            anomaly_explanation = json.loads(row[2])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    # Store hetgnn_score (stgnn/dgat not present in current schema)
                    routers[router_id]['stgnn_score'] = None
                    routers[router_id]['dgat_score'] = None
                    routers[router_id]['hetgnn_score'] = row[1]
                    routers[router_id]['router_rca'] = anomaly_explanation
                    routers[router_id]['embedding_timestamp'] = row[3].isoformat() if row[3] else None
        
        # Fetch interface embeddings
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(interface_embedding_query, params=params, param_types=param_types)
            
            for row in results:
                router_id = row[0]
                interface_id = row[1]
                interface_name = row[2]
                hetgnn_score = row[3]
                anomaly_explanation_str = row[4]
                
                if router_id and router_id in routers:
                    if 'interface_mses' not in routers[router_id]:
                        routers[router_id]['interface_mses'] = {}
                    
                    anomaly_explanation = None
                    if anomaly_explanation_str:
                        try:
                            anomaly_explanation = json.loads(anomaly_explanation_str)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    routers[router_id]['interface_mses'][interface_id] = {
                        'name': interface_name,
                        'stgnn_score': None,
                        'dgat_score': None,
                        'hetgnn_score': hetgnn_score,
                        'rca': anomaly_explanation
                    }
        
        logger.debug(f"Added embeddings to {len([r for r in routers.values() if 'stgnn_score' in r])} routers")
        
    except Exception as e:
        logger.error(f"Error fetching embeddings: {e}", exc_info=True)
        # Continue without embeddings rather than failing


def _add_devices_to_topology(topology, target_timestamp=None):
    """
    Add compute hosts to topology dict in-place.

    Hosts live in infratopology-db and the interfaces they attach to live in
    transporttopology-db, so the old ``Device JOIN PhysicalInterface`` is gone.
    utils/hosts.py recovers the same relation from LinuxNetwork.connected_routers
    without leaving infra, and derives the transport graph ids from the naming
    convention -- see that module for why this is possible.

    Args:
        topology: Topology dict with 'nodes' list and 'connections' list
        target_timestamp: Optional datetime for exact snapshot timestamp match
    """
    try:
        hosts = fetch_hosts(target_timestamp=target_timestamp)

        for host in hosts:
            # A host on a bridge with several routers attaches to all of them.
            # The UI's node shape holds one interface, so the first attachment
            # becomes the primary and the rest are carried alongside it.
            attachments = host['attachments']
            primary = attachments[0] if attachments else {}

            topology['nodes'].append({
                'id': host['id'],
                'name': host['name'],
                'type': 'device',
                'interface_id': primary.get('interface_id'),
                'router_id': primary.get('router_id'),
                'network_name': host['network_name'],
                'ip_address': host['ip_address'],
                'gateway': host['gateway'],
                'vlan': host['config'].get('vlan'),
                'status': host['status'] or 'unknown',
                'attachments': attachments,
            })

            # One edge per attachment, so a multi-homed host draws every link
            # it actually has rather than only the first.
            for n, attachment in enumerate(attachments):
                if not (attachment.get('interface_id') or attachment.get('router_id')):
                    continue
                topology['connections'].append({
                    'id': f"device_conn:{host['id']}:{n}",
                    'name': f"{host['name']} -> {attachment.get('interface') or 'Interface'}",
                    'source_device_id': host['id'],
                    'source_device_name': host['name'],
                    'target_interface_id': attachment.get('interface_id'),
                    'target_router_id': attachment.get('router_id'),
                    'type': 'device_to_interface'
                })

        logger.debug(f"Added {len(hosts)} devices to topology")

    except Exception as e:
        logger.error(f"Error fetching devices: {e}", exc_info=True)
        # Continue without devices rather than failing


def fetch_router_details(router_id, timestamp_str: str = None):
    """
    Fetch detailed information for a specific router by ID.

    Args:
        router_id: The ID of the router to fetch
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches router state
                      at that point in time. If None, fetches latest (live mode).

    Returns:
        dict: Router details including interfaces, config, and location
    """
    logger.debug(f"Fetching router details for router_id: {router_id} (timestamp={timestamp_str})")

    try:
        database = _database

        # Determine the timestamp to use for queries
        target_timestamp = None
        if timestamp_str:
            try:
                target_timestamp = _parse_iso_timestamp(timestamp_str)
                logger.debug(f"Using historical timestamp: {target_timestamp}")
            except ValueError as e:
                logger.error(f"Invalid timestamp format: {e}")
                return {'error': 'Invalid timestamp format'}

        # Build timestamp filter
        if target_timestamp:
            router_filter = f"""
                router.valid_start_ts <= TIMESTAMP('{target_timestamp.isoformat()}')
                AND (router.valid_end_ts > TIMESTAMP('{target_timestamp.isoformat()}') OR router.valid_end_ts IS NULL)
            """
            interface_filter = f"""
                interface.valid_start_ts <= TIMESTAMP('{target_timestamp.isoformat()}')
                AND (interface.valid_end_ts > TIMESTAMP('{target_timestamp.isoformat()}') OR interface.valid_end_ts IS NULL)
                AND interface.name != 'eth0'
            """
        else:
            router_filter = "router.valid_end_ts IS NULL"
            interface_filter = "interface.valid_end_ts IS NULL AND interface.name != 'eth0'"

        # GQL query to get router details with all its interfaces
        gql_query = f"""
            GRAPH {GRAPH_NAME}
            MATCH (router:PhysicalRouter {{id: '{router_id}'}})
            WHERE {router_filter}
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            WHERE {interface_filter}
            RETURN
                router.id AS router_id,
                router.name AS router_name,
                router.vendor AS router_vendor,
                router.model AS router_model,
                router.role AS router_role,
                router.status AS router_status,
                router.location_city AS router_city,
                router.location_lat AS router_lat,
                router.location_lon AS router_lon,
                TO_JSON_STRING(router.config) AS router_config,
                interface.id AS interface_id,
                interface.name AS interface_name,
                interface.speed AS interface_speed,
                interface.media_type AS interface_media_type,
                interface.ip_address AS interface_ip,
                interface.mac_address AS interface_mac,
                interface.status AS interface_status
        """
        
        logger.debug("Executing GQL query for router details")
        
        router_detail = None
        interfaces = []
        
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            
            for row in results:
                # Build router details from first row
                if router_detail is None:
                    router_location = {}
                    if row[6]:  # router_city
                        router_location['city'] = row[6]
                    if row[7] is not None:  # router_lat
                        router_location['latitude'] = row[7]
                    if row[8] is not None:  # router_lon
                        router_location['longitude'] = row[8]
                    
                    router_config = {}
                    if row[9]:  # router_config
                        try:
                            router_config = json.loads(row[9])
                        except (json.JSONDecodeError, TypeError):
                            router_config = {}
                    
                    router_detail = {
                        'id': row[0],
                        'name': row[1],
                        'vendor': row[2] if row[2] else 'unknown',
                        'model': row[3] if row[3] else 'unknown',
                        'role': row[4] if row[4] else 'unknown',
                        'status': row[5] if row[5] else 'unknown',
                        'location': router_location if router_location else None,
                        'config': router_config,
                        'interfaces': []
                    }
                
                # Add interface if present
                if row[10]:  # interface_id
                    interfaces.append({
                        'id': row[10],
                        'name': row[11],
                        'speed': row[12],
                        'media_type': row[13],
                        'ip_address': row[14],
                        'mac_address': row[15],
                        'status': row[16] if row[16] else 'unknown'
                    })
        
        if router_detail is None:
            logger.warning(f"Router with ID {router_id} not found")
            return {'error': f'Router with ID {router_id} not found'}
        
        # Add unique interfaces to router details
        router_detail['interfaces'] = interfaces
        
        logger.debug(f"Retrieved details for router {router_id} with {len(interfaces)} interfaces")
        return router_detail
        
    except Exception as e:
        logger.error(f"Error fetching router details: {e}", exc_info=True)
        return {'error': str(e)}


def fetch_device_details(device_id, timestamp_str: str = None):
    """
    Fetch detailed information for a specific compute host by ID.

    Args:
        device_id: The ComputeHost id (``host:<name>``) to fetch
        timestamp_str: Optional ISO-8601 timestamp. If provided, fetches device state
                      at that point in time. If None, fetches latest (live mode).

    Returns:
        dict: Device details including network info, connected router, and config
    """
    logger.debug(f"Fetching device details for device_id: {device_id} (timestamp={timestamp_str})")

    try:
        target_timestamp = None
        if timestamp_str:
            try:
                target_timestamp = _parse_iso_timestamp(timestamp_str)
                logger.debug(f"Using historical timestamp: {target_timestamp}")
            except ValueError as e:
                logger.error(f"Invalid timestamp format: {e}")
                return {'error': 'Invalid timestamp format'}

        hosts = fetch_hosts(target_timestamp=target_timestamp, host_id=device_id)
        if not hosts:
            logger.warning(f"Device with ID {device_id} not found")
            return {'error': f'Device with ID {device_id} not found'}

        host = hosts[0]
        # See _add_devices_to_topology: the first attachment is the primary and
        # the full list is carried alongside for multi-homed hosts.
        attachments = host['attachments']
        primary = attachments[0] if attachments else {}

        logger.debug(f"Retrieved details for device {device_id}")
        return {
            'id': host['id'],
            'name': host['name'],
            'interface_id': primary.get('interface_id'),
            'network_name': host['network_name'] or 'unknown',
            'ip_address': host['ip_address'] or 'unknown',
            'mgmt_ip': host['mgmt_ip'],
            'gateway': host['gateway'] or 'unknown',
            'vlan': host['config'].get('vlan'),
            'status': host['status'] or 'unknown',
            'config': host['config'],
            'router_id': primary.get('router_id'),
            'interface_name': primary.get('interface'),
            'interface_ip': primary.get('ip_address'),
            'attachments': attachments,
        }

    except Exception as e:
        logger.error(f"Error fetching device details: {e}", exc_info=True)
        return {'error': str(e)}


def fetch_node_embeddings(node_id):
    """
    Fetch the latest embeddings for a router and its interfaces (all 3 GNN models).
    
    Args:
        node_id: The ID of the router to fetch embeddings for
        
    Returns:
        dict: Embeddings data including router embedding and interface embeddings with all 3 model scores
    """
    logger.debug(f"Fetching embeddings for node_id: {node_id}")
    
    try:
        database = _database

        score_col = "anomaly_score" if _use_legacy_embedding_schema else "hetgnn_score"
        emb_col = "embedding" if _use_legacy_embedding_schema else "hetgnn_embedding"
        expl_col = "labels" if _use_legacy_embedding_schema else "anomaly_explanation"
        node_type_col = "'PhysicalRouter' AS node_type" if _use_legacy_embedding_schema else "e.node_type"

        # Query to get the latest embedding for the router (hetgnn model)
        router_embedding_query = f"""
            SELECT 
                e.node_id,
                {node_type_col},
                e.{score_col},
                e.{emb_col},
                e.timestamp,
                TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation
            FROM NodeEmbedding e
            WHERE e.node_id = @node_id
            ORDER BY e.timestamp DESC
            LIMIT 1
        """
        
        # Query to get the latest embeddings for all interfaces of this router (hetgnn model)
        interface_embeddings_query = f"""
            SELECT 
                i.id AS interface_id,
                i.name AS interface_name,
                e.{score_col},
                e.{emb_col},
                e.timestamp,
                TO_JSON_STRING(e.{expl_col}) AS anomaly_explanation
            FROM PhysicalInterface i
            JOIN NodeEmbedding e ON i.id = e.node_id
            WHERE i.router_id = @node_id
              AND i.valid_end_ts IS NULL
              AND e.timestamp = (
                  SELECT MAX(timestamp) 
                  FROM NodeEmbedding 
                  WHERE node_id = i.id
              )
            ORDER BY i.name
        """
        
        result = {
            'node_id': node_id,
            'router_embedding': None,
            'interface_embeddings': []
        }
        
        params = {"node_id": node_id}
        param_types = {"node_id": spanner.param_types.STRING}
        
        # Fetch router embedding
        with database.snapshot() as snapshot:
            router_results = snapshot.execute_sql(
                router_embedding_query, 
                params=params, 
                param_types=param_types
            )
            
            for row in router_results:
                anomaly_explanation = None
                if row[5]:
                    try:
                        anomaly_explanation = json.loads(row[5])
                    except (json.JSONDecodeError, TypeError):
                        anomaly_explanation = None
                
                result['router_embedding'] = {
                    'node_id': row[0],
                    'node_type': row[1],
                    'stgnn_score': None,
                    'stgnn_embedding': None,
                    'dgat_score': None,
                    'dgat_embedding': None,
                    'hetgnn_score': row[2],
                    'hetgnn_embedding': row[3],
                    'timestamp': row[4].isoformat() if row[4] else None,
                    'anomaly_explanation': anomaly_explanation
                }
                break
        
        # Fetch interface embeddings
        with database.snapshot() as snapshot:
            interface_results = snapshot.execute_sql(
                interface_embeddings_query,
                params=params,
                param_types=param_types
            )
            
            for row in interface_results:
                anomaly_explanation = None
                if row[5]:
                    try:
                        anomaly_explanation = json.loads(row[5])
                    except (json.JSONDecodeError, TypeError):
                        anomaly_explanation = None
                
                result['interface_embeddings'].append({
                    'interface_id': row[0],
                    'interface_name': row[1],
                    'stgnn_score': None,
                    'stgnn_embedding': None,
                    'dgat_score': None,
                    'dgat_embedding': None,
                    'hetgnn_score': row[2],
                    'hetgnn_embedding': row[3],
                    'timestamp': row[4].isoformat() if row[4] else None,
                    'anomaly_explanation': anomaly_explanation
                })
        
        logger.debug(f"Retrieved embeddings for node {node_id}: router={result['router_embedding'] is not None}, interfaces={len(result['interface_embeddings'])}")
        return result
        
    except Exception as e:
        logger.error(f"Error fetching node embeddings: {e}", exc_info=True)
        return {'error': str(e)}


#: Tables clear_topology() empties, by the database that owns them. The old
#: single-database list split three ways: ``Device`` became infra's
#: ``ComputeHost``, ``TrafficFlow`` moved to service, and ``NodeEmbedding``
#: exists once per domain rather than once overall.
_TOPOLOGY_TABLES = {
    'transport': [
        # Order chosen to respect logical dependencies (children before parents
        # where possible), though Spanner has no FK enforcement.
        "BGPSession",
        "VRF",
        "L3VPNService",
        "Interface_Link",
        "Subnet_Association",
        "LogicalSubnet",
        "PhysicalLink",
        "PhysicalInterface",
        "PhysicalRouter",
        "NodeEmbedding",
    ],
    'infra': [
        "ComputeHost",
        "NodeEmbedding",
    ],
    'service': [
        "TrafficFlow",
        "NodeEmbedding",
    ],
}


def clear_topology():
    """
    Clears all physical and logical topology data from Spanner using Partitioned DML,
    which bypasses the 20,000 mutation-per-transaction limit.

    Tables cleared (physical + logical topology + derived GNN data):
      transport: PhysicalRouter, PhysicalInterface, PhysicalLink,
                 Interface_Link, Subnet_Association, LogicalSubnet,
                 L3VPNService, VRF, BGPSession, NodeEmbedding
      infra:     ComputeHost, NodeEmbedding
      service:   TrafficFlow, NodeEmbedding

    Tables NOT touched: the metrics tables, KgLogEntryNode, NetworkDescriptor

    Returns:
        bool: True if all deletes succeeded, False if any failed.
    """
    ok = True
    for domain, tables in _TOPOLOGY_TABLES.items():
        try:
            database = get_database(domain)
        except Exception as e:
            logger.error(f"Failed to reach the {domain} database: {e}", exc_info=True)
            ok = False
            continue
        for table in tables:
            try:
                row_count = database.execute_partitioned_dml(
                    f"DELETE FROM {table} WHERE TRUE"
                )
                logger.debug(f"Cleared ~{row_count} rows from {domain}.{table}")
            except Exception as e:
                # Keep going: a domain whose tables were never populated must
                # not leave the other domains half-cleared.
                logger.error(f"Failed to clear {domain}.{table}: {e}", exc_info=True)
                ok = False
    if ok:
        logger.debug("Successfully cleared all topology tables")
    return ok


def build_graph(database, edge_label=None):
    """
    Build the graph elements for the frontend.
    
    Args:
        database: Spanner database object (unused but kept for signature compatibility)
        edge_label: Optional label to filter edges (unused for now)
        
    Returns:
        tuple: (elements list, success boolean)
    """
    try:
        # For now, we only support physical topology which corresponds to 'network' view
        # TODO: Support 'resources' view if needed
        topology = fetch_physical_topology()
        elements = []
        
        if 'error' in topology:
             logger.error(f"Error in fetch_physical_topology: {topology['error']}")
             return [], False
        
        for node in topology.get('nodes', []):
            elements.append({
                'data': {
                    'id': node['id'],
                    'label': node['name'],
                    'type': 'router', 
                    'status': node.get('status', 'unknown'),
                    'role': node.get('role', 'unknown'),
                    'location': node.get('location')
                }
            })
            
        for conn in topology.get('connections', []):
            elements.append({
                'data': {
                    'id': conn['id'],
                    'source': conn['source_router_id'],
                    'target': conn['target_router_id'],
                    'label': conn.get('name', 'link')
                }
            })
            
        return elements, True
    except Exception as e:
        logger.error(f"Error building graph: {e}", exc_info=True)
        return [], False

#####################################################################################
# Anomalies & Snapshots
#####################################################################################

def _add_link_bandwidths(database, connections, target_timestamp=None):
    """Fetch PhysicalLink.bandwidth for each connection and attach as link_bandwidth_bps.

    Queries Spanner for the bandwidth string stored on each PhysicalLink row and
    converts it to bits-per-second using _parse_bandwidth_bps().  Results are
    written back into the connection dicts in-place.

    Args:
        database: Spanner database connection
        connections: list of connection dicts (each must have an 'id' key)
        target_timestamp: Optional datetime for SCD2 time-travel queries
    """
    link_ids = [c['id'] for c in connections if c.get('id')]
    if not link_ids:
        return

    try:
        if target_timestamp:
            query = """
                SELECT id, bandwidth
                FROM PhysicalLink
                WHERE id IN UNNEST(@link_ids)
                  AND valid_start_ts <= @ts
                  AND (valid_end_ts > @ts OR valid_end_ts IS NULL)
            """
            params = {'link_ids': link_ids, 'ts': target_timestamp}
            param_types = {
                'link_ids': spanner.param_types.Array(spanner.param_types.STRING),
                'ts': spanner.param_types.TIMESTAMP,
            }
        else:
            query = """
                SELECT id, bandwidth
                FROM PhysicalLink
                WHERE id IN UNNEST(@link_ids)
                  AND valid_end_ts IS NULL
            """
            params = {'link_ids': link_ids}
            param_types = {'link_ids': spanner.param_types.Array(spanner.param_types.STRING)}

        bw_map = {}
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=params, param_types=param_types)
            for row in results:
                link_id, bw_str = row[0], row[1]
                bw_map[link_id] = _parse_bandwidth_bps(bw_str)

        for conn in connections:
            conn_id = conn.get('id')
            if conn_id and conn_id in bw_map:
                conn['link_bandwidth_bps'] = bw_map[conn_id]

        logger.debug("Attached bandwidth to %d/%d connections", len(bw_map), len(link_ids))

    except Exception as e:
        logger.error("Error fetching link bandwidths: %s", e, exc_info=True)


def fetch_snapshots():
    """
    Fetch available snapshots from the Spanner database.

    Timestamps are derived from two sources and merged into a single sorted list:
      1. SCD2 topology entries  – distinct ``valid_start_ts`` values from
         ``PhysicalRouter`` (each value represents a point in time when the
         physical topology was written / updated).
      2. Network-metrics entries – distinct ``timestamp`` values from the
         ``RouterSystemMetrics`` table.

    NodeEmbedding timestamps are intentionally excluded; the timeslot slider
    should reflect when observable data (topology changes or metrics) was
    recorded, not when GNN embeddings were computed.
    """
    logger.debug("Fetching snapshots from SCD2 (PhysicalRouter) and RouterSystemMetrics")
    try:
        database = _database

        # UNION of SCD2 valid_start timestamps and router metric timestamps.
        # The outer SELECT DISTINCT deduplicates any coinciding values.
        query = """
            SELECT DISTINCT ts
            FROM (
                SELECT valid_start_ts AS ts FROM PhysicalRouter WHERE valid_start_ts IS NOT NULL
                UNION ALL
                SELECT timestamp AS ts FROM RouterSystemMetrics WHERE timestamp IS NOT NULL
            )
            ORDER BY ts DESC
            LIMIT 100
        """

        snapshots = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query)
            for row in results:
                ts = row[0]
                if ts:
                    snapshots.append(ts.isoformat())

        logger.debug(f"Returning {len(snapshots)} snapshot timestamps")
        return {"snapshots": snapshots}
    except Exception as e:
        logger.error(f"Failed to fetch snapshots: {e}", exc_info=True)
        return {"error": str(e)}

def fetch_anomalies(limit: int = 50, timestamp_str: str = None):
    """
    Fetch top anomalies from NodeEmbedding (using average of 3 model scores for ranking).
    """
    logger.debug(f"Fetching anomalies (limit={limit}, timestamp={timestamp_str})")
    try:
        database = _database

        score_col = "anomaly_score" if _use_legacy_embedding_schema else "hetgnn_score"
        expl_col = "labels" if _use_legacy_embedding_schema else "anomaly_explanation"
        node_type_col = "CASE WHEN r.id IS NOT NULL THEN 'PhysicalRouter' WHEN i.id IS NOT NULL THEN 'PhysicalInterface' ELSE 'Unknown' END AS node_type" if _use_legacy_embedding_schema else "e.node_type"

        params = {"limit": limit}
        param_types = {"limit": spanner.param_types.INT64}
        
        if timestamp_str:
            try:
                ts = _parse_iso_timestamp(timestamp_str)
                params["timestamp"] = ts
                param_types["timestamp"] = spanner.param_types.TIMESTAMP
                
                query = f"""
                    SELECT 
                        e.node_id, 
                        {node_type_col},
                        e.{score_col},
                        e.{score_col} AS avg_score,
                        e.{expl_col} AS root_cause, 
                        COALESCE(r.name, i.name) as name, 
                        e.timestamp
                    FROM NodeEmbedding e
                    LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                    LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                    WHERE e.timestamp = @timestamp
                    ORDER BY avg_score DESC
                    LIMIT @limit
                """
            except ValueError:
                return {"error": "Invalid timestamp format"}
        else:
            query = f"""
                SELECT 
                    e.node_id, 
                    {node_type_col},
                    e.{score_col},
                    e.{score_col} AS avg_score,
                    e.{expl_col} AS root_cause, 
                    COALESCE(r.name, i.name) as name, 
                    e.timestamp
                FROM NodeEmbedding e
                LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE e.timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
                ORDER BY avg_score DESC
                LIMIT @limit
            """
            
        anomalies = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=params, param_types=param_types)
            for row in results:
                anomalies.append({
                    "node_id": row[0],
                    "node_type": row[1],
                    "stgnn_score": None,
                    "dgat_score": None,
                    "hetgnn_score": row[2],
                    "anomaly_score": row[3],  # hetgnn_score used as anomaly score
                    "root_cause": row[4],
                    "name": row[5] if row[5] else "Unknown",
                    "timestamp": row[6].isoformat() if row[6] else None
                })
                
        return {"anomalies": anomalies}
    except Exception as e:
        logger.error(f"Failed to fetch anomalies: {e}", exc_info=True)
        return {"error": str(e)}

#####################################################################################
# VyOSL3VPN & TrafficTest (Kubernetes CRDs)
#####################################################################################

def fetch_vpns(namespace: str = "network") -> list:
    """
    Fetch all VyOSL3VPN custom resources from Kubernetes and return a
    normalised summary list suitable for the dashboard.

    Returns:
        list of dicts with keys:
            name, phase, message, routers (list of str), underlay_ref
    """
    logger.debug(f"Fetching VyOSL3VPN resources from namespace={namespace}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="VyOSL3VPN")
        resources = api.get(namespace=namespace)

        vpns = []
        for item in resources.items:
            item_dict = item.to_dict()
            spec   = item_dict.get("spec",   {}) or {}
            status = item_dict.get("status", {}) or {}

            # Collect all router names (PE + CE)
            router_names = [r.get("name") for r in spec.get("routers", []) if r.get("name")]
            ce_names     = [r.get("name") for r in spec.get("ce_routers", []) if r.get("name")]
            all_routers  = router_names + ce_names

            # Collect per-router VRF RD/RT info for the dashboard table
            router_vrfs = []
            for pe_router in spec.get("routers", []):
                rname = pe_router.get("name", "")
                for vrf in pe_router.get("vrfs", []):
                    router_vrfs.append({
                        "router":     rname,
                        "vrf":        vrf.get("name", ""),
                        "rd":         vrf.get("rd"),
                        "rt_export":  vrf.get("rt_export", []),
                        "rt_import":  vrf.get("rt_import", []),
                        "description": vrf.get("description"),
                    })

            # Collect service-level metadata (topology, service RD/RT)
            services = []
            for svc in spec.get("services", []):
                services.append({
                    "name":        svc.get("name", ""),
                    "type":        svc.get("type"),
                    "topology":    svc.get("topology"),
                    "rd":          svc.get("rd"),
                    "rt_export":   svc.get("rt_export"),
                    "rt_import":   svc.get("rt_import"),
                    "description": svc.get("description"),
                })

            vpns.append({
                "name":        item_dict["metadata"]["name"],
                "phase":       status.get("phase",   "Unknown"),
                "message":     status.get("message", ""),
                "routers":     all_routers,
                "underlay_ref": spec.get("underlayRef"),
                "router_vrfs": router_vrfs,
                "services":    services,
            })

        logger.debug(f"Retrieved {len(vpns)} VyOSL3VPN resources")
        return vpns

    except Exception as e:
        logger.error(f"Error fetching VyOSL3VPN resources: {e}", exc_info=True)
        return []


def fetch_traffic_tests(namespace: str = "network") -> list:
    """
    Fetch all TrafficTest custom resources from Kubernetes and return a
    normalised summary list suitable for the dashboard.

    Returns:
        list of dicts with keys:
            name, phase, message, vpn_ref, source_devices, destination_device,
            duration, source_count, start_time, end_time, allocated_ports
    """
    logger.debug(f"Fetching TrafficTest resources from namespace={namespace}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="TrafficTest")
        resources = api.get(namespace=namespace)

        tests = []
        for item in resources.items:
            item_dict = item.to_dict()
            spec   = item_dict.get("spec",   {}) or {}
            status = item_dict.get("status", {}) or {}

            tests.append({
                "name":               item_dict["metadata"]["name"],
                "phase":              status.get("phase",   "Unknown"),
                "message":            status.get("message", ""),
                "vpn_ref":            spec.get("vpnRef"),
                "source_devices":     spec.get("source_devices", []),
                "destination_device": spec.get("destination_device"),
                "protocol":           spec.get("protocol"),
                "bandwidth":          spec.get("bandwidth"),
                "pattern_type":       spec.get("pattern_type"),
                "duration":           spec.get("duration", 60),
                "bidirectional":      bool(spec.get("bidirectional", False)),
                "source_count":       status.get("source_count", len(spec.get("source_devices", []))),
                "start_time":         status.get("start_time"),
                "end_time":           status.get("end_time"),
                "allocated_ports":    status.get("allocated_ports", []),
            })

        logger.debug(f"Retrieved {len(tests)} TrafficTest resources")
        return tests

    except Exception as e:
        logger.error(f"Error fetching TrafficTest resources: {e}", exc_info=True)
        return []


def delete_traffic_test_crd(name: str, namespace: str = "network") -> bool:
    """
    Delete a single TrafficTest CRD from Kubernetes by name.

    Args:
        name:      The TrafficTest resource name to delete.
        namespace: Kubernetes namespace (default: "network").

    Returns:
        True  – delete issued successfully (or resource already gone).
        False – kubernetes not available or unexpected error.
    """
    logger.debug(f"Deleting TrafficTest CRD: namespace={namespace}, name={name}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="TrafficTest")
        api.delete(name=name, namespace=namespace)
        logger.debug(f"Issued delete for TrafficTest/{name}")
        return True
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug(f"TrafficTest/{name} already gone")
            return True
        logger.error(f"Error deleting TrafficTest/{name}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Error deleting TrafficTest/{name}: {e}", exc_info=True)
        return False


def delete_vpn_crd(name: str, namespace: str = "network") -> bool:
    """
    Delete a single VyOSL3VPN CRD from Kubernetes by name.

    Args:
        name:      The VyOSL3VPN resource name to delete.
        namespace: Kubernetes namespace (default: "network").

    Returns:
        True  – delete issued successfully (or resource already gone).
        False – kubernetes not available or unexpected error.
    """
    logger.debug(f"Deleting VyOSL3VPN CRD: namespace={namespace}, name={name}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="VyOSL3VPN")
        api.delete(name=name, namespace=namespace)
        logger.debug(f"Issued delete for VyOSL3VPN/{name}")
        return True
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug(f"VyOSL3VPN/{name} already gone")
            return True
        logger.error(f"Error deleting VyOSL3VPN/{name}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Error deleting VyOSL3VPN/{name}: {e}", exc_info=True)
        return False


def fetch_vyos_underlay(namespace: str = "network") -> list:
    """
    Fetch all VyOSUnderlay custom resources from Kubernetes and return a
    normalised summary list suitable for the dashboard status card.

    Returns:
        list of dicts with keys:
            name, phase, message, infrastructure_ref
    """
    logger.debug(f"Fetching VyOSUnderlay resources from namespace={namespace}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="VyOSUnderlay")
        resources = api.get(namespace=namespace)

        underlay_list = []
        for item in resources.items:
            item_dict = item.to_dict()
            spec   = item_dict.get("spec",   {}) or {}
            status = item_dict.get("status", {}) or {}

            underlay_list.append({
                "name":               item_dict["metadata"]["name"],
                "phase":              status.get("phase",   "Unknown"),
                "message":            status.get("message", ""),
                "infrastructure_ref": spec.get("infrastructureRef"),
            })

        logger.debug(f"Retrieved {len(underlay_list)} VyOSUnderlay resources")
        return underlay_list

    except Exception as e:
        logger.error(f"Error fetching VyOSUnderlay resources: {e}", exc_info=True)
        return []


def fetch_vyos_infrastructure(namespace: str = "network") -> list:
    """
    Fetch all VyosInfrastructure custom resources from Kubernetes and return a
    normalised summary list suitable for the dashboard status card.

    Each infra item includes an 'underlays' list containing the status of any
    VyOSUnderlay CRs that reference this infrastructure via spec.infrastructureRef.

    Returns:
        list of dicts with keys:
            name, phase, message, router_count, network_count, device_count, underlays
    """
    logger.debug(f"Fetching VyosInfrastructure resources from namespace={namespace}")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        api = client.resources.get(api_version="google.dev/v1", kind="VyOSInfrastructure")
        resources = api.get(namespace=namespace)

        # Fetch all underlays once and group them by their infrastructureRef
        all_underlays = fetch_vyos_underlay(namespace=namespace)
        underlays_by_infra: dict = {}
        for u in all_underlays:
            ref = u.get("infrastructure_ref")
            if ref:
                underlays_by_infra.setdefault(ref, []).append({
                    "name":    u["name"],
                    "phase":   u["phase"],
                    "message": u["message"],
                })

        infra_list = []
        for item in resources.items:
            item_dict = item.to_dict()
            status = item_dict.get("status", {}) or {}

            routers  = status.get("routers",  []) or []
            networks = status.get("networks", []) or []
            devices  = status.get("devices",  []) or []

            infra_name = item_dict["metadata"]["name"]
            infra_list.append({
                "name":          infra_name,
                "phase":         status.get("phase",   "Unknown"),
                "message":       status.get("message", ""),
                "router_count":  len(routers),
                "network_count": len(networks),
                "device_count":  len(devices),
                "underlays":     underlays_by_infra.get(infra_name, []),
            })

        logger.debug(f"Retrieved {len(infra_list)} VyosInfrastructure resources")
        return infra_list

    except Exception as e:
        logger.error(f"Error fetching VyosInfrastructure resources: {e}", exc_info=True)
        return []


def fetch_infrastructure_state(namespace: str = "network") -> dict:
    """Return VPNs, TrafficTests, and VyosInfrastructure in a single call.

    Replaces three sequential REST calls from the dashboard VPN refresh timer
    (GET /vpns + GET /traffictests + GET /infrastructure) with one round-trip.

    Returns:
        dict with keys:
            vpns           — list of VPN summaries (from fetch_vpns)
            traffic_tests  — list of TrafficTest summaries (from fetch_traffic_tests)
            infrastructure — list of VyosInfrastructure summaries (from fetch_vyos_infrastructure)
    """
    logger.debug(f"Fetching combined infrastructure state for namespace={namespace}")
    return {
        "vpns":           fetch_vpns(namespace=namespace),
        "traffic_tests":  fetch_traffic_tests(namespace=namespace),
        "infrastructure": fetch_vyos_infrastructure(namespace=namespace),
    }
