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

import asyncio
import logging
import ipaddress
import re
import kubernetes
from typing import Dict, List, Any, Optional

# Shared lock to ensure only one VyOSUnderlay or VyOSL3VPN lifecycle
# operation runs at a time.  Both CR types patch VyOSRouter specs and
# wait for Ansible to complete; allowing them to run concurrently causes
# races on the same physical routers and configuration inconsistencies.
#
# VyOSRouter create/update handlers do NOT acquire this lock — they are
# child operations triggered by the spec patches above, and locking them
# would deadlock the parent waiting for the child to finish.
router_provisioning_lock = asyncio.Lock()

logger = logging.getLogger(__name__)

#########################################################################
# Validation Functions
#########################################################################

def validate_network_topology(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the network topology for consistency and conflicts"""
    try:
        networks = spec.get('networks', [])
        routers = spec.get('routers', [])
        qos_policies = spec.get('qos', {}).get('policies', [])
        firewall_policies = spec.get('security', {}).get('firewall', {}).get('policies', [])
        
        # Validate management network requirement
        management_validation = validate_management_network(networks)
        if not management_validation['valid']:
            return management_validation

        # Validate management interface assignments
        management_interface_validation = validate_management_interface_assignments(networks, routers)
        if not management_interface_validation['valid']:
            return management_interface_validation

        # Validate IP address assignments
        ip_validation = validate_ip_assignments(networks, routers)
        if not ip_validation['valid']:
            return ip_validation
        
        # Validate router interface mappings
        interface_validation = validate_interface_mappings(networks, routers)
        if not interface_validation['valid']:
            return interface_validation
        
        # Validate policy references
        policy_validation = validate_policy_references(routers, qos_policies, firewall_policies)
        if not policy_validation['valid']:
            return policy_validation
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Validation error: {str(e)}"}

def validate_management_network(networks: List[Dict]) -> Dict[str, Any]:
    """Validate that there is exactly one management network"""
    try:
        management_networks = [
            network for network in networks 
            if network.get('network_type') == 'management'
        ]
        
        if len(management_networks) == 0:
            return {
                'valid': False, 
                'error': "Network topology must include exactly one network with network_type 'management'"
            }
        elif len(management_networks) > 1:
            management_names = [net['name'] for net in management_networks]
            return {
                'valid': False,
                'error': f"Network topology must have only one management network, but found {len(management_networks)}: {', '.join(management_names)}"
            }
        # check if the network has a vlan defined
        if management_networks[0].get('vlan'):
            return {
                'valid': False,
                'error': "Management network must not have a 'vlan' defined"
            }
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Management network validation error: {str(e)}"}

def get_management_network_name(networks: List[Dict]) -> Optional[str]:
    """Get the name of the management network from the network list"""
    try:
        management_networks = [
            network for network in networks 
            if network.get('network_type') == 'management'
        ]
        
        # Return the name of the first (and should be only) management network
        return management_networks[0]['name'] if management_networks else None
        
    except Exception as e:
        logger.warning(f"Error getting management network name: {str(e)}")
        return None

def validate_management_interface_assignments(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate that each router uses the management network as its first interface"""
    try:
        # Get the management network name
        management_network_name = get_management_network_name(networks)
        if not management_network_name:
            # This should be caught by validate_management_network, but being defensive
            return {
                'valid': False,
                'error': "No management network found for interface validation"
            }
        
        # Check each router's interface assignments
        for router in routers:
            router_name = router['name']
            interfaces = router.get('interfaces', [])
            
            # Check that router has at least one interface
            if not interfaces:
                return {
                    'valid': False,
                    'error': f"Router {router_name} must have at least one interface defined"
                }
            
            # Get the first interface (should be management)
            first_interface = interfaces[0]
            
            # Validate first interface points to management network
            if first_interface['network'] != management_network_name:
                return {
                    'valid': False,
                    'error': f"Router {router_name} first interface ({first_interface['name']}) must connect to management network '{management_network_name}', but is connected to '{first_interface['network']}'"
                }
        
        # Also validate that the management network connects all routers via their first interface
        management_network = None
        for network in networks:
            if network['name'] == management_network_name:
                management_network = network
                break
        
        if management_network:
            connected_routers = management_network.get('connected_routers', [])
            for router_conn in connected_routers:
                router_name = router_conn['router_name']
                interface_name = router_conn['interface']
                
                # Find the corresponding router to check if this is its first interface
                router = None
                for r in routers:
                    if r['name'] == router_name:
                        router = r
                        break
                
                if router and router.get('interfaces'):
                    first_interface_name = router['interfaces'][0]['name']
                    if interface_name != first_interface_name:
                        return {
                            'valid': False,
                            'error': f"Management network '{management_network_name}' must connect router {router_name} via its first interface ({first_interface_name}), but uses {interface_name}"
                        }
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Management interface validation error: {str(e)}"}

def validate_ip_assignments(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate IP address assignments for conflicts and consistency"""
    try:
        # Build a map of network subnets
        network_subnets = {}
        for network in networks:
            network_name = network['name']
            subnet = ipaddress.ip_network(network['subnet'])
            network_subnets[network_name] = subnet
        
        # Check for subnet overlaps
        subnet_list = list(network_subnets.values())
        for i, subnet1 in enumerate(subnet_list):
            for j, subnet2 in enumerate(subnet_list[i+1:], i+1):
                if subnet1.overlaps(subnet2):
                    return {'valid': False, 'error': f"Subnet overlap detected: {subnet1} and {subnet2}"}
        
        # Validate router interface IP assignments
        for network in networks:
            network_name = network['name']
            subnet = network_subnets[network_name]
            connected_routers = network.get('connected_routers', [])
            
            used_ips = set()
            for router_conn in connected_routers:
                ip_addr = ipaddress.ip_address(router_conn['ip_address'])
                
                # Check if IP is in the network subnet
                if ip_addr not in subnet:
                    return {'valid': False, 'error': f"IP {ip_addr} not in subnet {subnet} for network {network_name}"}
                
                # Check for duplicate IP assignments
                if ip_addr in used_ips:
                    return {'valid': False, 'error': f"Duplicate IP assignment {ip_addr} in network {network_name}"}
                
                used_ips.add(ip_addr)
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"IP validation error: {str(e)}"}

def validate_interface_mappings(networks: List[Dict], routers: List[Dict]) -> Dict[str, Any]:
    """Validate router interface to network mappings"""
    try:
        # Build network connection map
        network_connections = {}
        for network in networks:
            network_name = network['name']
            connected_routers = network.get('connected_routers', [])
            network_connections[network_name] = {
                conn['router_name']: conn['interface'] for conn in connected_routers
            }
        
        # Validate router interface definitions
        for router in routers:
            router_name = router['name']
            interfaces = router.get('interfaces', [])
            
            for interface in interfaces:
                interface_name = interface['name']
                network_name = interface['network']
                
                # Skip loopback interfaces
                if interface_name == 'lo':
                    continue
                
                # Check if network exists
                if network_name not in network_connections:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent network {network_name}"}
                
                # Check if router is connected to this network
                if router_name not in network_connections[network_name]:
                    return {'valid': False, 'error': f"Router {router_name} not connected to network {network_name}"}
                
                # Check if interface matches
                expected_interface = network_connections[network_name][router_name]
                if interface_name != expected_interface:
                    return {'valid': False, 'error': f"Interface mismatch for router {router_name}: expected {expected_interface}, got {interface_name}"}
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Interface mapping validation error: {str(e)}"}

def validate_policy_references(routers: List[Dict], qos_policies: List[Dict], firewall_policies: List[Dict]) -> Dict[str, Any]:
    """Validate that all policy references exist"""
    try:
        # Build policy name sets
        qos_policy_names = {policy['name'] for policy in qos_policies}
        firewall_policy_names = {policy['name'] for policy in firewall_policies}
        
        # Check router policy references
        for router in routers:
            router_name = router['name']
            
            # Check QoS policy references
            qos_policies_applied = router.get('qos_policies', [])
            for qos_policy in qos_policies_applied:
                policy_name = qos_policy['policy_name']
                if policy_name not in qos_policy_names:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent QoS policy {policy_name}"}
            
            # Check firewall policy references
            firewall_policies_applied = router.get('firewall_policies', [])
            for firewall_policy in firewall_policies_applied:
                policy_name = firewall_policy['policy_name']
                if policy_name not in firewall_policy_names:
                    return {'valid': False, 'error': f"Router {router_name} references non-existent firewall policy {policy_name}"}
        
        return {'valid': True}
        
    except Exception as e:
        return {'valid': False, 'error': f"Policy reference validation error: {str(e)}"}

#########################################################################
# CR Generation
#########################################################################

def generate_linux_networks(spec: Dict[str, Any], parent_name: str, parent_namespace: str, parent_uid: str, parent_kind: str) -> List[Dict[str, Any]]:
    """Generate LinuxNetwork CRs from parent spec"""
    networks = spec.get('networks', [])
    linux_network_crs = []
    
    for network in networks:
        # Skip loopback networks - they don't need Linux networks
        if network['name'] == 'loopbacks':
            continue
        
        linux_network_cr = {
            'apiVersion': 'google.dev/v1',
            'kind': 'LinuxNetwork',
            'metadata': {
                'name': network['name'],
                'namespace': parent_namespace,
                'labels': {
                    'parent-kind': parent_kind,
                    'parent-name': parent_name,
                    'network-type': network.get('network_type', 'p2p'),
                    'environment': 'lab'
                },
                'ownerReferences': [{
                    'apiVersion': 'google.dev/v1',
                    'kind': parent_kind,
                    'name': parent_name,
                    'uid': parent_uid,
                    'controller': True,
                    'blockOwnerDeletion': True
                }]
            },
            'spec': {
                'name': network['name'],
                'source_network': parent_name,
                'network_type': network.get('network_type', 'p2p'),
                'connected_routers': network.get('connected_routers', [])
            }
        }

        # Only include bandwidth when it is a valid value matching the CRD pattern
        # (^[0-9]+[kmg]?bit$).  Values like "unlimited" or None are silently omitted,
        # which means no bandwidth cap is applied on the Linux network.
        bandwidth = network.get('bandwidth')
        if bandwidth and re.match(r'^[0-9]+[kmg]?bit$', bandwidth):
            linux_network_cr['spec']['bandwidth'] = bandwidth

        # Add gateway if specified
        if 'gateway' in network:
            linux_network_cr['spec']['gateway'] = network['gateway']
        
        linux_network_crs.append(linux_network_cr)
    
    return linux_network_crs

def generate_vyos_routers(spec: Dict[str, Any], parent_name: str, parent_namespace: str, parent_uid: str, parent_kind: str) -> List[Dict[str, Any]]:
    """Generate VyOSRouter CRs from parent spec"""
    routers = spec.get('routers', [])
    networks = spec.get('networks', [])
    vyos_router_crs = []
    
    # Build network lookup for IP address resolution
    network_lookup = build_network_lookup(networks)
    
    for router in routers:
        vyos_router_cr = {
            'apiVersion': 'google.dev/v1',
            'kind': 'VyOSRouter',
            'metadata': {
                'name': router['name'],
                'namespace': parent_namespace,
                'labels': {
                    'parent-kind': parent_kind,
                    'parent-name': parent_name,
                },
                'ownerReferences': [{
                    'apiVersion': 'google.dev/v1',
                    'kind': parent_kind,
                    'name': parent_name,
                    'uid': parent_uid,
                    'controller': True,
                    'blockOwnerDeletion': True
                }]
            },
            'spec': {
                'hostname': router['hostname'],
                'router_id': router['router_id'],
                'image': router.get('image', 'vyos:1.5'),
                'source_network': parent_name,
                'location': router.get('location', {}),
                'interfaces': generate_router_interfaces(router, network_lookup),
                'vrfs': router.get('vrfs', []),
                'protocols': generate_router_protocols(router),
                'services': router.get('services', {}),
                'qos': generate_router_qos_config(router),
                'firewall': generate_router_firewall_config(router),
                'traffic_policy': router.get('traffic_policy', {}),
                'role': router.get('role', 'P')
            }
        }
        
        vyos_router_crs.append(vyos_router_cr)
    
    return vyos_router_crs

def build_network_lookup(networks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build a lookup table for network information"""
    network_lookup = {}
    
    for network in networks:
        network_name = network['name']
        connected_routers = network.get('connected_routers', [])
        
        # Build router to IP mapping for this network
        router_ips = {}
        for conn in connected_routers:
            router_ips[conn['router_name']] = {
                'ip_address': conn['ip_address'],
                'interface': conn['interface']
            }
        
        network_lookup[network_name] = {
            'subnet': network['subnet'],
            'mtu': network.get('mtu', 1500),
            'vlan': network.get('vlan'),
            'router_ips': router_ips,
            'linux_network': network_name if network_name != 'loopbacks' else None
        }

        # if there is gateway defined, add it
        if 'gateway' in network:
            network_lookup[network_name]['gateway'] = network['gateway']

        # Include bandwidth so it can be applied as a host-side veth TC limit.
        # Only store values that match the tc-compatible pattern (e.g. "100mbit",
        # "1gbit").  "unlimited" and None are intentionally excluded so that
        # management and loopback networks never get a TC qdisc applied.
        bandwidth = network.get('bandwidth')
        if bandwidth and re.match(r'^[0-9]+[kmg]?bit$', bandwidth):
            network_lookup[network_name]['bandwidth'] = bandwidth

    return network_lookup

def generate_router_interfaces(router: Dict[str, Any], network_lookup: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate interface configuration for a router"""
    interfaces = []
    router_name = router['name']
    
    for interface_def in router.get('interfaces', []):
        interface_name = interface_def['name']
        network_name = interface_def['network']
        
        # Handle loopback interfaces
        if interface_name == 'lo':
            interfaces.append({
                'name': 'lo',
                'description': interface_def.get('description', 'Loopback interface'),
                'address': f"{router['router_id']}/32",
                'mtu': 1500,
                'enabled': True
            })
            continue
        
        # Get network information
        if network_name not in network_lookup:
            logger.warning(f"Network {network_name} not found for router {router_name}")
            continue
        
        network_info = network_lookup[network_name]
        router_ip_info = network_info['router_ips'].get(router_name)
        
        if not router_ip_info:
            logger.warning(f"No IP assignment found for router {router_name} on network {network_name}")
            continue
        
        # Calculate subnet mask from network subnet
        subnet = ipaddress.ip_network(network_info['subnet'])
        ip_with_mask = f"{router_ip_info['ip_address']}/{subnet.prefixlen}"
        
        interface_config = {
            'name': interface_name,
            'description': interface_def.get('description', ''),
            'address': ip_with_mask,
            'mtu': network_info['mtu'],
            'enabled': interface_def.get('enabled', True)
        }

        # Add gateway if specified
        if 'gateway' in network_info:
            interface_config['gateway'] = network_info['gateway']

        # Add VRF if specified
        if 'vrf' in interface_def:
            interface_config['vrf'] = interface_def['vrf']
        
        # Add Linux network reference
        if network_info['linux_network']:
            interface_config['linux_network'] = network_info['linux_network']
        
        # Add VLAN if specified
        if network_info['vlan'] is not None:
            interface_config['vlan'] = network_info['vlan']
        
        interfaces.append(interface_config)
    
    return interfaces

def generate_router_protocols(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate protocol configuration for a router"""
    protocols = {}
    router_protocols = router.get('protocols', {})
    
    # OSPF configuration
    if 'ospf' in router_protocols and router_protocols['ospf'].get('enabled'):
        ospf_config = {
            'router_id': router['router_id'],
            'areas': []
        }
        
        for area_id in router_protocols['ospf'].get('areas', []):
            networks = []
            
            # Add connected interface networks to the appropriate OSPF area
            for interface in router.get('interfaces', []):
                if interface['name'] == 'lo':
                    continue
                if interface.get('vrf'):
                    continue
                # Note: Actual subnets are populated by the generator using the specification
                pass
            
            # Add loopback network for all areas (router-id advertisement)
            if area_id == '0.0.0.0':  # Backbone area gets the loopback
                networks.append(f"{router['router_id']}/32")
            
            area_config = {
                'area': area_id,
                'networks': networks,
                'type': 'standard' if area_id != '0.0.0.0' else 'backbone'
            }
            ospf_config['areas'].append(area_config)
        
        # Add passive interfaces if specified
        if 'passive_interfaces' in router_protocols['ospf']:
            ospf_config['passive_interfaces'] = router_protocols['ospf']['passive_interfaces']
        
        protocols['ospf'] = ospf_config
    
    # BGP configuration
    if 'bgp' in router_protocols and router_protocols['bgp'].get('enabled'):
        bgp_config = {
            'as_number': router_protocols['bgp']['as_number'],
            'router_id': router['router_id'],
            'route_reflector': router_protocols['bgp'].get('route_reflector', False),
            'neighbors': router_protocols['bgp'].get('neighbors', []),
            'vrfs': router_protocols['bgp'].get('vrfs', [])
        }
        
        # Add address families if specified
        if 'address_families' in router_protocols['bgp']:
            bgp_config['address_families'] = router_protocols['bgp']['address_families']
        else:
            # Auto-enable VPNv4 address family for PE routers (with VRFs) and route reflectors
            has_vrfs = len(router.get('vrfs', [])) > 0
            is_route_reflector = router_protocols['bgp'].get('route_reflector', False)
            
            if has_vrfs or is_route_reflector:
                bgp_config['address_families'] = [{'family': 'vpnv4'}]
        
        protocols['bgp'] = bgp_config
    
    # MPLS configuration
    if 'mpls' in router_protocols and router_protocols['mpls'].get('enabled'):
        mpls_config = {
            'enabled': True,
            'ldp': {
                'router_id': router['router_id'],
                'interfaces': router_protocols['mpls'].get('ldp_interfaces', [])
            }
        }
        protocols['mpls'] = mpls_config
    
    # Static routes configuration
    if 'static' in router_protocols and router_protocols['static'].get('routes'):
        static_config = {
            'routes': router_protocols['static']['routes']
        }
        protocols['static'] = static_config
    
    return protocols

def generate_router_qos_config(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate QoS configuration for a router"""
    qos_config = {'policies': []}
    
    for qos_policy in router.get('qos_policies', []):
        qos_config['policies'].append({
            'name': qos_policy['policy_name'],
            'interface': qos_policy['interface'],
            'direction': qos_policy.get('direction', 'out')
        })
    
    return qos_config

def generate_router_firewall_config(router: Dict[str, Any]) -> Dict[str, Any]:
    """Generate firewall configuration for a router"""
    firewall_config = {'policies': []}
    
    for firewall_policy in router.get('firewall_policies', []):
        firewall_config['policies'].append({
            'name': firewall_policy['policy_name'],
            'interface': firewall_policy['interface'],
            'direction': firewall_policy['direction']
        })
    
    return firewall_config

#########################################################################
# Kubernetes API Operations
#########################################################################

async def create_linux_network(network_cr: Dict[str, Any], namespace: str):
    """Create a LinuxNetwork custom resource.

    Raises:
        RuntimeError: when a 409 conflict is returned and the existing resource
            has a ``deletionTimestamp`` (i.e. it is still terminating).  The
            caller should treat this as a transient failure and retry later.
        kubernetes.client.rest.ApiException: for any non-409 API errors.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    name = network_cr['metadata']['name']

    try:
        api.create(network_cr)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            # Check whether the conflicting resource is being deleted.  If it
            # has a deletionTimestamp the CR is still terminating — silently
            # skipping creation here would leave the network permanently absent
            # once the old object is finally removed from etcd.
            try:
                existing = api.get(name=name, namespace=namespace)
                existing_dict = existing.to_dict() if hasattr(existing, 'to_dict') else existing
                if existing_dict.get('metadata', {}).get('deletionTimestamp'):
                    raise RuntimeError(
                        f"LinuxNetwork '{name}' is still terminating "
                        f"(deletionTimestamp={existing_dict['metadata']['deletionTimestamp']}); "
                        "will retry after it is fully removed"
                    )
                # Truly already exists and is healthy — nothing to do.
                logger.info(f"LinuxNetwork '{name}' already exists and is not terminating")
            except kubernetes.client.rest.ApiException as get_exc:
                if get_exc.status == 404:
                    # Disappeared between the create attempt and this get — no-op.
                    logger.info(f"LinuxNetwork '{name}' vanished between create-409 and get; treating as success")
                else:
                    raise
        else:
            raise

async def create_vyos_router(router_cr: Dict[str, Any], namespace: str):
    """Create a VyOSRouter custom resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
    
    try:
        api.create(router_cr)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            logger.info(f"VyOSRouter {router_cr['metadata']['name']} already exists, updating...")
            try:
                # Patch the existing router to ensure it stays in sync
                await patch_vyos_router(
                    router_cr['metadata']['name'],
                    router_cr['metadata']['namespace'],
                    {
                        'metadata': {'labels': router_cr['metadata']['labels']},
                        'spec': router_cr['spec']
                    }
                )
            except Exception as patch_error:
                logger.error(f"Failed to update existing VyOSRouter {router_cr['metadata']['name']}: {patch_error}")
        else:
            raise

async def update_status(name: str, namespace: str, kind: str, phase: str, message: str, 
                       networks: Optional[List[str]] = None, routers: Optional[List[str]] = None,
                       devices: Optional[List[str]] = None):
    """Update the status of a Custom Resource.

    Uses a direct merge-patch on the status subresource rather than a
    read-modify-write cycle.  This avoids an extra API round-trip per status
    update and eliminates the race window where kopf's own status.kopf.progress
    writes could be overwritten by a stale full-resource read.
    """
    logger.info(f"Updating {kind} {name} status to {phase}: {message}")

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind=kind)

    status = {
        'phase': phase,
        'message': message
    }
    if networks:
        status['networks'] = networks
    if routers:
        status['routers'] = routers
    if devices:
        status['devices'] = devices

    try:
        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': status},
            content_type='application/merge-patch+json',
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for {kind} {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for {kind} {name}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status for {kind} {name}: {e}")

async def patch_vyos_router(name: str, namespace: str, patch: Dict[str, Any]):
    """Patch a VyOSRouter custom resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
    
    try:
        api.patch(
            namespace=namespace,
            name=name,
            body=patch,
            content_type='application/merge-patch+json'
        )
        logger.info(f"Patched VyOSRouter: {name}")
    except kubernetes.client.rest.ApiException as e:
        logger.error(f"Failed to patch VyOSRouter {name}: {e}")
        raise

async def check_and_update_parent_status(parent_name: str, parent_kind: str, namespace: str, logger):
    """Check if all child VyOSRouters and Devices are Ready and update parent status accordingly"""
    logger.info(f"Checking parent {parent_kind} {parent_name} status for child readiness")
    
    try:
        # Get the parent resource
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        parent_api = client.resources.get(api_version='google.dev/v1', kind=parent_kind)
        vyosrouter_api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
        device_api = client.resources.get(api_version='google.dev/v1', kind='Device')
        
        # Get the parent
        try:
            parent_res = parent_api.get(name=parent_name, namespace=namespace)
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                logger.info(f"Parent {parent_kind} {parent_name} not found, may have been deleted")
                return
            else:
                raise
        
        # Check current status - skip terminal/in-progress-deletion states and
        # states that are actively managed by the infrastructure controller itself.
        # We must also re-evaluate from 'Ready' because routers may reach Running
        # before all Devices are Ready, causing a premature Ready transition that
        # needs to be corrected when the device events arrive.
        current_status = parent_res.get('status', {})
        current_phase = current_status.get('phase', '')
        
        if current_phase in ['Error', 'Deleting', 'Validating', 'Processing']:
            logger.info(f"Parent {parent_kind} {parent_name} is in {current_phase} state - skipping check")
            return
        
        # Get all child VyOSRouters that belong to this parent
        all_routers = vyosrouter_api.get(namespace=namespace)
        child_routers = []
        
        for router in all_routers.items:
            owner_references = router.get('metadata', {}).get('ownerReferences', [])
            for owner in owner_references:
                if owner.get('kind') == parent_kind and owner.get('name') == parent_name:
                    child_routers.append(router)
                    break

        # Get all child Devices that belong to this parent
        child_devices = []
        try:
            all_devices = device_api.get(namespace=namespace)
            for device in all_devices.items:
                owner_references = device.get('metadata', {}).get('ownerReferences', [])
                for owner in owner_references:
                    if owner.get('kind') == parent_kind and owner.get('name') == parent_name:
                        child_devices.append(device)
                        break
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                pass  # Device CRD not installed — no children of this kind
            else:
                raise

        if not child_routers and not child_devices:
            logger.warning(f"No child resources found for {parent_kind} {parent_name}")
            return
        
        # Check if all child routers are Ready (status.phase == "Running")
        ready_routers = []
        failed_routers = []
        not_ready_routers = []

        for router in child_routers:
            router_name = router.get('metadata', {}).get('name', 'unknown')
            router_status = router.get('status', {})
            router_phase = router_status.get('phase', 'Unknown')

            if router_phase == 'Running':
                ready_routers.append(router_name)
            elif router_phase == 'Failed':
                failed_routers.append(router_name)
            else:
                not_ready_routers.append(f"{router_name}({router_phase})")

        # Check if all child Devices are Ready (status.phase == "Ready")
        ready_devices = []
        failed_devices = []
        not_ready_devices = []

        for device in child_devices:
            device_name = device.get('metadata', {}).get('name', 'unknown')
            device_status = device.get('status', {})
            device_phase = device_status.get('phase', 'Unknown')

            if device_phase == 'Ready':
                ready_devices.append(device_name)
            elif device_phase == 'Failed':
                failed_devices.append(device_name)
            else:
                not_ready_devices.append(f"{device_name}({device_phase})")

        # If any child permanently failed, propagate Error immediately rather than
        # staying stuck in Creating forever with no recovery path.
        failed_children = failed_routers + failed_devices
        if failed_children:
            error_message = f"Children permanently failed: {', '.join(failed_children)}"
            logger.error(f"{parent_kind} {parent_name}: {error_message}")
            await update_status(parent_name, namespace, parent_kind, "Error", error_message)
            return

        total_routers = len(child_routers)
        ready_router_count = len(ready_routers)
        total_devices = len(child_devices)
        ready_device_count = len(ready_devices)

        all_ready = (ready_router_count == total_routers) and (ready_device_count == total_devices)

        if all_ready:
            parts = []
            if total_routers:
                parts.append(f"{total_routers} router(s)")
            if total_devices:
                parts.append(f"{total_devices} device(s)")
            success_message = f"All {' and '.join(parts)} are ready"
            await update_status(parent_name, namespace, parent_kind, "Ready", success_message)
        else:
            # If the parent is already Ready, do NOT downgrade its status based on
            # transient router states caused by external controllers (VyOSUnderlay or
            # VyOSL3VPN patching routers for protocol/VRF updates).  Those controllers
            # own the router spec changes and are responsible for waiting on the routers
            # themselves.  Downgrading the infrastructure to "Creating" here causes a
            # cascade: VyOSUnderlay sees the infrastructure as not-Ready and enters
            # "Waiting", even though the infrastructure itself is healthy.
            #
            # Only downgrade to "Creating" when the parent is in a state that indicates
            # it is actively being reconciled by the infrastructure controller (i.e.,
            # "Creating" or empty/unknown — meaning initial bring-up is still in progress).
            if current_phase == 'Ready':
                logger.info(
                    f"Parent {parent_kind} {parent_name} is Ready but some children are "
                    f"not yet Running ({not_ready_routers + not_ready_devices}) — "
                    f"skipping status downgrade; children are likely being updated by "
                    f"VyOSUnderlay or VyOSL3VPN"
                )
                return

            waiting_parts = []
            if not_ready_routers:
                waiting_parts.append(
                    f"routers: {', '.join(not_ready_routers)} ({ready_router_count}/{total_routers} ready)"
                )
            if not_ready_devices:
                waiting_parts.append(
                    f"devices: {', '.join(not_ready_devices)} ({ready_device_count}/{total_devices} ready)"
                )
            status_message = f"Waiting for {'; '.join(waiting_parts)}"
            await update_status(parent_name, namespace, parent_kind, "Creating", status_message)
            
    except Exception as e:
        logger.error(f"Failed to check parent status for {parent_name}: {e}")

#########################################################################
# Device CR Generation
#########################################################################

def generate_devices(spec: Dict[str, Any], parent_name: str, parent_namespace: str, parent_uid: str, parent_kind: str) -> List[Dict[str, Any]]:
    """Generate Device CRs from a VyOSInfrastructure spec.

    Each entry in ``spec.devices`` becomes a Device CR owned by the parent.
    The management bridge name is looked up automatically from the
    ``management`` network in the spec so callers don't have to pass it.
    """
    devices = spec.get('devices', [])
    if not devices:
        return []

    management_network_name = get_management_network_name(spec.get('networks', []))
    device_crs = []

    for device in devices:
        device_cr: Dict[str, Any] = {
            'apiVersion': 'google.dev/v1',
            'kind': 'Device',
            'metadata': {
                'name': device['name'],
                'namespace': parent_namespace,
                'labels': {
                    'parent-kind': parent_kind,
                    'parent-name': parent_name,
                    'environment': 'lab',
                },
                'ownerReferences': [{
                    'apiVersion': 'google.dev/v1',
                    'kind': parent_kind,
                    'name': parent_name,
                    'uid': parent_uid,
                    'controller': True,
                    'blockOwnerDeletion': True,
                }],
            },
            'spec': {
                'network_name': device['network_name'],
                'ip_address': device['ip_address'],
                'mgmt_ip': device['mgmt_ip'],
                # Management bridge name derived from the infrastructure spec
                'mgmt_network': management_network_name or '',
            },
        }

        if 'gateway' in device:
            device_cr['spec']['gateway'] = device['gateway']
        if 'vlan' in device:
            device_cr['spec']['vlan'] = device['vlan']
        if 'image' in device:
            device_cr['spec']['image'] = device['image']
        if 'extra_interfaces' in device:
            device_cr['spec']['extra_interfaces'] = device['extra_interfaces']

        device_crs.append(device_cr)

    return device_crs


async def create_device(device_cr: Dict[str, Any], namespace: str):
    """Create a Device custom resource (idempotent — ignores 409 Conflict)."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')

    try:
        api.create(device_cr)
        logger.info(f"Created Device {device_cr['metadata']['name']}")
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:
            logger.info(f"Device {device_cr['metadata']['name']} already exists — skipping")
        else:
            raise


