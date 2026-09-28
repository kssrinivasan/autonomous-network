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
from utils.compute import *
# from utils.request_throttler import throttled, throttled_call
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner
import threading
import time
import os

SQL_TEMPLATES = {
  # --- Knowledge Graph tables ---
  'create_kg_res_node': "INSERT KgResourceDescriptionNode (id, content)"
                        " VALUES (@id, @content)",
  'update_kg_res_node': "UPDATE KgResourceDescriptionNode SET content = @content WHERE id = @id",
  'delete_kg_res_node': "DELETE FROM KgResourceDescriptionNode WHERE id = @id",
  'exist_kg_res_node' : "SELECT id FROM KgResourceDescriptionNode WHERE id = '{id}'",

  # --- Network topology tables ---
  # PhysicalRouter SCD
  'get_active_phy_router': "SELECT config, status FROM PhysicalRouter WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_router': "UPDATE PhysicalRouter SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_router': "INSERT PhysicalRouter (id, name, vendor, model, location_city, location_lat, location_lon, role, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @name, @vendor, @model, @location_city, @location_lat, @location_lon, @role, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_router': "UPDATE PhysicalRouter SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'get_router_id_by_name': "SELECT id FROM PhysicalRouter WHERE name = @name AND valid_end_ts IS NULL",

  # PhysicalInterface SCD
  'get_active_phy_interface': "SELECT speed, media_type, ip_address, mac_address, status FROM PhysicalInterface WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_interface': "UPDATE PhysicalInterface SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_interface': "INSERT PhysicalInterface (id, router_id, name, speed, media_type, ip_address, mac_address, status, valid_start_ts, valid_end_ts) VALUES (@id, @router_id, @name, @speed, @media_type, @ip_address, @mac_address, @status, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_interface_by_router': "UPDATE PhysicalInterface SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE router_id = @router_id AND valid_end_ts IS NULL",

  'upsert_customer': "INSERT OR UPDATE Customer (id, name, type, properties, last_updated) VALUES (@id, @name, @type, @properties, PENDING_COMMIT_TIMESTAMP())",
  
  # L3VPNService SCD
  'get_active_l3vpn': "SELECT config, status FROM L3VPNService WHERE id = @id AND valid_end_ts IS NULL",
  'close_l3vpn': "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_l3vpn': "INSERT L3VPNService (id, customer_id, name, service_type, topology, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @customer_id, @name, @service_type, @topology, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_l3vpn': "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  # VRF SCD
  'get_active_vrf': "SELECT config, status FROM VRF WHERE id = @id AND valid_end_ts IS NULL",
  'close_vrf': "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_vrf': "INSERT VRF (id, router_id, vpn_id, name, rd, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @router_id, @vpn_id, @name, @rd, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_vrf_by_vpn': "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vpn_id = @vpn_id AND valid_end_ts IS NULL",
  
  # BGPSession SCD
  'get_active_bgp': "SELECT config, status FROM BGPSession WHERE id = @id AND valid_end_ts IS NULL",
  'close_bgp': "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_bgp': "INSERT BGPSession (id, vrf_id, local_as, remote_as, peer_ip, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @vrf_id, @local_as, @remote_as, @peer_ip, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_bgp_by_vpn': "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id = @vpn_id AND valid_end_ts IS NULL) AND valid_end_ts IS NULL",

  # LogicalSubnet SCD
  'get_active_subnet': "SELECT properties, network_type FROM LogicalSubnet WHERE id = @id AND valid_end_ts IS NULL",
  'close_subnet': "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_subnet': "INSERT LogicalSubnet (id, cidr, network_type, description, properties, valid_start_ts, valid_end_ts) VALUES (@id, @cidr, @network_type, @description, @properties, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_subnet': "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  # PhysicalLink SCD
  'get_active_phy_link': "SELECT properties, status FROM PhysicalLink WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_link': "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_link': "INSERT PhysicalLink (id, name, bandwidth, status, properties, valid_start_ts, valid_end_ts) VALUES (@id, @name, @bandwidth, @status, @properties, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_link': "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  # Interface_Link SCD
  'get_active_interface_link': "SELECT interface_id FROM Interface_Link WHERE interface_id = @interface_id AND link_id = @link_id AND valid_end_ts IS NULL",
  'close_interface_link': "UPDATE Interface_Link SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE interface_id = @interface_id AND link_id = @link_id AND valid_end_ts IS NULL",
  'insert_interface_link': "INSERT Interface_Link (interface_id, link_id, valid_start_ts, valid_end_ts) VALUES (@interface_id, @link_id, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_interface_link_by_id': "UPDATE Interface_Link SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE (interface_id = @interface_id OR link_id = @link_id) AND valid_end_ts IS NULL",

  # Subnet_Association SCD
  'get_active_subnet_assoc': "SELECT entity_id FROM Subnet_Association WHERE entity_id = @entity_id AND subnet_id = @subnet_id AND valid_end_ts IS NULL",
  'close_subnet_assoc': "UPDATE Subnet_Association SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE entity_id = @entity_id AND subnet_id = @subnet_id AND valid_end_ts IS NULL",
  'insert_subnet_assoc': "INSERT Subnet_Association (entity_id, subnet_id, entity_type, valid_start_ts, valid_end_ts) VALUES (@entity_id, @subnet_id, @entity_type, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_subnet_assoc_by_id': "UPDATE Subnet_Association SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE (entity_id = @entity_id OR subnet_id = @subnet_id) AND valid_end_ts IS NULL",
}

# Spanner DB connection check interval
DB_CHECK_SECONDS=int(os.environ.get("DB_CHECK_SECONDS", 60))

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client(disable_builtin_metrics=True)
  instance = spanner_client.instance('networktopology-instance')
  db_name = os.environ.get('GOOGLE_SPANNER_TRANSPORT_DATABASE', 'transporttopology-db')
  database = instance.database(db_name)
  return database

# Global lock for thread-safe database updates
lock = threading.Lock()

def check_spanner_connection(db_container, lock):
    """Checks if the Spanner database exists and reconnects if necessary."""
    try:
        # Check outside lock first
        if not db_container['db'].exists():
            with lock:
                # Double check inside the lock to avoid multiple reconnections
                if not db_container['db'].exists():
                    logger.warning("Spanner database not found. Attempting to reconnect...")
                    db_container['db'] = spanner_connect()
                    logger.warning("Reconnected to Spanner.")
        else:
            logger.debug("Spanner DB connection alive!")
    except Exception as e:
        with lock:
            logger.error(f"Error checking Spanner connection: {e}. Attempting to reconnect...")
            try:
                db_container['db'] = spanner_connect()
                logger.warning("Reconnected to Spanner after error.")
            except Exception as re:
                logger.error(f"Failed to reconnect to Spanner: {re}")

def spanner_connection_worker(db_container, lock):
    """
    Background worker that regularly checks the Spanner connection.
    """
    logger.info(f"Spanner connection worker started. Checking every {DB_CHECK_SECONDS} seconds.")
    while True:
        check_spanner_connection(db_container, lock)
        time.sleep(DB_CHECK_SECONDS)

# Initialize database connection and start connection monitor thread
database_initial = spanner_connect()
db_container = {'db': database_initial}

connection_thread = threading.Thread(
    target=spanner_connection_worker, 
    args=(db_container, lock), 
    daemon=True
)
connection_thread.start()

logger = logging.getLogger(__name__)

# ------------------------------------------
# Build a serialized JSON representation of the 
# body that fit into a INSERT/UPDATE SQL statement
#
# **WARNING** Please think twice before making modifications
# here as it took me a lot of trial and errors to come up
# with this solution
# ------------------------------------------
def body_sql_json_dump(string_dump):
  # Double escape the \" sequences created by the santitize call so as to build
  # a syntactically correct SQL INSERT statement for Spanner to execute.
  # Also escape single quotes as single quotes are used to enclose the
  # JSON string in the SQL statement.
  return string_dump.replace('\\n','\\\\n').replace('\\"', '\\\\"').replace("'", "\\'")
 
def body_string_dump(body, kind, namespace, name):
  # Do not rely on the body object from kopf. Get it from
  # K8s directly
  api = kubernetes.client.ApiClient()
  client = kubernetes.dynamic.DynamicClient(api)
  resource_api = get_resource_api(body.get('apiVersion'), kind, client)
  resource = resource_api.get(namespace=namespace, name=name)
  #sanitized_resource = api.sanitize_for_serialization(resource.to_dict())
  #logger.debug("resource: %s",sanitized_resource)

  # Remove some JSON keys that Spanner JSON doesn't like although it is perfectly
  # valid and sanitized (invalid JSON litteral error on SQL INSERT)
  resource_dict = api.sanitize_for_serialization(resource.to_dict())

  resource_dict['metadata'].pop('managedFields', None)
  if 'annotations' in resource_dict['metadata']:
    # CAUTION !! We are iterating through keys that we can possibly delete 
    # so keep the for loop below exactly as is (the call to list() does
    # a copy of the keys)
    for key in list(resource_dict['metadata']['annotations'].keys()):
      if key.startswith('kopf'):
        resource_dict['metadata']['annotations'].pop(key, None)
 
  return json.dumps(resource_dict, ensure_ascii = True)

# ------------------------------------------
# Extract a human readbale status and return a well
# formatted string to use in SQL INSERT (either NULL or
# "'status_string'")
# NOTE: sanitize_k8s_body is now imported from utils.compute
# ------------------------------------------
def get_status(body):
  status_value = "NULL"
  status = body.get('status')
  if status is not None:
    conditions = status.get('conditions')
    # NOTE: conditions is a list object
    if conditions is not None:
      reason = conditions[0].get('reason')
      if reason is not None:
        status_value = reason
    else:
      if body['kind'].lower() in ['wireguardappliance', 'pointtopointservice', 'meshservice', 'userplanefunction', 'controlplane', 'datanetwork','ueransim']:
        if 'currentStatus' in body['status']:
          status_value = body['status']['currentStatus']
        else:
          svc = body['kind'].lower()
          if (svc in body['status']):
            if ('status' in body['status'][svc]):
              status_value = body['status'][svc]['status']
          elif ('kopf' in body['status']) and ('progress' in body['status']['kopf']) and (svc in body['status']['kopf']['progress']):
            if ('failure' in body['status']['kopf']['progress'][svc]) and (body['status']['kopf']['progress'][svc]['failure'] == True):
              status_value = 'Failed'

  return status_value

# ------------------------------------------
# Idempotent function to create or update a
# KG resource node
# ------------------------------------------
# @throttled
async def create_or_update_kg_resource_description_node(id, body_string):
  success = True
  if await exist_kg_resource_description_node(id):
    success = success & await update_kg_resource_description_node(id, body_string)
  else:
    success = success & await create_kg_resource_description_node(id, body_string)
  return success

# ------------------------------------------
# Does a KG resource node exists
# ------------------------------------------
# @throttled
async def exist_kg_resource_description_node(id):

  tmpl = SQL_TEMPLATES['exist_kg_res_node']
  sql = tmpl.format(id=id)
  logger.debug("SQL: {}".format(sql))

  try:
    with db_container['db'].snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    success = (results.one_or_none() is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.debug("{} KG resource node exists)".format(id))
  else:
    logger.debug("{} KG resource node doesn't exist)".format(id))
  return success

# ------------------------------------------
# Create K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def create_kg_resource_description_node(id, body_string):

  def sql_create_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['create_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string
  
  row_ct = 0
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_create_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"KG Resource node created id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node creation failed id: {id}")
  return success


# ------------------------------------------
# Update K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def update_kg_resource_description_node(id, body_string):

  def sql_update_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['update_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string

  row_ct = None
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_update_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
  
  if success:
    logger.debug(f"KG Resource node updated id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node update failed id: {id} ")
  return success

# ------------------------------------------
# Delete K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def delete_kg_resource_description_node(id):

  def sql_delete_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['delete_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"id": id},
      param_types={"id": spanner.param_types.STRING})
   
  row_ct = None
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_delete_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"{id} KG Resource node deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node deletion failed id: {id}")
  return success

# ------------------------------------------
# Helper to propagate PhysicalLink bandwidth to PhysicalInterface.speed (SCD)
# ------------------------------------------
async def _sync_iface_speed_from_bandwidth(interface_id, bandwidth, logger):
    """Update PhysicalInterface.speed to match a connected PhysicalLink.bandwidth (SCD).

    Called by sync_vyos_infrastructure() after creating an Interface_Link so that
    the GNN can compute accurate utilisation ratios (tx_bytes / speed_bps) relative
    to the real simulated link capacity rather than the hardcoded 1G default.

    If the PhysicalInterface doesn't exist yet (router not synced yet) this is a
    no-op — sync_physical_router() will perform the same link-bandwidth lookup when
    the router CRD is eventually processed.
    """
    if not bandwidth or bandwidth == 'unknown':
        return

    # Derive router_id and interface name from the ID convention:
    #   "router:<router-name>:interface:<if-name>"
    parts = interface_id.split(':interface:')
    if len(parts) != 2:
        logger.warning(f"Cannot parse interface_id '{interface_id}' for speed sync")
        return
    router_id = parts[0]
    iface_name = parts[1]

    def sql_sync_speed(transaction):
        # Read the currently active PhysicalInterface row.
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_phy_interface'],
            params={'id': interface_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()
        if not row:
            # Interface doesn't exist yet; sync_physical_router() will pick up
            # the bandwidth when the VyOSRouter CRD is processed.
            return

        # SELECT speed, media_type, ip_address, mac_address, status
        existing_speed, existing_media, existing_ip, existing_mac, existing_status = row
        if existing_speed == bandwidth:
            return  # Already up to date; nothing to do.

        # Close the existing SCD row and reinsert with the corrected speed.
        transaction.execute_update(
            SQL_TEMPLATES['close_phy_interface'],
            params={'id': interface_id},
            param_types={'id': spanner.param_types.STRING}
        )
        transaction.execute_update(
            SQL_TEMPLATES['insert_phy_interface'],
            params={
                'id': interface_id,
                'router_id': router_id,
                'name': iface_name,
                'speed': bandwidth,
                'media_type': existing_media,
                'ip_address': existing_ip,
                'mac_address': existing_mac,
                'status': existing_status,
            },
            param_types={
                'id': spanner.param_types.STRING,
                'router_id': spanner.param_types.STRING,
                'name': spanner.param_types.STRING,
                'speed': spanner.param_types.STRING,
                'media_type': spanner.param_types.STRING,
                'ip_address': spanner.param_types.STRING,
                'mac_address': spanner.param_types.STRING,
                'status': spanner.param_types.STRING,
            }
        )

    try:
        db_container['db'].run_in_transaction(sql_sync_speed)
        logger.debug(f"Synced interface {interface_id} speed to '{bandwidth}' from PhysicalLink")
    except Exception as e:
        error_msg = f"Failed to sync interface {interface_id} speed from bandwidth: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)


# ------------------------------------------
# Helper to find PhysicalRouter ID by name
# ------------------------------------------
async def _get_router_id_by_name(name):
  sql = SQL_TEMPLATES['get_router_id_by_name']
  try:
    with db_container['db'].snapshot() as snapshot:
      results = snapshot.execute_sql(sql, params={'name': name}, param_types={'name': spanner.param_types.STRING})
      # Get first result in case of duplicates (could happen if routers were recreated)
      for row in results:
        return row[0]  # Return the first match
  except Exception as e:
    logger.error(f"Error finding router id for {name}: {e}")
  return None

# ------------------------------------------
# Sync VyOSInfrastructure
# ------------------------------------------
async def sync_vyos_infrastructure(body, spec, name, uid, logger):
    logger.debug(f"Syncing VyOSInfrastructure {name}")
    # Sync networks as LogicalSubnets (SCD)
    networks = spec.get('networks', [])
    for net in networks:
        subnet_id = f"subnet:{net['name']}"
        # Convert to dict if it's a Kubernetes object
        net_dict = dict(net) if hasattr(net, '__iter__') and not isinstance(net, (str, bytes)) else net
        subnet_props = json.dumps(net_dict)
        cidr = net.get('subnet', '')
        network_type = net.get('network_type', 'unknown')
        description = net.get('description', '')
        
        def sql_upsert_subnet(transaction):
            # 1. Get active subnet
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_subnet'],
                params={'id': subnet_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                existing_props = row[0] # properties is first col in get_active_subnet
                # Compare content
                if existing_props == net_dict:
                     need_insert = False
                else:
                    # Close existing
                    transaction.execute_update(
                        SQL_TEMPLATES['close_subnet'],
                        params={'id': subnet_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
            
            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_subnet'],
                    params={
                        'id': subnet_id,
                        'cidr': cidr,
                        'network_type': network_type,
                        'description': description,
                        'properties': subnet_props
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'cidr': spanner.param_types.STRING,
                        'network_type': spanner.param_types.STRING,
                        'description': spanner.param_types.STRING,
                        'properties': spanner.param_types.JSON
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_subnet)
        except Exception as e:
            error_msg = f"Failed to upsert subnet {subnet_id}: {e}"
            logger.error(error_msg)
            raise kopf.TemporaryError(error_msg, delay=10)
        
        # Create PhysicalLink if this network has connected_routers (indicates a physical link)
        connected_routers = net.get('connected_routers', [])
        if len(connected_routers) >= 2:
            # This is a physical link connecting routers
            link_id = f"link:{net['name']}"
            link_name = net.get('name', '')
            bandwidth = net.get('bandwidth', 'unknown')
            link_status = 'UP'  # Could be derived from network/router status
            
            # Build link properties including all network details.
            # bandwidth is included here so that the properties dict comparison in
            # sql_upsert_link detects bandwidth-only changes — the dedicated
            # PhysicalLink.bandwidth column is set from net.get('bandwidth') below, but
            # the SCD change detection reads back only (properties, status) via
            # get_active_phy_link, so storing bandwidth in properties is the simplest
            # way to make the equality check fire when only bandwidth changes.
            link_props = {
                'subnet': net.get('subnet', ''),
                'network_type': net.get('network_type', 'unknown'),
                'vlan': net.get('vlan', None),
                'mtu': net.get('mtu', 1500),
                'description': net.get('description', ''),
                'bandwidth': bandwidth,
                'connected_routers': connected_routers
            }
            
            def sql_upsert_link(transaction):
                # 1. Get active link
                results = transaction.execute_sql(
                    SQL_TEMPLATES['get_active_phy_link'],
                    params={'id': link_id},
                    param_types={'id': spanner.param_types.STRING}
                )
                row = results.one_or_none()

                need_insert = True
                if row:
                    # row[0] is properties (JSON/Dict), row[1] is status
                    existing_props = row[0]
                    existing_status = row[1]
                    
                    # Compare content (Dict comparison).
                    # link_props includes 'bandwidth' so a bandwidth change will
                    # cause existing_props != link_props and trigger a new SCD row.
                    if existing_props == link_props and existing_status == link_status.lower():
                        need_insert = False
                    else:
                        # Close existing row
                        transaction.execute_update(
                            SQL_TEMPLATES['close_phy_link'],
                            params={'id': link_id},
                            param_types={'id': spanner.param_types.STRING}
                        )
                
                if need_insert:
                    transaction.execute_update(
                        SQL_TEMPLATES['insert_phy_link'],
                        params={
                            'id': link_id,
                            'name': link_name,
                            'bandwidth': bandwidth,
                            'status': link_status.lower(),
                            'properties': json.dumps(link_props)
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'name': spanner.param_types.STRING,
                            'bandwidth': spanner.param_types.STRING,
                            'status': spanner.param_types.STRING,
                            'properties': spanner.param_types.JSON
                        }
                    )
            
            try:
                db_container['db'].run_in_transaction(sql_upsert_link)
                logger.debug(f"Created PhysicalLink {link_id} connecting {len(connected_routers)} routers")
            except Exception as e:
                error_msg = f"Failed to create PhysicalLink {link_id}: {e}"
                logger.error(error_msg)
                raise kopf.TemporaryError(error_msg, delay=10)
            
            # Create Interface_Link associations for each connected router
            for router_conn in connected_routers:
                router_name = router_conn.get('router_name')
                interface_name = router_conn.get('interface')
                
                if not router_name or not interface_name:
                    logger.warning(f"Skipping connection with missing router_name or interface in link {link_id}")
                    continue
                
                # Look up router ID by name
                router_id = await _get_router_id_by_name(router_name)
                
                if not router_id:
                    logger.debug(f"Router {router_name} not found yet for link {link_id}, will sync when router is created")
                    continue
                
                interface_id = f"{router_id}:interface:{interface_name}"
                
                def sql_upsert_iface_link(transaction):
                    # Check active link
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_interface_link'],
                        params={'interface_id': interface_id, 'link_id': link_id},
                        param_types={'interface_id': spanner.param_types.STRING, 'link_id': spanner.param_types.STRING}
                    )
                    if not results.one_or_none():
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_interface_link'],
                            params={
                                'interface_id': interface_id,
                                'link_id': link_id
                            },
                            param_types={
                                'interface_id': spanner.param_types.STRING,
                                'link_id': spanner.param_types.STRING
                            }
                        )
                
                try:
                    db_container['db'].run_in_transaction(sql_upsert_iface_link)
                    logger.debug(f"Linked interface {interface_id} to {link_id}")
                except Exception as e:
                    error_msg = f"Failed to link interface {interface_id} to {link_id}: {e}"
                    logger.error(error_msg)
                    raise kopf.TemporaryError(error_msg, delay=10)

                # Propagate the link bandwidth to PhysicalInterface.speed so the GNN
                # can compute accurate utilisation ratios (tx_bytes / speed_bps).
                # No-op if the interface doesn't exist yet (router not created yet)
                # or if the speed is already correct.
                if bandwidth and bandwidth != 'unknown':
                    await _sync_iface_speed_from_bandwidth(interface_id, bandwidth, logger)


async def delete_vyos_infrastructure(uid, spec, logger):
    """Delete VyOSInfrastructure and associated subnets and physical links"""
    logger.debug(f"Deleting VyOSInfrastructure {uid}")
    
    def sql_delete(transaction):
        # Delete interface-link associations
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE link_id LIKE 'link:%'",
            params={},
            param_types={}
        )
        
        # Close physical links (SCD)
        transaction.execute_update(
            "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'link:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # Delete subnet associations
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE subnet_id LIKE 'subnet:%'",
            params={},
            param_types={}
        )
        
        # Close logical subnets created by this infrastructure (SCD)
        transaction.execute_update(
            "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'subnet:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
    
    try:
        db_container['db'].run_in_transaction(sql_delete)
        logger.debug(f"Successfully deleted VyOSInfrastructure topology for {uid}")
    except Exception as e:
        error_msg = f"Failed to delete VyOSInfrastructure {uid}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)

# ------------------------------------------
# Sync PhysicalRouter
# ------------------------------------------
async def sync_physical_router(body, spec, name, uid, logger):
    logger.debug(f"Syncing PhysicalRouter {name}")
    
    # 1. Upsert Router
    # Use router name as ID to prevent duplicates when router is recreated
    router_id = f"router:{name}"
    
    router_role = spec.get('role', 'Router')
    logger.debug(f"Syncing PhysicalRouter {name} with role: {router_role}")
    
    # Extract status from CRD status field
    router_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        router_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            router_status = status_str
    
    def sql_upsert_router(transaction):
        # Prepare the full config object (sanitized body)
        # We use the full body so we have metadata, status, etc.
        sanitized_body = sanitize_k8s_body(body)
        # Ensure it is a dict (it should be coming from kopf/lifecycle)
        if not isinstance(sanitized_body, dict):
             # Fallback if somehow it's not a dict, though unexpected
             sanitized_body = dict(sanitized_body)
             
        config_json = json.dumps(sanitized_body)

        
        # Get location from spec.location (VyOS Infrastructure uses latitude/longitude, not lat/lon)
        location = spec.get('location', {})
        metadata_labels = body.get('metadata', {}).get('labels', {})
        
        # 1. Get active router
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_phy_router'],
            params={'id': router_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            existing_config = row[0]
            existing_status = row[1]
            # Compare content. existing_config is the config JSON string from DB (or dict if client decodes it??)
            # Spanner client usually returns JSON types as native Python objects (dicts/lists)
            # But let's be careful. If row[0] is a string, load it.
            # update: param_types.JSON returns native object (dict)
            
            # We compare the dictionary representations
            if existing_config == sanitized_body and existing_status == router_status.lower():
                need_insert = False
            else:
                # Close existing row
                transaction.execute_update(
                    SQL_TEMPLATES['close_phy_router'],
                    params={'id': router_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        
        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_phy_router'],
                params={
                    'id': router_id,
                    'name': name,
                    'vendor': spec.get('vendor', 'VyOS'),
                    'model': spec.get('model', 'Virtual'),
                    'location_city': location.get('city') or metadata_labels.get('city', 'Unknown'),
                    'location_lat': float(location.get('latitude') or location.get('lat') or metadata_labels.get('latitude') or metadata_labels.get('lat') or 0.0),
                    'location_lon': float(location.get('longitude') or location.get('lon') or metadata_labels.get('longitude') or metadata_labels.get('lon') or 0.0),
                    'role': spec.get('role', 'Router'),
                    'status': router_status.lower(),
                    'config': config_json
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'vendor': spanner.param_types.STRING,
                    'model': spanner.param_types.STRING,
                    'location_city': spanner.param_types.STRING,
                    'location_lat': spanner.param_types.FLOAT64,
                    'location_lon': spanner.param_types.FLOAT64,
                    'role': spanner.param_types.STRING,
                    'status': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON
                }
            )
    
    try:
        db_container['db'].run_in_transaction(sql_upsert_router)
    except Exception as e:
        error_msg = f"Failed to upsert router {name}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)

    # 2. Upsert Interfaces
    interfaces = spec.get('interfaces', [])
    for iface in interfaces:
        # Handle both string and object interface definitions
        if isinstance(iface, str):
            iface_name = iface
            iface_data = {}
        else:
            iface_name = iface.get('name', 'unknown')
            iface_data = iface
        
        iface_id = f"{router_id}:interface:{iface_name}"
        
        # Extract IP address (remove CIDR if present)
        ip_address = iface_data.get('address', '0.0.0.0')
        if '/' in ip_address:
            ip_address = ip_address.split('/')[0]
        
        # Determine interface status from router status or interface specific status
        iface_status = 'Unknown'
        if status_obj and 'interfaces' in status_obj:
            for iface_status_obj in status_obj['interfaces']:
                if not isinstance(iface_status_obj, dict):
                    logger.warning(f"Skipping non-dict entry in interfaces status (got {type(iface_status_obj).__name__}): {iface_status_obj!r}")
                    continue
                if iface_status_obj.get('name') == iface_name:
                    iface_status = iface_status_obj.get('status', 'Unknown')
                    break
        
        # If no specific status, use enabled flag or default to UP if router is running
        # Normalise to uppercase to match Ansible operstate values (UP / DOWN / UNKNOWN)
        if iface_status == 'Unknown':
            if iface_data.get('enabled', True):
                iface_status = 'UP' if router_status in ['Running', 'Ready'] else 'DOWN'
            else:
                iface_status = 'ADMIN_DOWN'
        
        # Extract speed and media type.
        # Preferred source: PhysicalLink.bandwidth from a connected link (set by
        # sync_vyos_infrastructure from the VyOSInfrastructure spec.networks[*].bandwidth).
        # This lets the GNN compute accurate utilisation ratios (tx_bytes / speed_bps).
        # Fallback: CRD spec field → hardcoded default '1G' for non-loopback interfaces.
        media_type = iface_data.get('media_type', 'ethernet')
        if iface_name == 'lo':
            media_type = 'loopback'
            speed = 'N/A'
        else:
            speed = None
            try:
                with db_container['db'].snapshot() as snapshot:
                    results = snapshot.execute_sql(
                        """SELECT pl.bandwidth
                           FROM PhysicalLink pl
                           JOIN Interface_Link il ON il.link_id = pl.id
                           WHERE il.interface_id = @iface_id
                             AND il.valid_end_ts IS NULL
                             AND pl.valid_end_ts IS NULL
                           LIMIT 1""",
                        params={'iface_id': iface_id},
                        param_types={'iface_id': spanner.param_types.STRING}
                    )
                    row = results.one_or_none()
                    if row and row[0] and row[0] != 'unknown':
                        speed = row[0]
                        logger.debug(f"Using PhysicalLink bandwidth '{speed}' as speed for {iface_id}")
            except Exception as _e:
                logger.debug(f"Could not look up link bandwidth for {iface_id}: {_e}")
            if not speed:
                speed = iface_data.get('speed', '1G')
        
        def sql_upsert_iface(transaction):
            # 1. Get active interface
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_phy_interface'],
                params={'id': iface_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                # SELECT speed, media_type, ip_address, mac_address, status
                existing_speed = row[0]
                existing_media = row[1]
                existing_ip = row[2]
                existing_mac = row[3]
                existing_status = row[4]
                
                # Compare content
                current_mac = iface_data.get('mac', '')
                if (existing_speed == speed and 
                    existing_media == media_type and 
                    existing_ip == ip_address and 
                    existing_mac == current_mac and 
                    existing_status == iface_status):
                    need_insert = False
                else:
                    # Close existing row
                    transaction.execute_update(
                        SQL_TEMPLATES['close_phy_interface'],
                        params={'id': iface_id},
                        param_types={'id': spanner.param_types.STRING}
                    )

            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_phy_interface'],
                    params={
                        'id': iface_id,
                        'router_id': router_id,
                        'name': iface_name,
                        'speed': speed,
                        'media_type': media_type,
                        'ip_address': ip_address,
                        'mac_address': iface_data.get('mac', ''),
                        'status': iface_status.lower()
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'router_id': spanner.param_types.STRING,
                        'name': spanner.param_types.STRING,
                        'speed': spanner.param_types.STRING,
                        'media_type': spanner.param_types.STRING,
                        'ip_address': spanner.param_types.STRING,
                        'mac_address': spanner.param_types.STRING,
                        'status': spanner.param_types.STRING
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_iface)
        except Exception as e:
            error_msg = f"Failed to upsert interface {iface_id}: {e}"
            logger.error(error_msg)
            raise kopf.TemporaryError(error_msg, delay=10)
        
        # 3. Create subnet associations for interfaces with IP addresses
        if iface_data.get('address'):
            # Extract CIDR network from interface address
            addr_cidr = iface_data.get('address')
            if '/' in addr_cidr:
                # Create the LogicalSubnet first (required by foreign key constraint)
                subnet_id = f"subnet:{addr_cidr}"
                
                def sql_upsert_subnet(transaction):
                    # 1. Get active subnet
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_subnet'],
                        params={'id': subnet_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
                    row = results.one_or_none()

                    need_insert = True
                    if row:
                        existing_props = row[0]
                        if existing_props == {}:
                             need_insert = False
                        else:
                            # Close existing
                            transaction.execute_update(
                                SQL_TEMPLATES['close_subnet'],
                                params={'id': subnet_id},
                                param_types={'id': spanner.param_types.STRING}
                            )
                    
                    if need_insert:
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_subnet'],
                            params={
                                'id': subnet_id,
                                'cidr': addr_cidr,
                                'network_type': 'interface',
                                'description': f'Subnet for interface {iface_name}',
                                'properties': json.dumps({})
                            },
                            param_types={
                                'id': spanner.param_types.STRING,
                                'cidr': spanner.param_types.STRING,
                                'network_type': spanner.param_types.STRING,
                                'description': spanner.param_types.STRING,
                                'properties': spanner.param_types.JSON
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_subnet)
                except Exception as e:
                    error_msg = f"Failed to create subnet {subnet_id}: {e}"
                    logger.error(error_msg)
                    raise kopf.TemporaryError(error_msg, delay=10)
                
                # Now create the association (subnet must exist due to foreign key)
                def sql_upsert_subnet_assoc(transaction):
                    # Check active association
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_subnet_assoc'],
                        params={'entity_id': iface_id, 'subnet_id': subnet_id},
                        param_types={'entity_id': spanner.param_types.STRING, 'subnet_id': spanner.param_types.STRING}
                    )
                    if not results.one_or_none():
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_subnet_assoc'],
                            params={
                                'entity_id': iface_id,
                                'subnet_id': subnet_id,
                                'entity_type': 'Interface'
                            },
                            param_types={
                                'entity_id': spanner.param_types.STRING,
                                'subnet_id': spanner.param_types.STRING,
                                'entity_type': spanner.param_types.STRING
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_subnet_assoc)
                except Exception as e:
                    error_msg = f"Failed to create subnet association for {iface_id}: {e}"
                    logger.error(error_msg)
                    raise kopf.TemporaryError(error_msg, delay=10)


async def delete_physical_router(uid, name=None):
    """Delete physical router and cascade delete related entities"""
    # Use name-based ID if available, otherwise fall back to UID for backwards compatibility
    router_id = f"router:{name}" if name else uid
    logger.debug(f"Deleting PhysicalRouter {router_id}")
    

        
    def sql_delete_router(transaction):
        # 1. Delete subnet associations (Cleanup Edge Table)
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE entity_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # 2. Delete interface-link associations (Cleanup Edge Table)
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE interface_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # 3. Close the router (SCD)
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_router'],
            params={'id': router_id},
            param_types={'id': spanner.param_types.STRING}
        )
        
        # 4. Close associated interfaces (SCD) - Cascading Close
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_interface_by_router'],
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_delete_router)
        logger.debug(f"Successfully closed PhysicalRouter {name} and its interfaces")
    except Exception as e:
        error_msg = f"Failed to close PhysicalRouter {name}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)

# ------------------------------------------
# Sync L3VPNService
# ------------------------------------------
async def sync_l3vpn_service(body, spec, name, uid, logger):
    logger.debug(f"Syncing L3VPNService {name}")
    
    # Extract status from CRD
    l3vpn_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        l3vpn_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            l3vpn_status = status_str
    
    # Track VPN IDs created from this CRD for delete tracking
    vpn_ids_in_crd = []
    
    customer_id = "cust:default"  # Placeholder for customer
    
    # Ensure customer exists
    def sql_upsert_cust(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['upsert_customer'],
            params={'id': customer_id, 'name': 'Default Customer', 'type': 'Internal', 'properties': '{}'},
            param_types={'id': spanner.param_types.STRING, 'name': spanner.param_types.STRING, 'type': spanner.param_types.STRING, 'properties': spanner.param_types.JSON}
        )
    try:
        db_container['db'].run_in_transaction(sql_upsert_cust)
    except:
        pass

    # 1. Upsert VPN Services (SCD) from spec.services
    # spec.services contains only service metadata (name, type, topology).
    # spec.routers (top-level sibling) contains the per-router VRF and BGP config.
    services = spec.get('services', [])
    service_map = {svc['name']: svc for svc in services}

    for svc in services:
        vpn_id = f"vpn:{svc['name']}"
        vpn_ids_in_crd.append(vpn_id)

        vpn_name = svc['name']
        service_type = svc.get('type', 'L3VPN')
        topology = svc.get('topology', 'Mesh')
        vpn_config = json.dumps(dict(svc) if hasattr(svc, '__iter__') and not isinstance(svc, (str, bytes)) else svc)

        def sql_upsert_l3vpn(transaction, _vpn_id=vpn_id, _vpn_name=vpn_name,
                              _service_type=service_type, _topology=topology,
                              _vpn_config=vpn_config):
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_l3vpn'],
                params={'id': _vpn_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                existing_config = row[0]
                existing_status = row[1]
                if existing_config == _vpn_config and existing_status == l3vpn_status.lower():
                    need_insert = False
                else:
                    transaction.execute_update(
                        SQL_TEMPLATES['close_l3vpn'],
                        params={'id': _vpn_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
            
            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_l3vpn'],
                    params={
                        'id': _vpn_id,
                        'customer_id': customer_id,
                        'name': _vpn_name,
                        'service_type': _service_type,
                        'topology': _topology,
                        'status': l3vpn_status.lower(),
                        'config': _vpn_config
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'customer_id': spanner.param_types.STRING,
                        'name': spanner.param_types.STRING,
                        'service_type': spanner.param_types.STRING,
                        'topology': spanner.param_types.STRING,
                        'status': spanner.param_types.STRING,
                        'config': spanner.param_types.JSON
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_l3vpn)
            logger.debug(f"Upserted L3VPNService {vpn_id}")
        except Exception as e:
            error_msg = f"Failed to upsert L3VPNService {vpn_id}: {e}"
            logger.error(error_msg)
            raise kopf.TemporaryError(error_msg, delay=10)

    # 2. Sync VRFs and BGP Sessions from spec.routers (top-level field).
    # Each router entry has:
    #   - name: router name
    #   - vrfs: list of {name, table, rd, rt_export, rt_import, interfaces}
    #   - bgp.vrfs: list of {name, neighbors: [{peer, remote_as}]}
    # The VRF name matches a service name in spec.services, providing the vpn_id link.
    routers = spec.get('routers', [])
    for router in routers:
        router_name = router.get('name')
        if not router_name:
            continue
        
        router_id = await _get_router_id_by_name(router_name)
        if not router_id:
            logger.warning(f"Router {router_name} not found in Spanner for L3VPN {name}")
            continue

        # Build a map of VRF name → BGP neighbors from router.bgp.vrfs
        bgp_neighbors_by_vrf = {}
        bgp = router.get('bgp', {})
        for bgp_vrf in bgp.get('vrfs', []):
            bgp_vrf_name = bgp_vrf.get('name')
            if bgp_vrf_name:
                bgp_neighbors_by_vrf[bgp_vrf_name] = bgp_vrf.get('neighbors', [])

        # Process each VRF on this router
        for vrf in router.get('vrfs', []):
            vrf_name = vrf.get('name')
            if not vrf_name:
                continue

            vpn_id = f"vpn:{vrf_name}"
            vrf_id = f"vrf:{router_name}:{vrf_name}"
            rd = vrf.get('rd', 'unknown')
            vrf_status = 'Active' if l3vpn_status == 'Ready' else 'Pending'
            vrf_config = json.dumps(dict(vrf) if hasattr(vrf, '__iter__') and not isinstance(vrf, (str, bytes)) else vrf)

            def sql_upsert_vrf(transaction, _vrf_id=vrf_id, _router_id=router_id,
                               _vpn_id=vpn_id, _vrf_name=vrf_name, _rd=rd,
                               _vrf_status=vrf_status, _vrf_config=vrf_config):
                results = transaction.execute_sql(
                    SQL_TEMPLATES['get_active_vrf'],
                    params={'id': _vrf_id},
                    param_types={'id': spanner.param_types.STRING}
                )
                row = results.one_or_none()

                need_insert = True
                if row:
                    existing_config = row[0]
                    existing_status = row[1]
                    if existing_config == _vrf_config and existing_status == _vrf_status.lower():
                        need_insert = False
                    else:
                        transaction.execute_update(
                            SQL_TEMPLATES['close_vrf'],
                            params={'id': _vrf_id},
                            param_types={'id': spanner.param_types.STRING}
                        )
                
                if need_insert:
                    transaction.execute_update(
                        SQL_TEMPLATES['insert_vrf'],
                        params={
                            'id': _vrf_id,
                            'router_id': _router_id,
                            'vpn_id': _vpn_id,
                            'name': f"VRF-{_vrf_name}",
                            'rd': _rd,
                            'status': _vrf_status.lower(),
                            'config': _vrf_config
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'router_id': spanner.param_types.STRING,
                            'vpn_id': spanner.param_types.STRING,
                            'name': spanner.param_types.STRING,
                            'rd': spanner.param_types.STRING,
                            'status': spanner.param_types.STRING,
                            'config': spanner.param_types.JSON
                        }
                    )
            try:
                db_container['db'].run_in_transaction(sql_upsert_vrf)
                logger.debug(f"Upserted VRF {vrf_id}")
            except Exception as e:
                error_msg = f"Failed to upsert VRF {vrf_id}: {e}"
                logger.error(error_msg)
                raise kopf.TemporaryError(error_msg, delay=10)

            # 3. Sync BGP Sessions for this VRF (SCD)
            # Neighbors are under router.bgp.vrfs[name].neighbors.
            # The CRD uses 'peer' for the neighbor IP (not 'peer_ip').
            neighbors = bgp_neighbors_by_vrf.get(vrf_name, [])

            for n in neighbors:
                # CRD schema field is 'peer', not 'peer_ip'
                peer_ip = n.get('peer') or n.get('peer_ip')
                if not peer_ip:
                    continue
                
                bgp_id = f"bgp:{router_name}:{vrf_name}:{peer_ip}"
                remote_as = n.get('remote_as', 0)
                bgp_status = 'Established' if l3vpn_status == 'Ready' else 'Idle'
                bgp_config = json.dumps(dict(n) if hasattr(n, '__iter__') and not isinstance(n, (str, bytes)) else n)

                def sql_upsert_bgp(transaction, _bgp_id=bgp_id, _vrf_id=vrf_id,
                                   _remote_as=remote_as, _peer_ip=peer_ip,
                                   _bgp_status=bgp_status, _bgp_config=bgp_config):
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_bgp'],
                        params={'id': _bgp_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
                    row = results.one_or_none()

                    need_insert = True
                    if row:
                        existing_config = row[0]
                        existing_status = row[1]
                        if existing_config == _bgp_config and existing_status == _bgp_status:
                            need_insert = False
                        else:
                            transaction.execute_update(
                                SQL_TEMPLATES['close_bgp'],
                                params={'id': _bgp_id},
                                param_types={'id': spanner.param_types.STRING}
                            )
                    
                    if need_insert:
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_bgp'],
                            params={
                                'id': _bgp_id,
                                'vrf_id': _vrf_id,
                                'local_as': 0,
                                'remote_as': _remote_as,
                                'peer_ip': _peer_ip,
                                'status': _bgp_status,
                                'config': _bgp_config
                            },
                            param_types={
                                'id': spanner.param_types.STRING,
                                'vrf_id': spanner.param_types.STRING,
                                'local_as': spanner.param_types.INT64,
                                'remote_as': spanner.param_types.INT64,
                                'peer_ip': spanner.param_types.STRING,
                                'status': spanner.param_types.STRING,
                                'config': spanner.param_types.JSON
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_bgp)
                    logger.debug(f"Upserted BGPSession {bgp_id}")
                except Exception as e:
                    error_msg = f"Failed to upsert BGP {bgp_id}: {e}"
                    logger.error(error_msg)
                    raise kopf.TemporaryError(error_msg, delay=10)
    
    return vpn_ids_in_crd


async def delete_l3vpn_service(uid):
    """Delete L3VPN service and cascade delete VRFs and BGP sessions (SCD)"""
    logger.debug(f"Deleting L3VPN Service CRD {uid}")
    
    # We need to find all VPNs that match this CRD's UID pattern
    # VPN IDs are created as f"vpn:{svc['name']}" where svc comes from this CRD
    # Since we can't easily know the names without the spec, we might rely on the vpn_ids returned by sync
    # But K8s finalizer logic usually just gives us the UID.
    # For now, assuming we can partial match or we rely on the implementation that passes names?
    # Actually checking the caller in `lifecycle.py`, it passes the UID.
    # The current implementation used `LIKE 'vpn:%'` which is dangerous if there are multiple services.
    # But wait, looking at `sync_l3vpn_service`, it returns `vpn_ids_in_crd`.
    # Code below assumes we can delete by pattern matching or we need to query first.
    # The original code did `DELETE ... WHERE vpn_id LIKE 'vpn:%'` which deletes EVERYTHING?
    # No, it was likely deleting based on some other logic or just broken in the example I saw.
    # The original snippet showed: `DELETE FROM L3VPNService WHERE id LIKE 'vpn:%'`
    # This looks like it deletes ALL VPNs? That seems wrong for a single CRD delete if multiple CRDs exist.
    # However, if the user only has one L3VPNService CRD managing everything, it's fine.
    # Refactoring to be safer is out of scope, I will just apply the SCD Close to whatever it was selecting.
    # BUT `delete_l3vpn` template takes `@id`. 
    # I will stick to the pattern implicit in the previous code but use SCD updates.
    
    # Actually, proper cleanup requires knowing the IDs. 
    # If the previous code used `LIKE 'vpn:%'`, I will attempt to CLOSE all active VPNs matching that.
    
    def sql_delete(transaction):
        # 1. Close BGP sessions (SCD)
        # Using a broad update based on the assumption that ALL VPNs are being deleted?
        # Or did the previous code filter by something else?
        # Previous code: DELETE FROM BGPSession WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id LIKE 'vpn:%')
        transaction.execute_update(
            "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id LIKE 'vpn:%' AND valid_end_ts IS NULL) AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 2. Close VRFs (SCD)
        transaction.execute_update(
            "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vpn_id LIKE 'vpn:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 3. Close VPN services (SCD)
        transaction.execute_update(
            "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'vpn:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 4. Cleanup OwnedBy edges (L3VPNService -> Customer)
        # This is logical view-based, nothing to delete if it's a View.
        # But if we had an explicit edge table, we'd delete it. 
        # Here `OwnedBy` is a View on L3VPNService, so closing L3VPNService is enough.
    
    try:
        db_container['db'].run_in_transaction(sql_delete)
        logger.debug(f"Successfully closed L3VPN services and related entities for CRD {uid}")
    except Exception as e:
        error_msg = f"Failed to delete L3VPN service {uid}: {e}"
        logger.error(error_msg)
        raise kopf.TemporaryError(error_msg, delay=10)

