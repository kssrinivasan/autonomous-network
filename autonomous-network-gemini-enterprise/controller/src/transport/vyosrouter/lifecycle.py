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
from typing import Dict, Any, Optional
import kubernetes
from transport.vyosrouter.lifecycle_tasks import (
    create_vyos_router,
    delete_vyos_router,
    update_vyos_router,
    configure_vyos_router,
    check_linux_networks_ready
)
from utils.compute import *
from graph.lifecycle_tasks import sync_physical_router

logger = logging.getLogger(__name__)

#########################################################################
# VyOSRouter Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'vyosrouter')
async def create_vyosrouter(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSRouter creation using Ansible"""
    logger.info(f"Creating VyOSRouter: {name} in namespace: {namespace}")

    # Idempotency guard: if already Running (e.g. operator restarted after Ansible
    # succeeded but before kopf recorded completion), skip re-creation entirely.
    current_phase = body.get('status', {}).get('phase')
    if current_phase == 'Running':
        logger.info(f"VyOSRouter {name} already in Running state, skipping re-creation")
        return

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    # Update status to indicate pending (automatically syncs to Spanner)
    await update_status(name, namespace, "Pending", "Validating and waiting for dependencies",
                       body=body, spec=spec, uid=uid, logger_obj=logger)

    # Extract router configuration from spec
    router_config = {
        'name': name,
        'hostname': spec['hostname'],
        'router_id': spec['router_id'],
        'image': spec.get('image', 'vyos:1.5'),
        'source_network': spec.get('source_network'),
        'interfaces': spec.get('interfaces', []),
        'vrfs': spec.get('vrfs', []),
        'protocols': spec.get('protocols', {}),
        'services': spec.get('services', {}),
        'qos': spec.get('qos', {}),
        'firewall': spec.get('firewall', {}),
        'traffic_policy': spec.get('traffic_policy', {})
    }

    # Enrich interfaces with network-level bandwidth from the parent
    # VyOSInfrastructure CR.  Bandwidth is NOT stored in the VyOSRouter spec
    # (it is not a CRD field) — we look it up at runtime so the Ansible
    # playbook can apply a TC HTB qdisc on the host-side veth, mimicking a
    # physical link speed without any VyOS traffic-policy configuration.
    source_network = spec.get('source_network')
    if source_network:
        try:
            k8s_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
            infra_api = k8s_client.resources.get(api_version='google.dev/v1', kind='VyOSInfrastructure')
            infra_cr = infra_api.get(name=source_network, namespace=namespace)
            infra_spec = infra_cr.get('spec', {}) if hasattr(infra_cr, 'get') else infra_cr.to_dict().get('spec', {})
            # Build a network-name → bandwidth map (only valid TC values)
            import re as _re
            net_bandwidth = {}
            for net in infra_spec.get('networks', []):
                bw = net.get('bandwidth')
                if bw and _re.match(r'^[0-9]+[kmg]?bit$', bw):
                    net_bandwidth[net['name']] = bw
            # Inject bandwidth into each interface that has a matching linux_network
            if net_bandwidth:
                enriched_interfaces = []
                for iface in router_config['interfaces']:
                    iface = dict(iface)  # shallow copy — don't mutate the spec object
                    linux_net = iface.get('linux_network')
                    if linux_net and linux_net in net_bandwidth:
                        iface['bandwidth'] = net_bandwidth[linux_net]
                    enriched_interfaces.append(iface)
                router_config['interfaces'] = enriched_interfaces
                logger.info(f"VyOSRouter {name}: enriched interfaces with network bandwidths: {net_bandwidth}")
        except Exception as bw_err:
            # Non-fatal — if we can't look up the infra, proceed without TC shaping
            logger.warning(f"VyOSRouter {name}: could not look up VyOSInfrastructure '{source_network}' for bandwidth enrichment: {bw_err}")

    logger.info(f"VyOSRouter config: {router_config}")

    # Check if all required LinuxNetwork CRs are ready
    interfaces = router_config.get('interfaces', [])
    if interfaces:
        all_ready, not_ready_networks = await check_linux_networks_ready(interfaces, namespace)
        
        if not all_ready:
            # Build detailed error message and check for permanent failures
            network_details = []
            has_permanent_failure = False
            for net in not_ready_networks:
                network_details.append(f"{net['name']} (phase: {net['phase']}, message: {net['message']})")
                if net['phase'] == 'Failed':
                    has_permanent_failure = True

            error_msg = f"Waiting for LinuxNetwork(s) to be ready: {', '.join(network_details)}"
            logger.warning(f"VyOSRouter {name}: {error_msg}")

            await update_status(name, namespace, "Pending", error_msg)

            if has_permanent_failure:
                # A dependency is permanently failed — stop retrying
                raise kopf.PermanentError(error_msg)
            else:
                # Dependency is still coming up — retry later
                raise kopf.TemporaryError(error_msg, delay=20)
        
    try:
        # Update status to indicate creation has started (automatically syncs to Spanner)
        await update_status(name, namespace, "Creating", "Creating VyOS router container",
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        
        # Create the VyOS router container using Ansible
        result = await create_vyos_router(ip_address, router_config)
        
        if result['success']:
            # Update status to configuring (automatically syncs to Spanner)
            await update_status(
                name, namespace, "Configuring", 
                "Applying VyOS configuration",
                body=body, spec=spec, uid=uid, logger_obj=logger,
                container_id=result.get('container_id')
            )
            
            # Configure the VyOS router
            config_result = await configure_vyos_router(ip_address, router_config)
            
            if config_result['success']:
                # Initialize interface status from spec
                interfaces_status = []
                for iface in router_config['interfaces']:
                    interfaces_status.append({
                        'name': iface['name'],
                        'status': 'up',  # Assume up initially
                        'linux_network': iface.get('linux_network', '')
                    })
                
                # Prefer the IP returned by Ansible; fall back to the mgmt interface in
                # spec (Ansible doesn't always echo management_ip on idempotent reruns).
                management_ip = result.get('management_ip', '')
                if not management_ip:
                    for iface in router_config.get('interfaces', []):
                        if iface.get('linux_network') == 'mgmt':
                            addr = iface.get('address', '')
                            management_ip = addr.split('/')[0] if '/' in addr else addr
                            break

                # Update status to running with interface details (automatically syncs to Spanner)
                await update_status(
                    name, namespace, "Running",
                    f"VyOS router {name} is running and configured",
                    body=body, spec=spec, uid=uid, logger_obj=logger,
                    container_id=result.get('container_id'),
                    ip_address=management_ip,
                    interfaces=interfaces_status,
                    applied_config=config_result.get('applied_config')
                )
                logger.info(f"Successfully created and configured VyOSRouter {name}")
            else:
                # Configuration errors - check if transient or permanent
                error_msg = config_result.get('error', 'Unknown configuration error')
                if _is_transient_error(error_msg):
                    logger.warning(f"Transient configuration error for {name}, will retry: {error_msg}")
                    await update_status(name, namespace, "Pending", f"Configuration pending retry: {error_msg}",
                                       body=body, spec=spec, uid=uid, logger_obj=logger)
                    raise kopf.TemporaryError(f"Transient configuration error: {error_msg}", delay=30)
                else:
                    await update_status(name, namespace, "Failed", f"Configuration failed: {error_msg}",
                                       body=body, spec=spec, uid=uid, logger_obj=logger)
                    raise kopf.PermanentError(f"VyOS router configuration failed: {error_msg}")
        else:
            # Container creation failed - check if transient or permanent
            error_msg = result.get('error', 'Unknown creation error')
            if _is_transient_error(error_msg):
                logger.warning(f"Transient creation error for {name}, will retry: {error_msg}")
                await update_status(name, namespace, "Pending", f"Creation pending retry: {error_msg}",
                                   body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.TemporaryError(f"Transient creation error: {error_msg}", delay=30)
            else:
                await update_status(name, namespace, "Failed", f"Failed to create container: {error_msg}",
                                   body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.PermanentError(f"VyOS router creation failed: {error_msg}")

    except (kopf.TemporaryError, kopf.PermanentError):
        # Re-raise kopf control exceptions without double-handling
        raise
    except Exception as e:
        # Unexpected exception - check if it looks transient before permanently failing
        error_msg = str(e)
        logger.error(f"Unexpected error creating VyOSRouter {name}: {error_msg}")
        if _is_transient_error(error_msg):
            logger.warning(f"Treating as transient error, will retry: {error_msg}")
            await update_status(name, namespace, "Pending", f"Retrying after error: {error_msg}",
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(error_msg, delay=30)
        else:
            await update_status(name, namespace, "Failed", error_msg,
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise

@kopf.on.update('google.dev', 'v1', 'vyosrouter')
async def update_vyosrouter(body, spec, name, namespace, uid, logger, diff, **kwargs):
    """Handle VyOSRouter updates using Ansible.

    Reacts to either a spec change or an explicit reconcile-trigger annotation
    written by the VyOSL3VPN handler when it detects that a router is in Failed
    state and the spec patch would otherwise be a no-op (identical values already
    stored → no K8s update event → update handler never fires → router stays stuck).
    """
    RECONCILE_ANNOTATION = 'vyos.google.dev/vpn-reconcile-trigger'

    relevant = any(
        (len(fp) > 0 and fp[0] == 'spec') or
        fp == ('metadata', 'annotations', RECONCILE_ANNOTATION)
        for _, fp, _, _ in diff
    )
    if not relevant:
        logger.debug(
            f"VyOSRouter {name}: update event contains no spec or reconcile-trigger "
            f"changes — skipping Ansible run"
        )
        return

    logger.info(f"Updating VyOSRouter: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        await update_status(name, namespace, "Updating", "Updating VyOS router configuration",
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        
        # Extract updated router configuration
        router_config = {
            'name': name,
            'hostname': spec['hostname'],
            'router_id': spec['router_id'],
            'image': spec.get('image', 'vyos:1.5'),
            'interfaces': spec.get('interfaces', []),
            'vrfs': spec.get('vrfs', []),
            'protocols': spec.get('protocols', {}),
            'services': spec.get('services', {}),
            'qos': spec.get('qos', {}),
            'firewall': spec.get('firewall', {}),
            'traffic_policy': spec.get('traffic_policy', {})
        }
        
        # Update the VyOS router using Ansible
        result = await update_vyos_router(ip_address, router_config)
        
        if result['success']:
            await update_status(name, namespace, "Running", "VyOS router updated successfully",
                               body=body, spec=spec, uid=uid, logger_obj=logger,
                               applied_config=result.get('applied_config'))
            logger.info(f"Successfully updated VyOSRouter {name}")
        else:
            error_msg = result.get('error', 'Unknown update error')
            # All Ansible update failures are treated as transient — VyOS config errors
            # are almost always worth retrying (timing issues, commit locks, etc.).
            # Raising TemporaryError lets kopf reschedule this handler automatically
            # without requiring a new spec-change event to re-trigger it.
            delay = 30 if _is_transient_error(error_msg) else 60
            logger.warning(f"Update error for {name}, will retry in {delay}s: {error_msg}")
            await update_status(name, namespace, "Updating", f"Update pending retry: {error_msg}",
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(f"Update error (will retry): {error_msg}", delay=delay)

    except (kopf.TemporaryError, kopf.PermanentError):
        # Re-raise kopf control exceptions without double-handling
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to update VyOSRouter {name}: {error_msg}")
        delay = 30 if _is_transient_error(error_msg) else 60
        logger.warning(f"Treating as retryable error, will retry in {delay}s: {error_msg}")
        await update_status(name, namespace, "Updating", f"Retrying after error: {error_msg}",
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.TemporaryError(error_msg, delay=delay)

@kopf.on.delete('google.dev', 'v1', 'vyosrouter')
async def delete_vyosrouter(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSRouter deletion using Ansible"""
    logger.info(f"Deleting VyOSRouter: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")
    
    try:
        # Extract interfaces from spec for proper veth cleanup
        interfaces = spec.get('interfaces', [])
        
        # Delete the VyOS router container using Ansible
        result = await delete_vyos_router(ip_address, name, interfaces)
        
        if result['success']:
            logger.info(f"Successfully deleted VyOSRouter {name}")
        else:
            logger.warning(f"Failed to delete VyOS router {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes

    except Exception as e:
        logger.error(f"Error during VyOSRouter deletion {name}: {e}")
        # Don't raise error on delete failure

def _is_transient_error(error_msg: str) -> bool:
    """
    Determine if an error is transient (worth retrying) vs permanent.
    
    Transient errors include:
    - SSH/network connectivity issues to the VM
    - Docker daemon temporarily unavailable
    - Image pull timeouts or retryable failures
    - Container name conflict (previous partial creation - idempotent retry)
    - Ansible connection timeout
    """
    error_lower = error_msg.lower()
    transient_patterns = [
        'ssh',
        'connection refused',
        'connection timed out',
        'connection reset',
        'timeout',
        'unreachable',
        'temporarily unavailable',
        'docker daemon',
        'failed to execute ansible',
        'ansible playbook failed with status: failed',  # Generic Ansible failure - often transient
        'already in use',           # Container name conflict - idempotent retry
        'already exists',           # Container already exists - idempotent retry
        'no such host',
        'network is unreachable',
        'pulling image',
        'pull access denied',       # Docker pull issue - may be transient
        'toomanyrequests',          # Docker Hub rate limit
        'i/o timeout',
        'broken pipe',
        'eof',
        'not ready',                # VyOS container not yet ready (still starting)
        'exit code 124',            # timeout(1) killed vbash commit (slow router)
        'timed out waiting',        # Generic readiness wait timeout
        'timed out',                # Ansible subprocess timeout
    ]
    
    return any(pattern in error_lower for pattern in transient_patterns)


#########################################################################
# Status Management
#########################################################################
async def update_status(name: str, namespace: str, phase: str, message: str, 
                       body: Optional[Dict] = None, spec: Optional[Dict] = None, 
                       uid: Optional[str] = None, logger_obj: Optional[Any] = None,
                       container_id: Optional[str] = None, ip_address: Optional[str] = None,
                       interfaces: Optional[list] = None, protocols_status: Optional[Dict] = None,
                       applied_config: Optional[str] = None):
    """Update the status of a VyOSRouter resource in both Kubernetes and Spanner"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

    status = {
        'phase': phase,
        'message': message
    }

    if container_id:
        status['container_id'] = container_id

    if ip_address:
        status['ip_address'] = ip_address

    if interfaces:
        status['interfaces'] = interfaces

    if protocols_status:
        status['protocols_status'] = protocols_status

    if applied_config:
        status['applied_config'] = applied_config

    # Patch only the status delta — do NOT read-modify-write the full resource.
    # Reading the full resource and patching it back races with kopf's own writes
    # to status.kopf.progress, causing "Patching failed with inconsistencies" warnings
    # and potential idempotency-guard misses.  A merge-patch on the status subresource
    # with only the fields we own leaves status.kopf untouched.
    try:
        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': status},
            content_type='application/merge-patch+json',
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for VyOSRouter {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for VyOSRouter {name}: {e}")
    
    # Sync to Spanner if parameters provided
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            if 'status' not in body_dict or body_dict['status'] is None:
                body_dict['status'] = {}
            else:
                # Make a shallow copy of status to avoid modifying the original body
                body_dict['status'] = dict(body_dict['status'])
            
            body_dict['status'].update(status)
            # Sync to Spanner
            await sync_physical_router(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for VyOSRouter {name}: {e}")
