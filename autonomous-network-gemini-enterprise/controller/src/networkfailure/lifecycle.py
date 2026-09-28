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
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from networkfailure.lifecycle_tasks import inject_failure, restore_failure
from networkfailure.operator_injection import inject_failure_operator, restore_failure_operator
from utils.compute import get_ip
from graph.ops_tasks import sync_fault_event, close_fault_event

logger = logging.getLogger(__name__)


async def _derive_bridge_name(infra_name: str, router_a: str, router_b: str, namespace: str) -> str:
    """
    Derive the Linux bridge name connecting two routers by scanning the
    VyOSInfrastructure spec for the network segment shared by both routers.

    The network segment name in VyOSInfrastructure is the same as the Linux
    bridge name created by the LinuxNetwork operator on the network VM host.

    Raises ValueError if no shared network is found or if multiple shared
    networks exist (ambiguous — cannot determine which link to bring down).
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSInfrastructure')
    infra = api.get(name=infra_name, namespace=namespace)

    shared_networks = []
    for network in infra.spec.get('networks', []):
        # Skip loopback and management networks — they are not p2p links
        network_type = network.get('network_type', '')
        if network_type in ('loopback', 'management'):
            continue
        router_names = {r['router_name'] for r in network.get('connected_routers', [])}
        if router_a in router_names and router_b in router_names:
            shared_networks.append(network['name'])

    if not shared_networks:
        raise ValueError(
            f"No shared p2p network found between '{router_a}' and '{router_b}' "
            f"in VyOSInfrastructure '{infra_name}'"
        )
    if len(shared_networks) > 1:
        raise ValueError(
            f"Multiple shared networks found between '{router_a}' and '{router_b}' "
            f"in VyOSInfrastructure '{infra_name}': {shared_networks}. "
            "Cannot determine which link to bring down — use target.interface instead."
        )
    return shared_networks[0]


#########################################################################
# NetworkFailure Lifecycle Handlers
#########################################################################

@kopf.on.create('google.dev', 'v1', 'networkfailure')
async def create_network_failure_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """
    Handle NetworkFailure creation by injecting the specified fault into the
    running VyOS router topology.

    Workflow:
    1. Validate spec (required fields per failure type)
    2. Retrieve Network VM IP for Ansible execution
    3. Set status to Injecting
    4. Run the injection Ansible playbook via lifecycle_tasks
    5. Store the returned original_state in status for later restoration
    6. Set status to Active with timestamp
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})
    router = target.get('router')

    logger.info(f"Creating NetworkFailure '{name}': type={failure_type}, router={router}")

    # --- Validate required fields per failure type ---
    validation_error = _validate_spec(failure_type, target, parameters, spec=spec)
    if validation_error:
        await _update_status(name, namespace, "Failed", validation_error)
        raise kopf.PermanentError(validation_error)

    # --- Get Network VM IP address ---
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError(
            "No IP address found on Network VM yet — waiting for networkvm to be ready",
            delay=15
        )
    logger.info(f"Network VM address: {ip_address}")

    # --- Determine injection mode based on failure type ---
    # Operator mode is used for configuration-based faults that can be represented in CRDs.
    # Direct mode is used for kernel/transient faults that cannot be represented in CRDs.
    operator_mode_types = {
        'VRF_RT_MISCONFIGURATION',
        'MTU_MISMATCH',
        'BGP_SESSION_DOWN',
        'PACKET_CORRUPTION',
        'OSPF_AREA_MISMATCH',
        'OSPF_COST_INFLATION'
    }
    injection_mode = 'operator' if failure_type in operator_mode_types else 'direct'

    # --- Mark as Injecting and write initial FaultEvent to Spanner ---
    await _update_status(name, namespace, "Injecting",
                         f"Injecting {failure_type} on router '{router}' (mode={injection_mode})")
    try:
        await sync_fault_event(name=name, spec=spec, phase='Injecting', injected_at=None)
    except Exception as e:
        logger.warning(f"Failed to write FaultEvent to Spanner (non-fatal): {e}")

    # --- For LINK_DOWN bridge mode: derive bridge name before injection ---
    if failure_type == 'LINK_DOWN' and target.get('peer_router'):
        infra_name = spec.get('infrastructureRef')
        peer_router = target.get('peer_router')
        try:
            bridge_name = await _derive_bridge_name(infra_name, router, peer_router, namespace)
            logger.info(
                f"LINK_DOWN bridge mode: derived bridge '{bridge_name}' "
                f"for {router} ↔ {peer_router}"
            )
            # Inject the derived bridge name into a mutable copy of spec so
            # lifecycle_tasks can pass it to the Ansible playbook as an extravar.
            spec = dict(spec)
            spec['_derived_bridge'] = bridge_name
        except ValueError as e:
            error_msg = f"LINK_DOWN bridge derivation failed: {e}"
            logger.error(error_msg)
            await _update_status(name, namespace, "Failed", error_msg)
            raise kopf.PermanentError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error deriving bridge name: {e}"
            logger.error(error_msg)
            await _update_status(name, namespace, "Failed", error_msg)
            raise kopf.PermanentError(error_msg)

    # --- Run injection (direct or operator mode) ---
    try:
        if injection_mode == 'operator':
            result = await inject_failure_operator(name, namespace, spec)
        else:
            result = await inject_failure(name, ip_address, spec)
    except Exception as e:
        error_msg = f"Unexpected error during fault injection: {e}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    if not result['success']:
        error_msg = f"Fault injection failed: {result.get('error', 'unknown error')}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    # --- Mark as Active and store original state ---
    injected_at = datetime.now(timezone.utc).isoformat()
    original_state = result.get('original_state', {})

    await _update_status(
        name, namespace, "Active",
        f"Fault '{failure_type}' is active on router '{router}' (mode={injection_mode}) — "
        "original state saved for restoration",
        additional_data={
            'injected_at': injected_at,
            'original_state': original_state,
        }
    )

    # --- Write Active FaultEvent to Spanner ---
    try:
        await sync_fault_event(name=name, spec=spec, phase='Active', injected_at=injected_at)
    except Exception as e:
        logger.warning(f"Failed to write Active FaultEvent to Spanner (non-fatal): {e}")

    logger.info(f"NetworkFailure '{name}' successfully injected (mode={injection_mode}). original_state={original_state}")


@kopf.on.delete('google.dev', 'v1', 'networkfailure')
async def delete_network_failure_handler(body, spec, name, namespace, logger, **kwargs):
    """
    Handle NetworkFailure deletion by restoring the original network configuration.

    Workflow:
    1. Retrieve the original_state saved during injection from the resource status
    2. Retrieve Network VM IP for Ansible execution
    3. Set status to Restoring
    4. Run the restoration Ansible playbook via lifecycle_tasks
    5. Set status to Restored
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    router = target.get('router')
    status = body.get('status', {})
    original_state = status.get('original_state', {})

    # kopf's body snapshot may be stale if the status patch from the create handler
    # has not yet propagated to the informer cache. Re-fetch from the API when empty.
    if not original_state:
        try:
            client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
            api = client.resources.get(api_version='google.dev/v1', kind='NetworkFailure')
            resource = api.get(name=name, namespace=namespace)
            original_state = (resource.to_dict().get('status') or {}).get('original_state', {})
            if original_state:
                logger.info(
                    f"Re-fetched original_state for '{name}' from API "
                    f"(body snapshot was stale): {original_state}"
                )
        except Exception as e:
            logger.warning(f"Could not re-fetch original_state for '{name}': {e}")

    # For LINK_DOWN bridge mode, the bridge name can always be re-derived from the
    # VyOSInfrastructure spec — it does not depend on saved status. This is the most
    # reliable path because status may not have been written if the resource was deleted
    # very quickly after creation (status patch races with deletion).
    if failure_type == 'LINK_DOWN' and target.get('peer_router'):
        peer_router = target.get('peer_router')
        infra_name = spec.get('infrastructureRef')
        if infra_name and not original_state.get('bridge'):
            try:
                bridge_name = await _derive_bridge_name(infra_name, router, peer_router, namespace)
                logger.info(
                    f"LINK_DOWN bridge mode: re-derived bridge '{bridge_name}' "
                    f"for {router} ↔ {peer_router} (status was empty)"
                )
                original_state = dict(original_state)
                original_state['bridge'] = bridge_name
                original_state.setdefault('mode', 'bridge')
                original_state.setdefault('router', router)
                original_state.setdefault('peer_router', peer_router)
            except Exception as e:
                logger.warning(f"Could not re-derive bridge name for '{name}': {e}")

    logger.info(f"Deleting NetworkFailure '{name}': type={failure_type}, router={router}")

    # --- Get Network VM IP address ---
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        logger.warning(
            f"No IP address found on Network VM, skipping restoration for '{name}'. "
            "The fault may remain active on the router."
        )
        return

    # --- Determine injection mode based on failure type ---
    # Must match the same logic used during creation
    operator_mode_types = {
        'VRF_RT_MISCONFIGURATION',
        'MTU_MISMATCH',
        'BGP_SESSION_DOWN',
        'PACKET_CORRUPTION',
        'OSPF_AREA_MISMATCH',
        'OSPF_COST_INFLATION'
    }
    injection_mode = 'operator' if failure_type in operator_mode_types else 'direct'

    # --- Mark as Restoring ---
    await _update_status(name, namespace, "Restoring",
                         f"Restoring original configuration on router '{router}'")

    # --- Run restoration (direct or operator mode) ---
    try:
        if injection_mode == 'operator':
            result = await restore_failure_operator(name, namespace, spec, original_state)
        else:
            result = await restore_failure(name, ip_address, spec, original_state)
    except Exception as e:
        error_msg = f"Unexpected error during fault restoration: {e}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        # Do NOT raise a PermanentError on delete — allow the resource to be removed
        # even if restoration fails. Log the issue for manual remediation.
        logger.warning(
            f"NetworkFailure '{name}' deletion will proceed despite restoration failure. "
            "Manual remediation may be required on the router."
        )
        return

    if not result['success']:
        logger.warning(
            f"Restoration playbook reported failure for NetworkFailure '{name}': "
            f"{result.get('error', 'unknown error')}. "
            "Proceeding with resource deletion. Manual remediation may be required."
        )
    elif result.get('remediation') == 'skipped':
        # Manual remediation detected - the underlying CR was modified before deletion
        restored_at = datetime.now(timezone.utc).isoformat()
        await _update_status(
            name, namespace, "Restored",
            f"Manual remediation detected on router '{router}' — restoration skipped",
            additional_data={'restored_at': restored_at, 'remediation': 'manual'}
        )
        logger.info(
            f"NetworkFailure '{name}' restoration skipped at {restored_at}: "
            f"underlying CR was manually remediated. No automatic restoration performed."
        )
    else:
        # Normal automatic restoration
        restored_at = datetime.now(timezone.utc).isoformat()
        await _update_status(
            name, namespace, "Restored",
            f"Original configuration restored on router '{router}'",
            additional_data={'restored_at': restored_at, 'remediation': 'automatic'}
        )
        logger.info(f"NetworkFailure '{name}' successfully restored at {restored_at}")


@kopf.on.delete('google.dev', 'v1', 'networkfailure')
async def close_fault_event_handler(name, logger, **kwargs):
    """Close the Spanner FaultEvent row when the NetworkFailure goes away.

    Deliberately a second delete handler rather than a step at the end of
    delete_network_failure_handler: that one returns early on three separate
    restoration paths, so a fault whose restoration was skipped or failed would
    keep an open row anyway. kopf runs both handlers for the same event.

    close_fault_event was imported and never called, so valid_end_ts stayed
    NULL on every fault ever injected. That is the column query_fault_events
    reads to decide whether a fault is still in place, which made every
    long-removed fault read as ACTIVE.
    """
    try:
        await close_fault_event(name)
    except Exception as e:
        # Never block deletion on a telemetry write -- the resource has to be
        # removable when Spanner is unreachable. close_fault_event raises
        # kopf.TemporaryError, which would otherwise retry the delete forever.
        logger.warning(f"Failed to close FaultEvent for '{name}' (non-fatal): {e}")


@kopf.on.resume('google.dev', 'v1', 'networkfailure')
async def resume_network_failure_handler(body, spec, name, namespace, logger, **kwargs):
    """
    Re-sync NetworkFailure state when the operator restarts.

    If a failure was Active when the operator restarted, log a warning so that
    operators are aware that a fault is still injected in the network.
    No re-injection is performed on resume — the fault persists in the network
    and the resource continues tracking it.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    router = target.get('router')
    status = body.get('status', {})
    phase = status.get('phase', 'Unknown')

    logger.info(
        f"Resuming NetworkFailure '{name}': type={failure_type}, router={router}, phase={phase}"
    )

    if phase == 'Active':
        injected_at = status.get('injected_at', 'unknown time')
        logger.warning(
            f"NetworkFailure '{name}' ({failure_type} on {router}) is still ACTIVE "
            f"(injected at {injected_at}). The fault is currently affecting the network. "
            "Delete this resource to restore the original configuration."
        )
    elif phase == 'Injecting':
        # The operator crashed mid-injection — mark as Failed for visibility
        logger.error(
            f"NetworkFailure '{name}' was in 'Injecting' phase when the operator restarted. "
            "The fault state is uncertain. Manual verification is required."
        )
        await _update_status(
            name, namespace, "Failed",
            "Operator restarted during fault injection. Fault state is uncertain. "
            "Delete this resource and manually verify the router configuration."
        )
    elif phase == 'Restoring':
        logger.error(
            f"NetworkFailure '{name}' was in 'Restoring' phase when the operator restarted. "
            "The restoration state is uncertain. Manual verification is required."
        )
        await _update_status(
            name, namespace, "Failed",
            "Operator restarted during fault restoration. Restoration state is uncertain. "
            "Delete this resource and manually verify the router configuration."
        )


#########################################################################
# Input Validation
#########################################################################

def _validate_spec(failure_type: str, target: dict, parameters: dict,
                   spec: Optional[dict] = None) -> Optional[str]:
    """
    Validate that all required fields are present for the given failure type.
    Returns an error message string if validation fails, or None if valid.
    """
    router = target.get('router')
    interface = target.get('interface')
    peer_ip = target.get('peer_ip')
    vrf = target.get('vrf')
    if spec is None:
        spec = {}

    if not router:
        return "spec.target.router is required for all failure types"

    if failure_type == 'MTU_MISMATCH':
        if not interface:
            return "spec.target.interface is required for MTU_MISMATCH"
        mtu = parameters.get('mtu')
        if mtu is None:
            return "spec.parameters.mtu is required for MTU_MISMATCH"

    elif failure_type == 'BGP_SESSION_DOWN':
        if not peer_ip:
            return "spec.target.peer_ip is required for BGP_SESSION_DOWN"
        if not vrf:
            return "spec.target.vrf is required for BGP_SESSION_DOWN"
        if parameters.get('remote_as') is None:
            return "spec.parameters.remote_as is required for BGP_SESSION_DOWN"

    elif failure_type == 'PROCESS_CRASH':
        method = parameters.get('method', 'loopback_disable')
        if method not in ('process_kill', 'loopback_disable'):
            return "spec.parameters.method must be 'process_kill' or 'loopback_disable'"

    elif failure_type == 'PACKET_CORRUPTION':
        if not interface:
            return "spec.target.interface is required for PACKET_CORRUPTION"
        error_rate = parameters.get('error_rate', '5%')
        if not error_rate:
            return "spec.parameters.error_rate is required for PACKET_CORRUPTION"

    elif failure_type == 'LINK_DOWN':
        has_interface = bool(interface)
        has_peer_router = bool(target.get('peer_router'))
        if not has_interface and not has_peer_router:
            return ("LINK_DOWN requires either spec.target.interface (interface mode) "
                    "or spec.target.peer_router (bridge mode) — exactly one must be provided")
        if has_interface and has_peer_router:
            return ("spec.target.interface and spec.target.peer_router are mutually exclusive "
                    "for LINK_DOWN — provide exactly one")
        if has_peer_router and not spec.get('infrastructureRef'):
            return ("spec.infrastructureRef is required when using LINK_DOWN bridge mode "
                    "(spec.target.peer_router is set)")

    elif failure_type == 'OSPF_AREA_MISMATCH':
        if not interface:
            return "spec.target.interface is required for OSPF_AREA_MISMATCH"
        if not parameters.get('wrong_area'):
            return "spec.parameters.wrong_area is required for OSPF_AREA_MISMATCH"
        if not parameters.get('correct_area'):
            return "spec.parameters.correct_area is required for OSPF_AREA_MISMATCH"

    elif failure_type == 'DUPLICATE_IP':
        if not interface:
            return "spec.target.interface is required for DUPLICATE_IP"
        if not parameters.get('duplicate_ip'):
            return "spec.parameters.duplicate_ip is required for DUPLICATE_IP"

    elif failure_type == 'TXQUEUE_STARVATION':
        if not interface:
            return "spec.target.interface is required for TXQUEUE_STARVATION"
        queue_length = parameters.get('queue_length', 20)
        if queue_length is None:
            return "spec.parameters.queue_length is required for TXQUEUE_STARVATION"

    elif failure_type == 'OSPF_COST_INFLATION':
        if not interface:
            return "spec.target.interface is required for OSPF_COST_INFLATION"
        ospf_cost = parameters.get('ospf_cost', 65535)
        if ospf_cost is None:
            return "spec.parameters.ospf_cost is required for OSPF_COST_INFLATION"

    elif failure_type == 'VRF_RT_MISCONFIGURATION':
        if not vrf:
            return "spec.target.vrf is required for VRF_RT_MISCONFIGURATION"
        if not parameters.get('wrong_rt'):
            return "spec.parameters.wrong_rt is required for VRF_RT_MISCONFIGURATION"
        if not parameters.get('correct_rt'):
            return "spec.parameters.correct_rt is required for VRF_RT_MISCONFIGURATION"

    else:
        return f"Unknown failureType: {failure_type}"

    return None


#########################################################################
# Status Management
#########################################################################

async def _update_status(name: str, namespace: str, phase: str, message: str,
                          additional_data: Optional[Dict[str, Any]] = None):
    """Update the status subresource of a NetworkFailure resource."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='NetworkFailure')

    try:
        resource = api.get(name=name, namespace=namespace)
        resource_dict = resource.to_dict()

        if 'status' not in resource_dict or resource_dict['status'] is None:
            resource_dict['status'] = {}

        status = {
            'phase': phase,
            'message': message,
        }

        if additional_data:
            status.update(additional_data)

        resource_dict['status'].update(status)

        api.status.patch(
            namespace=namespace,
            name=name,
            body={'status': resource_dict['status']},
            content_type='application/merge-patch+json',
        )

        logger.debug(f"Updated status for NetworkFailure '{name}': {phase} — {message}")

    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(
                f"Status subresource not enabled for NetworkFailure '{name}', "
                "skipping status update."
            )
        else:
            logger.error(f"Failed to update status for NetworkFailure '{name}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status for NetworkFailure '{name}': {e}")
