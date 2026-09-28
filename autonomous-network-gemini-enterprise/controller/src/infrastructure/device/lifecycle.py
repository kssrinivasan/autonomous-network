import kopf
import logging
import kubernetes
from infrastructure.device.lifecycle_tasks import (
    create_device,
    delete_device,
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# Device Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'device')
async def create_device_handler(body, spec, name, namespace, uid, logger, retry=0, **kwargs):
    """Handle Device creation using Ansible"""
    logger.info(f"Creating Device: {name} in namespace: {namespace}")
    logger.info(f"spec {spec}")

    # Initialize status immediately on first create to prevent race condition with graph sync.
    # The graph sync handler fires on @kopf.on.create and defaults to "Unknown" if status
    # is missing. Setting "Pending" here ensures the device never appears as "Unknown".
    current_phase = body.get('status', {}).get('phase', '')
    if not current_phase:
        await update_status(name, namespace, "Pending", "Device created, awaiting provisioning")
        logger.info(f"Device {name} status initialized to Pending")

    # Idempotency guard: if the device is already Ready (e.g. kopf retries after a
    # progress-annotation race), skip the Ansible playbook entirely to avoid
    # transiently flipping the status back to Creating/Failed.
    if current_phase == 'Ready':
        logger.info(f"Device {name} is already Ready — skipping creation playbook (idempotent retry)")
        return

    networkvm_ip_address = await get_ip("automation", "networkvm")
    if networkvm_ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {networkvm_ip_address}")

    # Validate device name length (permanent — check before touching status)
    if len(name) > 8:
        error_msg = "Device name must be 8 characters or fewer"
        logger.error(f"Device {name} creation failed: {error_msg}")
        await update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    # unique within namespace check
    # TODO

    # Check if the required LinuxNetwork CR is ready.
    # Do NOT set status to "Creating" yet — that would create spurious
    # Creating → Pending → Creating churn on every retry while waiting.
    # Status stays at whatever it was on previous attempts (Pending / Creating).
    isready = await check_linux_network_ready(spec.get('network_name', ''), namespace)
    if not isready:
        error_msg = f"Waiting for LinuxNetwork to be ready"
        logger.warning(f"Device {name}: {error_msg}")
        # Only flip to Creating on the very first attempt; subsequent retries
        # stay in Pending so the operator status is stable while dependencies settle.
        if retry == 0:
            await update_status(name, namespace, "Creating", "Waiting for LinuxNetwork")
        else:
            await update_status(name, namespace, "Pending", error_msg)

        # Raise temporary error to retry later
        raise kopf.TemporaryError(error_msg, delay=20)

    try:
        # All dependencies ready — set Creating now, right before running Ansible
        await update_status(name, namespace, "Creating", "Creating Device")

        # Create the Device using Ansible
        result = await create_device(networkvm_ip_address, name, spec)

        if result['success']:
            # Update status to ready — include mgmt_ip so TrafficTest can find the daemon
            await update_status(
                name, namespace, "Ready",
                f"Device {name} created successfully",
                mgmt_ip=result.get('mgmt_ip', ''),
            )
            logger.info(f"Successfully created Device {name}")
        else:
            error_msg = result.get('error', 'Unknown creation error')
            if _is_transient_error(error_msg):
                logger.warning(f"Transient creation error for {name}, will retry: {error_msg}")
                await update_status(name, namespace, "Pending", f"Creation pending retry: {error_msg}")
                raise kopf.TemporaryError(f"Transient creation error: {error_msg}", delay=30)
            else:
                await update_status(name, namespace, "Failed", f"Failed to create Device: {error_msg}")
                raise kopf.PermanentError(f"Device creation failed: {error_msg}")

    except (kopf.TemporaryError, kopf.PermanentError):
        # Re-raise kopf control exceptions without double-handling
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error creating Device {name}: {error_msg}")
        if _is_transient_error(error_msg):
            logger.warning(f"Treating as transient error, will retry: {error_msg}")
            await update_status(name, namespace, "Pending", f"Retrying after error: {error_msg}")
            raise kopf.TemporaryError(error_msg, delay=30)
        else:
            await update_status(name, namespace, "Failed", error_msg)
            raise

@kopf.on.delete('google.dev', 'v1', 'device')
async def delete_device_handler(body, spec, name, namespace, logger, **kwargs):
    """Handle Device deletion using Ansible"""
    logger.info(f"Deleting Device: {name} in namespace: {namespace}")

    networkvm_ip_address = await get_ip("automation", "networkvm")
    if networkvm_ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {networkvm_ip_address}")

    try:
        # Delete the Device container using Ansible
        result = await delete_device(networkvm_ip_address, name, spec)
        
        if result['success']:
            logger.info(f"Successfully deleted Device {name}")
        else:
            logger.warning(f"Failed to delete Device {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes

    except Exception as e:
        logger.error(f"Error during Device deletion {name}: {e}")
        # Don't raise error on delete failure

#########################################################################
# Error Classification
#########################################################################

def _is_transient_error(error_msg: str) -> bool:
    """
    Determine if an error is transient (worth retrying) vs permanent.

    Transient errors include SSH/network connectivity issues to the VM,
    Docker daemon temporarily unavailable, image pull timeouts, container
    name conflicts (idempotent retry), and Ansible connection timeouts.
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
        'ansible playbook failed with status: failed',  # Generic Ansible failure — often transient
        'already in use',        # Container name conflict — idempotent retry
        'already exists',        # Container already exists — idempotent retry
        'no such host',
        'network is unreachable',
        'pulling image',
        'pull access denied',    # Docker pull issue — may be transient
        'toomanyrequests',       # Docker Hub rate limit
        'i/o timeout',
        'broken pipe',
        'eof',
        'not ready',
        'exit code 124',         # timeout(1) killed command (slow VM)
        'timed out waiting',
        'timed out',                 # Ansible subprocess timeout
    ]
    return any(pattern in error_lower for pattern in transient_patterns)

#########################################################################
# LinuxNetwork Validation
#########################################################################

async def check_linux_network_ready(network_name: str, namespace: str) -> bool:
    """
    Check if the specified LinuxNetwork CR exists and has status 'Ready'.
    
    Args:
        network_name: Name of the LinuxNetwork resource
        namespace: Kubernetes namespace
    
    Returns:
        bool: True if network is ready, False otherwise
    """
    if not network_name:
        logger.warning("No network_name provided for LinuxNetwork check")
        return False
        
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    
    try:
        network = api.get(name=network_name, namespace=namespace)
        network_dict = network.to_dict()
        
        status = network_dict.get('status', {})
        phase = status.get('phase', 'Unknown')
        
        if phase != 'Ready':
            logger.warning(f"LinuxNetwork '{network_name}' is in phase '{phase}', not Ready")
            return False
        else:
            logger.info(f"LinuxNetwork '{network_name}' is Ready")
            return True

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning(f"LinuxNetwork '{network_name}' not found")
            return False
        else:
            logger.error(f"Error checking LinuxNetwork '{network_name}': {e}")
            return False
    except Exception as e:
        logger.error(f"Unexpected error checking LinuxNetwork '{network_name}': {e}")
        return False

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, mgmt_ip: str = None):
    """Update the status of a Device resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')

    status = {
        'phase': phase,
        'message': message
    }

    if mgmt_ip:
        status['mgmt_ip'] = mgmt_ip

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
            logger.warning(f"Status subresource not enabled for Device {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for Device {name}: {e}")
