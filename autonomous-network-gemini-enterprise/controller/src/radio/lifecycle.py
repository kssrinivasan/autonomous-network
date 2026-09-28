import kopf
import logging
import kubernetes
from radio.lifecycle_tasks import create_cellsite, delete_cellsite
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# CellSite Lifecycle Management (UERANSIM gNodeB + UEs)
#########################################################################

@kopf.on.create('google.dev', 'v1', 'cellsite')
async def create_cellsite_handler(body, spec, name, namespace, uid, logger, retry=0, **kwargs):
    """Provision a UERANSIM gNodeB and UEs inside a Device container."""
    logger.info(f"Creating CellSite: {name}")

    current_phase = body.get('status', {}).get('phase', '')
    if current_phase == 'Ready':
        logger.info(f"CellSite {name} already Ready — skipping")
        return
    if current_phase == 'Creating':
        # Ansible is in flight on another invocation. Raise TemporaryError so Kopf
        # does NOT settle this handler — it must retry once the in-flight run finishes.
        raise kopf.TemporaryError(f"CellSite {name} already creating, waiting for Ansible to finish", delay=15)

    networkvm_ip = await get_ip("automation", "networkvm")
    if networkvm_ip is None:
        raise kopf.TemporaryError("No IP address found on Network VM, retrying", delay=10)

    container_name = spec.get('container_name', '')
    if not container_name:
        error_msg = "spec.container_name is required"
        await update_status(name, namespace, "Failed", error_msg,
                            body=body, spec=spec, uid=uid, logger_obj=logger)
        raise kopf.PermanentError(error_msg)

    gnb_spec = spec.get('gnb', {})
    amf_name = gnb_spec.get('amf_name', '')
    upf_name = gnb_spec.get('upf_name', '')
    smf_name = gnb_spec.get('smf_name', '')

    # Wait for Device to be Ready and get its IP
    gnb_ip, device_err = await _get_device_ip(container_name, namespace)
    if device_err:
        raise kopf.TemporaryError(f"Waiting for Device: {device_err}", delay=20)

    # Resolve AMF IP from the referenced NetworkFunction CR
    amf_ip, amf_err = await _get_nf_ip(amf_name, namespace)
    if amf_err:
        raise kopf.TemporaryError(f"Waiting for AMF NetworkFunction: {amf_err}", delay=20)

    # Wait for UPF to be Ready (N3/GTP-U data plane)
    if upf_name:
        _, upf_err = await _get_nf_ip(upf_name, namespace)
        if upf_err:
            raise kopf.TemporaryError(f"Waiting for UPF NetworkFunction: {upf_err}", delay=20)

    # Wait for SMF to be Ready (session management)
    if smf_name:
        _, smf_err = await _get_nf_ip(smf_name, namespace)
        if smf_err:
            raise kopf.TemporaryError(f"Waiting for SMF NetworkFunction: {smf_err}", delay=20)

    # Set status to 'Creating' BEFORE calling Ansible to establish the lock
    try:
        await update_status(name, namespace, "Creating",
                            f"Starting gNodeB in {container_name}, connecting to AMF at {amf_ip}",
                            body=body, spec=spec, uid=uid, logger_obj=logger)

        result = await create_cellsite(networkvm_ip, name, spec, gnb_ip, amf_ip)

        if result['success']:
            ue_count = len(spec.get('ues', []))
            await update_status(
                name, namespace, "Ready",
                f"gNodeB running in {container_name}, {ue_count} UE(s) provisioned",
                body=body, spec=spec, uid=uid, logger_obj=logger
            )
            logger.info(f"CellSite {name} is Ready")
        else:
            error_msg = result.get('error', 'Unknown error')
            if _is_transient_error(error_msg):
                await update_status(name, namespace, "Pending", f"Retrying: {error_msg}",
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
        logger.error(f"Unexpected error creating CellSite {name}: {error_msg}")
        if _is_transient_error(error_msg):
            await update_status(name, namespace, "Pending", f"Retrying: {error_msg}",
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(error_msg, delay=30)
        else:
            await update_status(name, namespace, "Failed", error_msg,
                                body=body, spec=spec, uid=uid, logger_obj=logger)
            raise


@kopf.on.resume('google.dev', 'v1', 'cellsite')
async def resume_cellsite_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """On operator restart/reconcile, skip CellSites that are already running."""
    phase = body.get('status', {}).get('phase', '')
    if phase in ('Ready', 'Failed'):
        logger.info(f"CellSite {name} already {phase} on resume — skipping")
        return
    if phase == 'Creating':
        logger.warning(f"CellSite {name} stuck in 'Creating' on resume — resetting to Pending")
        await update_status(name, namespace, "Pending", "Reset from stale Creating state on operator resume",
                            body=body, spec=spec, uid=uid, logger_obj=logger)
        return
    logger.info(f"CellSite {name} in phase '{phase}' on resume — will retry via create handler")
    raise kopf.TemporaryError(f"CellSite {name} needs attention (phase={phase!r})", delay=30)


@kopf.on.delete('google.dev', 'v1', 'cellsite')
async def delete_cellsite_handler(body, spec, name, namespace, logger, **kwargs):
    """Stop gNodeB and UE processes in the Device container."""
    logger.info(f"Deleting CellSite: {name}")

    networkvm_ip = await get_ip("automation", "networkvm")
    if networkvm_ip is None:
        raise kopf.TemporaryError("No IP address found on Network VM, retrying", delay=10)

    try:
        result = await delete_cellsite(networkvm_ip, name, spec)
        if result['success']:
            logger.info(f"Successfully deleted CellSite {name}")
        else:
            logger.warning(f"Delete returned failure for {name}: {result.get('error')}")
    except Exception as e:
        logger.error(f"Error during CellSite deletion {name}: {e}")


#########################################################################
# Helpers
#########################################################################

async def _get_device_ip(container_name: str, namespace: str):
    """Return (ip_address, None) if Device is Ready, or (None, error_msg)."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')
    try:
        device = api.get(name=container_name, namespace=namespace)
        d = device.to_dict()
        status = d.get('status', {})
        phase = status.get('phase', 'Unknown')
        if phase != 'Ready':
            return None, f"Device '{container_name}' phase is '{phase}'"
        ip = status.get('ip_address') or d.get('spec', {}).get('ip_address', '')
        if not ip:
            return None, f"Device '{container_name}' has no ip_address"
        return ip, None
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return None, f"Device '{container_name}' not found"
        return None, f"Error querying Device '{container_name}': {e}"
    except Exception as e:
        return None, str(e)


async def _get_nf_ip(nf_name: str, namespace: str):
    """Return (ip_address, None) if NetworkFunction is Ready, or (None, error_msg)."""
    if not nf_name:
        return None, "amf_name not specified in gnb spec"
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='NetworkFunction')
    try:
        nf = api.get(name=nf_name, namespace=namespace)
        d = nf.to_dict()
        status = d.get('status', {})
        phase = status.get('phase', 'Unknown')
        if phase != 'Ready':
            return None, f"NetworkFunction '{nf_name}' phase is '{phase}'"
        ip = status.get('ip_address', '')
        if not ip:
            return None, f"NetworkFunction '{nf_name}' has no ip_address"
        return ip, None
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return None, f"NetworkFunction '{nf_name}' not found"
        return None, f"Error querying NetworkFunction '{nf_name}': {e}"
    except Exception as e:
        return None, str(e)


def _is_transient_error(error_msg: str) -> bool:
    transient_patterns = [
        'ssh', 'connection refused', 'connection timed out', 'connection reset',
        'timeout', 'unreachable', 'temporarily unavailable', 'docker daemon',
        'failed to execute ansible', 'ansible playbook failed with status: failed',
        'already in use', 'already exists', 'no such host', 'network is unreachable',
        'pulling image', 'pull access denied', 'toomanyrequests', 'i/o timeout',
        'broken pipe', 'eof', 'not ready', 'exit code 124', 'timed out waiting',
        'timed out',
    ]
    return any(p in error_msg.lower() for p in transient_patterns)


#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str,
                        body: dict = None, spec: dict = None, uid: str = None, logger_obj = None):
    """Update CellSite status in K8s and sync to Spanner.

    Args:
        name, namespace, phase, message: Status fields to update
        body, spec, uid, logger_obj: If provided, sync to Spanner after K8s update
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='CellSite')

    status_update = {'phase': phase, 'message': message}

    try:
        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': status_update},
            content_type='application/merge-patch+json',
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for CellSite {name}")
        else:
            logger.error(f"Failed to update status for CellSite {name}: {e}")

    # Sync to Spanner if parameters provided (following transport pattern)
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Import here to avoid circular dependency
            from graph.radio_tasks import sync_cellsite

            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            if 'status' not in body_dict or body_dict['status'] is None:
                body_dict['status'] = {}
            else:
                body_dict['status'] = dict(body_dict['status'])

            body_dict['status'].update(status_update)
            # Sync to Spanner
            await sync_cellsite(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for CellSite {name}: {e}")
