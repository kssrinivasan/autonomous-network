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
import kubernetes
from infrastructure.linuxnetwork.lifecycle_tasks import (
    create_linux_network,
    delete_linux_network,
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# DockerNetwork Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'linuxnetwork')
async def create_linuxnetwork(body, spec, name, namespace, uid, logger, retry=0, **kwargs):
    """Handle LinuxNetwork creation using Ansible"""
    logger.info(f"Creating LinuxNetwork: {name} in namespace: {namespace}")
    logger.info(f"spec {spec}")

    # Idempotency guard: if already Ready (e.g. operator restarted mid-handler after
    # Ansible succeeded but before kopf recorded completion), skip re-creation entirely.
    current_phase = body.get('status', {}).get('phase')
    if current_phase == 'Ready':
        logger.info(f"LinuxNetwork {name} already in Ready state, skipping re-creation")
        return

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        # Only emit "Creating" on the first attempt.  On kopf retries the resource is
        # likely already in "Failed" (set below before re-raise) so flipping back to
        # "Creating" would produce spurious Ready->Creating->Ready churn.
        if retry == 0:
            await update_status(name, namespace, "Creating", "Creating Linux network")

        # Create the Linux network using Ansible
        result = await create_linux_network(ip_address, spec)

        if result['success']:
            # if network_type == 'management' also add the default_interface to status
            if spec.get('network_type') == 'management':
                logger.info("Linux network is of type 'management', performing additional setup")

                # Extract default interface if available
                default_interface = result.get('default_interface', 'unknown')
                interface_ip = result.get('interface_ip', 'unknown')
                default_gateway = result.get('default_gateway', 'unknown')
                extra_status = {'interface': default_interface, 'gateway': default_gateway, 'interface_ip': interface_ip}
            else:
                extra_status = {}
            
            # Update status to ready with full details
            await update_status(
                name, namespace, "Ready", 
                f"Linux network {spec['name']} created successfully",
                extra_status=extra_status
            )
            logger.info(f"Successfully created LinuxNetwork {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to create network: {result['error']}")
            raise kopf.PermanentError(f"Linux network creation failed: {result['error']}")

    except (kopf.TemporaryError, kopf.PermanentError):
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to create LinuxNetwork {name}: {error_msg}")
        await update_status(name, namespace, "Failed", error_msg)
        # Raise as TemporaryError so kopf retries with a proper delay rather than
        # treating an unclassified exception as a crash (which has no delay cap).
        raise kopf.TemporaryError(error_msg, delay=30)

@kopf.on.delete('google.dev', 'v1', 'linuxnetwork')
async def delete_linuxnetwork(body, spec, status, name, namespace, logger, **kwargs):
    """Handle LinuxNetwork deletion using Ansible"""
    logger.info(f"Deleting LinuxNetwork: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    logger.info("TODO::make sure all dependent resources are deleted first")

    try:        
        # Delete the Docker network using Ansible
        result = await delete_linux_network(ip_address, spec, status)
        
        if result['success']:
            logger.info(f"Successfully deleted DockerNetwork {spec.get('network_name')}")
        else:
            logger.warning(f"Failed to delete Docker network {spec.get('network_name')}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during DockerNetwork deletion {name}: {e}")
        # Don't raise error on delete failure

@kopf.on.resume('google.dev', 'v1', 'linuxnetwork')
async def resume_linuxnetwork(body, spec, name, namespace, logger, **kwargs):
    """Handle operator restart against already-created LinuxNetwork resources.

    kopf fires on.resume (not on.create) when the operator restarts and the
    on.create handler previously completed.  We just log current state here to
    prevent the operator from re-running the full creation flow (and causing a
    Creating blip) every time it restarts.
    """
    current_phase = body.get('status', {}).get('phase')
    logger.info(f"Resuming LinuxNetwork {name} (phase={current_phase})")
    if current_phase != 'Ready':
        logger.warning(
            f"LinuxNetwork {name} is not Ready on resume (phase={current_phase})."
        )

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, extra_status: dict = None):
    """Update the status of a LinuxNetwork resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')

    status = {
        'phase': phase,
        'message': message
    }

    # Add any additional status fields
    if extra_status:
        status.update(extra_status)

    logger.debug(f"Updating status for LinuxNetwork {name}: {status}")

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
            logger.warning(f"Status subresource not enabled for LinuxNetwork {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for LinuxNetwork {name}: {e}")
