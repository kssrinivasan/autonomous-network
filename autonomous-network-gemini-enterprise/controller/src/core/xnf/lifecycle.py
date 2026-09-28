import kopf
import logging
import kubernetes
from core.xnf.lifecycle_tasks import create_nf, delete_nf, VALID_NF_TYPES
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# NetworkFunction Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'networkfunction')
async def create_nf_handler(body, spec, name, namespace, uid, logger, retry=0, **kwargs):
    """Handle NetworkFunction creation — renders config and starts the NF binary in a Device container."""
    import time
    handler_start = time.time()
    logger.info(f"[HANDLER-START] Creating NetworkFunction: {name} at {handler_start}, retry={retry}")

    # Idempotency guard: skip if already terminal or in-progress.
    # 'Creating' catches concurrent handler re-invocations (e.g. from watch
    # stream reconnection while the first Ansible run is still in flight).
    current_phase = body.get('status', {}).get('phase', '')
    if current_phase in ('Ready', 'Creating'):
        logger.info(f"NetworkFunction {name} phase='{current_phase}' — skipping")
        return

    networkvm_ip = await get_ip("automation", "networkvm")
    if networkvm_ip is None:
        raise kopf.TemporaryError("No IP address found on Network VM, retrying", delay=10)

    nf_type = spec.get('nf_type', '').lower()
    if nf_type not in VALID_NF_TYPES:
        error_msg = f"Invalid nf_type '{nf_type}'. Must be one of: {sorted(VALID_NF_TYPES)}"
        await update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    container_name = spec.get('container_name', '')
    if not container_name:
        error_msg = "spec.container_name is required"
        await update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    # Wait for Device CR to be Ready and retrieve its IP and extra_interfaces
    device_ip, device_extra_ifaces, device_err = await _get_device_ip(container_name, namespace)
    if device_err:
        raise kopf.TemporaryError(f"Device not ready: {device_err}", delay=20)

    # Set status to 'Creating' BEFORE calling Ansible to establish the lock
    try:
        await update_status(name, namespace, "Creating", f"Starting {nf_type} in {container_name}",
                            body=body, spec=spec, uid=uid, logger_obj=logger)

        result = await create_nf(networkvm_ip, name, spec, device_ip, device_extra_ifaces)
        logger.info(f"[HANDLER-ANSIBLE-COMPLETE] {name} ansible returned at {time.time()}, success={result['success']}")

        if result['success']:
            # Strip CIDR suffix — Device.spec.ip_address may be in "x.x.x.x/yy" form
            # but the NF CRD status schema requires a bare IPv4 address.
            device_ip_bare = device_ip.split('/')[0] if '/' in device_ip else device_ip
            await update_status(
                name, namespace, "Ready",
                f"{nf_type} running in {container_name}",
                ip_address=device_ip_bare,
                body=body, spec=spec, uid=uid, logger_obj=logger
            )
            logger.info(f"NetworkFunction {name} ({nf_type}) is Ready")
        else:
            error_msg = result.get('error', 'Unknown error')
            if _is_transient_error(error_msg):
                await update_status(name, namespace, "Pending", error_msg,
                                    body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.TemporaryError(error_msg, delay=30)
            else:
                await update_status(name, namespace, "Failed", error_msg,
                                    body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.PermanentError(error_msg)

    except (kopf.TemporaryError, kopf.PermanentError):
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error creating NetworkFunction {name}: {error_msg}")
        if _is_transient_error(error_msg):
            await update_status(name, namespace, "Pending", error_msg,
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(error_msg, delay=30)
        else:
            await update_status(name, namespace, "Failed", error_msg,
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise


@kopf.on.resume('google.dev', 'v1', 'networkfunction')
async def resume_nf_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """On operator restart/reconcile, skip NFs that are already running or in progress."""
    import time
    logger.info(f"[RESUME-HANDLER] NetworkFunction {name} at {time.time()}")
    phase = body.get('status', {}).get('phase', '')
    if phase in ('Ready', 'Failed'):
        logger.info(f"NetworkFunction {name} in phase '{phase}' on resume — skipping")
        return
    if phase == 'Creating':
        # Operator restarted mid-Ansible run; reset to Pending so the create handler retries
        logger.warning(f"NetworkFunction {name} stuck in 'Creating' on resume — resetting to Pending")
        await update_status(name, namespace, "Pending", "Reset from stale Creating state on operator resume",
                            body=body, spec=spec, uid=uid, logger_obj=logger)
    # Only intervene for Pending or unknown states
    logger.info(f"NetworkFunction {name} in phase '{phase}' on resume — will retry via create handler")
    raise kopf.TemporaryError(f"NetworkFunction {name} needs attention (phase={phase!r})", delay=30)


@kopf.on.delete('google.dev', 'v1', 'networkfunction')
async def delete_nf_handler(body, spec, name, namespace, logger, **kwargs):
    """Handle NetworkFunction deletion — stops the NF binary inside the Device container."""
    logger.info(f"Deleting NetworkFunction: {name}")

    container_name = spec.get('container_name', '')
    if container_name:
        _, _, device_err = await _get_device_ip(container_name, namespace)
        if device_err and 'not found' in device_err:
            logger.info(f"Device '{container_name}' already gone — skipping NF teardown for {name}")
            return

    networkvm_ip = await get_ip("automation", "networkvm")
    if networkvm_ip is None:
        raise kopf.TemporaryError("No IP address found on Network VM, retrying", delay=10)

    try:
        result = await delete_nf(networkvm_ip, name, spec)
        if result['success']:
            logger.info(f"Successfully deleted NetworkFunction {name}")
        else:
            logger.warning(f"Delete returned failure for {name}: {result.get('error')}")

    except Exception as e:
        logger.error(f"Error during NetworkFunction deletion {name}: {e}")


#########################################################################
# Helpers
#########################################################################

async def _get_device_ip(container_name: str, namespace: str):
    """Return (ip_address, extra_interfaces, None) if Device is Ready, or (None, [], error_msg)."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')
    try:
        device = api.get(name=container_name, namespace=namespace)
        d = device.to_dict()
        status = d.get('status', {})
        phase = status.get('phase', 'Unknown')
        if phase != 'Ready':
            return None, [], f"Device '{container_name}' phase is '{phase}'"
        spec = d.get('spec', {})
        ip = spec.get('ip_address', '')
        if not ip:
            return None, [], f"Device '{container_name}' has no ip_address"
        extra_interfaces = spec.get('extra_interfaces', [])
        return ip, extra_interfaces, None
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return None, [], f"Device '{container_name}' not found"
        return None, [], f"Error querying Device '{container_name}': {e}"
    except Exception as e:
        return None, [], str(e)


def _is_transient_error(error_msg: str) -> bool:
    transient_patterns = [
        'ssh', 'connection refused', 'connection timed out', 'connection reset',
        'timeout', 'unreachable', 'temporarily unavailable', 'docker daemon',
        'failed to execute ansible', 'ansible playbook failed with status: failed',
        'already in use', 'already exists', 'no such host', 'network is unreachable',
        'pulling image', 'pull access denied', 'toomanyrequests', 'i/o timeout',
        'broken pipe', 'eof', 'not ready', 'exit code 124', 'timed out waiting',
        'timed out',
        # Docker/filesystem transients from concurrent playbook execution
        'non-zero return code', 'is absent, cannot continue',
    ]
    return any(p in error_msg.lower() for p in transient_patterns)


#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, ip_address: str = None,
                        body: dict = None, spec: dict = None, uid: str = None, logger_obj = None):
    """Update NetworkFunction status in K8s and sync to Spanner.

    Args:
        name, namespace, phase, message: Status fields to update
        ip_address: Optional IP address to include in status
        body, spec, uid, logger_obj: If provided, sync to Spanner after K8s update
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='NetworkFunction')

    status = {'phase': phase, 'message': message}
    if ip_address:
        status['ip_address'] = ip_address

    try:
        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': status},
            content_type='application/merge-patch+json',
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for NetworkFunction {name}")
        else:
            logger.error(f"Failed to update status for NetworkFunction {name}: {e}")

    # Sync to Spanner if parameters provided (following transport pattern)
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Import here to avoid circular dependency
            from graph.core_tasks import sync_network_function

            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            if 'status' not in body_dict or body_dict['status'] is None:
                body_dict['status'] = {}
            else:
                body_dict['status'] = dict(body_dict['status'])

            body_dict['status'].update(status)
            # Sync to Spanner
            await sync_network_function(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for NetworkFunction {name}: {e}")
