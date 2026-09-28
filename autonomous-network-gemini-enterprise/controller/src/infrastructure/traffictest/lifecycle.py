import kopf
import logging
import kubernetes
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from infrastructure.traffictest.lifecycle_tasks import (
    create_traffic_test,
    delete_traffic_test,
)
from utils.compute import get_ip
from utils.ssh_http import ssh_exec
from infrastructure.traffictest.port_allocator import PortAllocator

logger = logging.getLogger(__name__)

# Global PortAllocator instance for the operator
port_allocator = PortAllocator()

#########################################################################
# Operator Startup Synchronization
#########################################################################

async def initial_setup(logger):
    """Scan existing TrafficTest resources and re-populate PortAllocator.

    This is an anti-entropy mechanism when the network operator is redeployed
    and we need to re-align the port allocator with the currently running
    traffic tests.

    In addition to port re-allocation, this pass reconciles any TrafficTest
    whose ``status.phase`` is missing (empty status dict) — a situation that
    arises when the operator was restarted before it could write the initial
    status, or when tests were created against an older operator version.

    Reconciliation heuristics (applied only when ``status.phase`` is absent):

    * **Has ``allocated_ports``** → the create handler ran far enough to assign
      ports and (presumably) start the agents.  We infer the phase from
      ``metadata.creationTimestamp + spec.duration``:

      - ``now  < creation + duration`` → **Running**
      - ``now >= creation + duration`` → **Completed**

    * **No ``allocated_ports`` and no ``phase``** → the create handler never
      finished (operator was down).  Kopf will re-trigger ``on.create`` for
      any resource it has not yet annotated; for resources Kopf considers
      "done" (its handler annotation is present) we mark **Failed** so the
      user has clear feedback.
    """
    logger.info("Operator starting up — synchronizing PortAllocator and reconciling TrafficTest phases")

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='TrafficTest')

    try:
        resources = api.get()
        now = datetime.now(timezone.utc)

        port_count = 0
        reconcile_count = 0

        for tt in resources.items:
            tt_dict = tt.to_dict()
            name      = tt_dict['metadata']['name']
            namespace = tt_dict['metadata'].get('namespace', 'default')
            spec      = tt_dict.get('spec',   {}) or {}
            status    = tt_dict.get('status', {}) or {}
            meta      = tt_dict.get('metadata', {})

            # ── 1. Re-populate PortAllocator ─────────────────────────────────
            ports = status.get('allocated_ports')
            if ports:
                port_allocator.mark_busy(ports)
                logger.info(f"Re-allocated ports {ports} for TrafficTest {name}")
                port_count += 1

            # ── 2. Reconcile missing phase ────────────────────────────────────
            if status.get('phase'):
                # Phase already set — nothing to reconcile.
                continue

            logger.info(f"TrafficTest {name} has no status.phase — reconciling")

            if ports:
                # Ports were allocated; infer phase from age vs duration.
                duration_sec = int(spec.get('duration', 60))
                source_count = len(spec.get('source_devices', []))

                creation_str = meta.get('creationTimestamp')  # ISO-8601 UTC
                try:
                    creation_ts = datetime.fromisoformat(
                        creation_str.replace('Z', '+00:00')
                    )
                except Exception:
                    creation_ts = now  # fallback: assume just created

                expected_end = creation_ts + timedelta(seconds=duration_sec)

                if now < expected_end:
                    # Agents should still be running.
                    remaining = int((expected_end - now).total_seconds())
                    phase   = "Running"
                    message = (
                        f"Reconciled on operator restart — estimated {remaining}s remaining"
                    )
                    additional = {
                        'start_time':  creation_ts.isoformat(),
                        'source_count': source_count,
                        'allocated_ports': ports,
                    }
                    logger.info(
                        f"TrafficTest {name}: marking Running (~{remaining}s left)"
                    )
                    # Re-arm the completion marker so we still transition to Completed.
                    asyncio.create_task(
                        mark_completed_after(name, namespace, remaining)
                    )
                else:
                    # Duration already elapsed — test is done.
                    phase   = "Completed"
                    message = "Reconciled on operator restart — duration had already elapsed"
                    additional = {
                        'start_time': creation_ts.isoformat(),
                        'end_time':   expected_end.isoformat(),
                        'source_count': source_count,
                        'allocated_ports': ports,
                    }
                    # Ports no longer needed — free them so the space is re-usable.
                    port_allocator.free(ports)
                    logger.info(f"TrafficTest {name}: marking Completed (duration elapsed)")

                # Extract body, spec, uid for Spanner sync
                uid = meta.get('uid', '')
                await update_status(name, namespace, phase, message,
                                    additional_data=additional,
                                    body=tt_dict, spec=spec, uid=uid, logger_obj=logger)
                reconcile_count += 1

            else:
                # No ports, no phase.  The create handler never ran (or failed
                # before port allocation).  Check whether Kopf has annotated this
                # resource; if so it considers the handler "done" and will not
                # retry — mark Failed so the user gets clear feedback.
                annotations = meta.get('annotations', {}) or {}
                kopf_handled = any(
                    k.startswith('kopf.zalando.org/') for k in annotations
                )
                if kopf_handled:
                    # Extract body, spec, uid for Spanner sync
                    uid = meta.get('uid', '')
                    await update_status(
                        name, namespace, "Failed",
                        "Operator restarted before this TrafficTest could be provisioned — "
                        "please delete and re-create the resource to retry",
                        body=tt_dict, spec=spec, uid=uid, logger_obj=logger
                    )
                    logger.warning(
                        f"TrafficTest {name}: marked Failed (no ports, kopf-annotated, operator restart)"
                    )
                    reconcile_count += 1
                else:
                    # Kopf will re-trigger on.create automatically.
                    logger.info(
                        f"TrafficTest {name}: no ports and no kopf annotation — "
                        "Kopf will re-trigger on.create"
                    )

        logger.info(
            f"Startup sync complete: {port_count} port block(s) re-marked, "
            f"{reconcile_count} TrafficTest phase(s) reconciled"
        )

    except Exception as e:
        logger.error(f"Failed to synchronize PortAllocator / reconcile phases: {e}")

#########################################################################
# TrafficTest Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'traffictest')
async def create_traffic_test_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle TrafficTest creation using Ansible"""
    logger.info(f"Creating TrafficTest: {name} in namespace: {namespace}")
    logger.info(f"spec: {spec}")
    
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        # Check that the referenced VyOSL3VPN is Ready before proceeding (optional).
        vpn_ref = spec.get('vpnRef')
        if vpn_ref:
            if not await check_vpn_ready(vpn_ref, namespace):
                await update_status(name, namespace, "Pending",
                                    f"Waiting for VyOSL3VPN '{vpn_ref}' to be Ready",
                                    body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.TemporaryError(
                    f"Waiting for VyOSL3VPN '{vpn_ref}' to be Ready", delay=30)

        # ue_imsi requires a cellSiteRef so the operator can gate on CellSite Ready.
        ue_imsi = spec.get('ue_imsi')
        cell_site_ref = spec.get('cellSiteRef')
        if ue_imsi and not cell_site_ref:
            error_msg = "cellSiteRef is required when ue_imsi is specified"
            await update_status(name, namespace, "Failed", error_msg,
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.PermanentError(error_msg)

        # Check that the referenced CellSite is Ready before proceeding (optional).
        if cell_site_ref:
            if not await check_cellsite_ready(cell_site_ref, namespace):
                await update_status(name, namespace, "Pending",
                                    f"Waiting for CellSite '{cell_site_ref}' to be Ready",
                                    body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.TemporaryError(
                    f"Waiting for CellSite '{cell_site_ref}' to be Ready", delay=30)

        # Validate required Devices exist and are ready
        source_devices = spec.get('source_devices', [])
        destination_device = spec.get('destination_device')
        
        if not source_devices or not destination_device:
            error_msg = "Both source_devices (array) and destination_device are required"
            logger.error(f"TrafficTest {name}: {error_msg}")
            await update_status(name, namespace, "Failed", error_msg,
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.PermanentError(error_msg)
        
        # Port assignment using global PortAllocator.
        # Bidirectional tests need 2 ports per source (one forward, one reverse).
        num_sources = len(source_devices)
        bidirectional = bool(spec.get('bidirectional', False))
        ports_needed = num_sources * 2 if bidirectional else num_sources
        port_mode = spec.get('port_mode', 'auto')
        
        if port_mode == 'manual':
            # Skip validation already done
            requested_port = spec.get('port')
            allocated_ports = port_allocator.alloc(ports_needed, requested_port)
            if not allocated_ports:
                error_msg = f"Manual port {requested_port} is already in use"
                logger.error(f"TrafficTest {name}: {error_msg}")
                await update_status(name, namespace, "Failed", error_msg)
                raise kopf.PermanentError(error_msg)
            else:
                logger.info(f"Using manual port {allocated_ports[0]} from spec")
        else:
            # Automatic allocation
            allocated_ports = port_allocator.alloc(ports_needed)

        if allocated_ports is None:
            raise kopf.PermanentError("No free port blocks available for TrafficTest")
        
        # Consistent integer for single-port status/ansible
        logger.info(f"Allocated ports {allocated_ports} for {num_sources} sources)")

        all_sources_ready = True
        not_ready_devices = []
        devices_info = {}
        
        # Check if all source devices are ready
        for index, source_device in enumerate(source_devices):
            ready, device_ip, device_mgmt_ip = await check_device_ready(source_device, namespace)
            if not ready:
                all_sources_ready = False
                not_ready_devices.append(source_device)

            devices_info[source_device] = {
                'ready':   ready,
                'ip':      device_ip,
                'mgmt_ip': device_mgmt_ip,
                'port':    allocated_ports[index],
            }
            # For bidirectional tests the second half of allocated_ports are the
            # reverse-direction ports (destination→source), one per source device.
            if bidirectional:
                devices_info[source_device]['reverse_port'] = allocated_ports[num_sources + index]

        # Check if destination device is ready
        dest_ready, dest_ip, dest_mgmt_ip = await check_device_ready(destination_device, namespace)
        devices_info[destination_device] = {
            'ready':   dest_ready,
            'ip':      dest_ip,
            'mgmt_ip': dest_mgmt_ip,
        }
        if not all_sources_ready or not dest_ready:
            not_ready_list = not_ready_devices + ([destination_device] if not dest_ready else [])
            error_msg = f"Waiting for Devices to be ready: {', '.join(not_ready_list)}"
            logger.warning(f"TrafficTest {name}: {error_msg}")
            await update_status(name, namespace, "Pending", error_msg,
                              additional_data={'source_count': len(source_devices)},
                              body=body, spec=spec, uid=uid, logger_obj=logger)

            # Raise temporary error to retry later
            raise kopf.TemporaryError(error_msg, delay=30)

        # Update status to indicate creation has started
        await update_status(name, namespace, "Deploying",
                          f"Starting traffic test deployment for {len(source_devices)} source(s)",
                          additional_data={'source_count': len(source_devices)},
                          body=body, spec=spec, uid=uid, logger_obj=logger)

        # Create the TrafficTest using Ansible
        result = await create_traffic_test(name, ip_address, spec, devices_info)

        if result['success']:
            # Initialize per-source status
            source_statuses = {}
            for source_device in source_devices:
                source_statuses[source_device] = {
                    'phase': 'Running',
                    'message': 'Traffic generator started',
                }
            # Update status to running
            await update_status(
                name, namespace, "Running",
                f"Traffic test started successfully with {len(source_devices)} source(s) at {result.get('start_time', 'unknown time')}",
                additional_data={
                    'start_time': result.get('start_time'),
                    'source_count': len(source_devices),
                    'source_status': source_statuses,
                    'allocated_ports': allocated_ports
                },
                body=body, spec=spec, uid=uid, logger_obj=logger
            )
            logger.info(f"Successfully started TrafficTest {name} with {len(source_devices)} source device(s)")

            # Start background task to mark Completed after duration elapses
            asyncio.create_task(
                mark_completed_after(name, namespace, spec.get('duration', 60))
            )

        else:
            await update_status(name, namespace, "Failed", f"Failed to start traffic test: {result['error']}",
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            # Free ports on failure
            port_allocator.free(allocated_ports)
            raise kopf.PermanentError(f"TrafficTest creation failed: {result['error']}")

    except kopf.TemporaryError:
        # Re-raise temporary errors for retry
        raise
    except kopf.PermanentError:
        # Re-raise permanent errors
        raise
    except Exception as e:
        logger.error(f"Failed to create TrafficTest {name}: {e}")
        await update_status(name, namespace, "Failed", str(e),
                            body=body, spec=spec, uid=uid, logger_obj=logger)
        # Free port on unexpected error if it was allocated
        if 'allocated_ports' in locals() and allocated_ports:
            port_allocator.free(allocated_ports)
        raise kopf.PermanentError(str(e))

@kopf.on.delete('google.dev', 'v1', 'traffictest')
async def delete_traffic_test_handler(body, spec, name, namespace, logger, **kwargs):
    """Handle TrafficTest deletion using Ansible"""
    logger.info(f"Deleting TrafficTest: {name} in namespace: {namespace}")

    #try:
    # Get Network VM IP address
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        logger.warning("No IP address found on Network VM, skipping traffic test deletion")
        return
            
    status = body.get('status', {})

    # Validate required Devices exist and are ready
    source_devices = spec.get('source_devices', [])
    destination_device = spec.get('destination_device')
    allocated_ports = status.get('allocated_ports')
    devices_info = {}

    if allocated_ports and source_devices:
        # Look up mgmt_ip for destination
        try:
            _, dest_ip, dest_mgmt_ip = await check_device_ready(destination_device, namespace)
        except Exception:
            dest_ip, dest_mgmt_ip = None, None
        devices_info[destination_device] = {'ip': dest_ip, 'mgmt_ip': dest_mgmt_ip}

        # Add source device infos with mgmt_ip
        num_sources = len(source_devices)
        bidirectional = bool(spec.get('bidirectional', False))
        for index, source_device in enumerate(source_devices):
            if index < len(allocated_ports):
                try:
                    _, _, src_mgmt_ip = await check_device_ready(source_device, namespace)
                except Exception:
                    src_mgmt_ip = None
                devices_info[source_device] = {
                    'port':    allocated_ports[index],
                    'mgmt_ip': src_mgmt_ip,
                }
                if bidirectional and (num_sources + index) < len(allocated_ports):
                    devices_info[source_device]['reverse_port'] = allocated_ports[num_sources + index]

        result = await delete_traffic_test(name, ip_address, spec, devices_info)

        if result['success']:
            # Free the ports in the internal allocator 
            port_allocator.free(allocated_ports)
            logger.info(f"Freed ports {allocated_ports}")
            logger.info(f"Successfully deleted TrafficTest {name}")
        else:
            logger.warning(f"Failed to delete TrafficTest {name}: {result['error']}")
    else:
        logger.info(f"No allocated ports/sources found for TrafficTest {name}, skipping remote deletion")
        # Don't raise error on delete failure - resource should still be removed from Kubernetes
        
    # except Exception as e:
    #     logger.error(f"Error during TrafficTest deletion {name}: {e}")
    #     logger.error(f"{e.backtrace.join("\n")}")
        # Don't raise error on delete failure

#########################################################################
# Background Completion Marker
#########################################################################

async def mark_completed_after(name: str, namespace: str, duration: int):
    """Wait for the test duration to elapse, then mark the TrafficTest Completed.

    Traffic agents self-terminate after duration_sec; actual metrics are
    collected by the Ops Agent and written to Cloud Monitoring — no polling
    of the agents is needed here.
    """
    logger.info(f"TrafficTest {name}: will mark Completed in {duration}s")
    await asyncio.sleep(duration)
    logger.info(f"TrafficTest {name}: duration elapsed, marking Completed")

    # Fetch the CR to get body, spec, uid for Spanner sync
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        api = client.resources.get(api_version='google.dev/v1', kind='TrafficTest')
        obj = api.get(name=name, namespace=namespace)
        body = obj.to_dict()
        spec = body.get('spec', {})
        uid = body.get('metadata', {}).get('uid', '')

        await update_status(
            name, namespace, "Completed",
            f"Traffic test completed after {duration}s",
            additional_data={'end_time': datetime.now(timezone.utc).isoformat()},
            body=body, spec=spec, uid=uid, logger_obj=logger
        )
    except Exception as e:
        logger.error(f"Failed to mark TrafficTest {name} as Completed: {e}")
        # Fall back to update without sync
        await update_status(
            name, namespace, "Completed",
            f"Traffic test completed after {duration}s",
            additional_data={'end_time': datetime.now(timezone.utc).isoformat()}
        )

#########################################################################
# VPN Validation
#########################################################################

async def check_cellsite_ready(cellsite_name: str, namespace: str) -> bool:
    """Return True if the CellSite is Ready, False if not yet Ready.

    Raises:
        kopf.PermanentError: if the CellSite resource is not found (404).
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='CellSite')

    try:
        obj = api.get(name=cellsite_name, namespace=namespace)
        phase = obj.to_dict().get('status', {}).get('phase', 'Unknown')
        if phase == 'Ready':
            logger.info(f"CellSite '{cellsite_name}' is Ready")
            return True
        logger.info(f"Waiting for CellSite '{cellsite_name}' to be Ready (current: {phase})")
        return False
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            raise kopf.PermanentError(f"CellSite '{cellsite_name}' not found")
        logger.error(f"Error checking CellSite '{cellsite_name}': {e}")
        raise


async def check_vpn_ready(vpn_name: str, namespace: str) -> bool:
    """
    Check if the specified VyOSL3VPN exists and has status 'Ready'.

    Args:
        vpn_name:  Name of the VyOSL3VPN resource
        namespace: Kubernetes namespace

    Returns:
        True if the VPN is Ready, False if it exists but is not yet Ready.

    Raises:
        kopf.PermanentError: if the VPN resource is not found (404).
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSL3VPN')

    try:
        vpn = api.get(name=vpn_name, namespace=namespace)
        vpn_dict = vpn.to_dict()
        phase = vpn_dict.get('status', {}).get('phase', 'Unknown')

        if phase == 'Ready':
            logger.info(f"VyOSL3VPN '{vpn_name}' is Ready")
            return True
        else:
            logger.info(f"Waiting for VyOSL3VPN '{vpn_name}' to be Ready (current: {phase})")
            return False

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            raise kopf.PermanentError(f"VyOSL3VPN '{vpn_name}' not found")
        else:
            logger.error(f"Error checking VyOSL3VPN '{vpn_name}': {e}")
            raise

#########################################################################
# Device Validation
#########################################################################

async def check_device_ready(device_name: str, namespace: str):
    """
    Check if the specified Device exists and has status 'Ready'.

    Args:
        device_name: Name of the Device resource
        namespace:   Kubernetes namespace

    Returns:
        Tuple (ready: bool, data_ip: str|None, mgmt_ip: str|None)
    """
    if not device_name:
        logger.warning("No device_name provided for Device check")
        return False, None, None

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')
    
    try:
        device = api.get(name=device_name, namespace=namespace)
        device_dict = device.to_dict()
        
        status = device_dict.get('status', {})
        phase = status.get('phase', 'Unknown')
        
        if phase != 'Ready':
            logger.warning(f"Device '{device_name}' is in phase '{phase}', not Ready")
            return False, None, None
        else:
            logger.info(f"Device '{device_name}' is Ready")
            data_ip  = device_dict.get('spec',   {}).get('ip_address')
            mgmt_ip  = device_dict.get('status', {}).get('mgmt_ip')
            return True, data_ip, mgmt_ip

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning(f"Device '{device_name}' not found")
            return False, None, None
        else:
            logger.error(f"Error checking Device '{device_name}': {e}")
            return False, None, None
    except Exception as e:
        logger.error(f"Unexpected error checking Device '{device_name}': {e}")
        return False, None, None

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str,
                        additional_data: dict = None, allocated_ports: Optional[list] = None,
                        body: dict = None, spec: dict = None, uid: str = None, logger_obj = None):
    """Update TrafficTest status in K8s and sync to Spanner.

    Args:
        name, namespace, phase, message: Status fields to update
        additional_data: Additional status fields to merge
        allocated_ports: Port allocation to include in status
        body, spec, uid, logger_obj: If provided, sync to Spanner after K8s update
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='TrafficTest')

    try:
        resource = api.get(name=name, namespace=namespace)
        resource_dict = resource.to_dict()

        if 'status' not in resource_dict:
            resource_dict['status'] = {}

        status = {
            'phase': phase,
            'message': message
        }
        
        # Add timestamps
        if phase == "Running" and 'start_time' not in resource_dict['status']:
            status['start_time'] = datetime.now(timezone.utc).isoformat()
        elif phase in ["Completed", "Failed", "Stopped"]:
            status['end_time'] = datetime.now(timezone.utc).isoformat()
        
        # Add additional data if provided
        if additional_data:
            status.update(additional_data)
        
        # Add explicitly passed allocated_ports to status
        if allocated_ports:
            status['allocated_ports'] = allocated_ports
            
        resource_dict['status'].update(status)

        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': resource_dict['status']},
            content_type='application/merge-patch+json',
        )
        
        logger.debug(f"Updated status for TrafficTest {name}: {phase} - {message}")
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug(f"TrafficTest {name} not found (likely deleted), skipping status update.")
        elif e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for TrafficTest {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for TrafficTest {name}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status for TrafficTest {name}: {e}")

    # Sync to Spanner if parameters provided (following transport pattern)
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Import here to avoid circular dependency
            from graph.service_tasks import sync_traffic_flow

            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            if 'status' not in body_dict or body_dict['status'] is None:
                body_dict['status'] = {}
            else:
                body_dict['status'] = dict(body_dict['status'])

            body_dict['status'].update(status)
            # Sync to Spanner
            await sync_traffic_flow(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for TrafficTest {name}: {e}")
