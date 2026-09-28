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

"""
Operator-mode fault injection for NetworkFailure.

This module implements operator-mode fault injection, which injects faults by
patching the VyOS operator CRDs (VyOSInfrastructure, VyOSUnderlay, VyOSL3VPN)
rather than directly modifying the running Docker containers.

The NetworkFailure lifecycle handler automatically determines the injection mode
based on failureType. Configuration-based faults use operator mode; kernel/transient
faults use direct mode.

Operator mode fault types (expressed as CRD configuration changes):
  - MTU_MISMATCH          → VyOSInfrastructure network mtu field
  - BGP_SESSION_DOWN      → VyOSL3VPN bgp.vrfs[*].neighbors list
  - PACKET_CORRUPTION     → VyOSUnderlay traffic_policy.network_emulator
  - OSPF_AREA_MISMATCH    → VyOSUnderlay router ospf area assignment
  - OSPF_COST_INFLATION   → VyOSUnderlay router ospf interface cost
  - VRF_RT_MISCONFIGURATION → VyOSL3VPN vrfs[*].rt_import

Direct mode fault types (kernel/transient, cannot be expressed in CRDs):
  - TXQUEUE_STARVATION  (Linux kernel txqueuelen — not in VyOS config)
  - LINK_DOWN           (ip link set — not in VyOS config)
  - DUPLICATE_IP        (ip addr add — not in VyOS config)
  - PROCESS_CRASH       (kill/loopback — not in VyOS config)

Operator mode behavior:
  - Patches the relevant CRD in-place. The original field values are read from
    the CRD before patching and stored in original_state for restoration.
  - When the NetworkFailure is deleted, the CRD is patched back to the original values.
  - Tracks all changes in Spanner via the existing SCD (Slowly Changing Dimension)
    mechanism used by the graph/lifecycle_tasks.py handlers.
  - Changes are visible in GitOps / config management (the CRD is the source of truth).
"""

import asyncio
import logging
import copy
import json
from typing import Dict, Any, Optional

import kubernetes
import yaml as yaml_lib

from utils.git import find_crd_git_path, get_git_file, commit_git_file

logger = logging.getLogger(__name__)


async def inject_failure_operator(name: str, namespace: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inject a fault by patching the appropriate VyOS operator CRD.

    Returns:
        dict with 'success' bool, 'original_state' dict, and optionally 'error' string.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})
    write_mode = spec.get('writeMode', 'git')

    router = target.get('router')
    interface = target.get('interface', '')
    peer_ip = target.get('peer_ip', '')
    vrf = target.get('vrf', 'global')

    logger.info(f"[operator-mode] Injecting '{failure_type}' on router '{router}' for NetworkFailure '{name}' (writeMode={write_mode})")

    try:
        if failure_type == 'MTU_MISMATCH':
            return await _inject_mtu_mismatch(name, namespace, router, interface, parameters, write_mode)

        elif failure_type == 'BGP_SESSION_DOWN':
            return await _inject_bgp_session_down(name, namespace, router, peer_ip, vrf, parameters, write_mode)

        elif failure_type == 'PACKET_CORRUPTION':
            return await _inject_packet_corruption(name, namespace, router, interface, parameters, write_mode)

        elif failure_type == 'OSPF_AREA_MISMATCH':
            return await _inject_ospf_area_mismatch(name, namespace, router, interface, parameters, write_mode)

        elif failure_type == 'OSPF_COST_INFLATION':
            return await _inject_ospf_cost_inflation(name, namespace, router, interface, parameters, write_mode)

        elif failure_type == 'VRF_RT_MISCONFIGURATION':
            return await _inject_vrf_rt_misconfiguration(name, namespace, router, vrf, parameters, write_mode)

        else:
            return {
                'success': False,
                'error': f"failureType '{failure_type}' is not supported in operator injection mode. "
                         "Use injectionMode: direct instead."
            }

    except Exception as e:
        logger.error(f"[operator-mode] Unexpected error injecting '{failure_type}': {e}")
        return {'success': False, 'error': str(e)}


async def restore_failure_operator(name: str, namespace: str, spec: Dict[str, Any],
                                    original_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Restore a fault by patching the VyOS operator CRD back to its original values.

    Detects manual remediation by comparing the current CR state with the faulty
    values from spec.parameters. If the CR has been manually modified (remediated),
    restoration is skipped.

    Returns:
        dict with 'success' bool and optionally 'error' string or 'remediation': 'skipped'.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})
    write_mode = spec.get('writeMode', 'git')

    router = target.get('router')
    interface = target.get('interface', '')
    peer_ip = target.get('peer_ip', '')
    vrf = target.get('vrf', 'global')

    logger.info(f"[operator-mode] Restoring '{failure_type}' on router '{router}' for NetworkFailure '{name}' (writeMode={write_mode})")

    try:
        if failure_type == 'MTU_MISMATCH':
            return await _restore_mtu_mismatch(name, namespace, router, interface, original_state, parameters, write_mode)

        elif failure_type == 'BGP_SESSION_DOWN':
            return await _restore_bgp_session_down(name, namespace, router, peer_ip, vrf, original_state, parameters, write_mode)

        elif failure_type == 'PACKET_CORRUPTION':
            return await _restore_packet_corruption(name, namespace, router, interface, original_state, parameters, write_mode)

        elif failure_type == 'OSPF_AREA_MISMATCH':
            return await _restore_ospf_area_mismatch(name, namespace, router, interface, original_state, parameters, write_mode)

        elif failure_type == 'OSPF_COST_INFLATION':
            return await _restore_ospf_cost_inflation(name, namespace, router, interface, original_state, parameters, write_mode)

        elif failure_type == 'VRF_RT_MISCONFIGURATION':
            return await _restore_vrf_rt_misconfiguration(name, namespace, router, vrf, original_state, parameters, write_mode)

        else:
            return {
                'success': False,
                'error': f"failureType '{failure_type}' is not supported in operator restoration mode."
            }

    except Exception as e:
        logger.error(f"[operator-mode] Unexpected error restoring '{failure_type}': {e}")
        return {'success': False, 'error': str(e)}


# =============================================================================
# MTU_MISMATCH — VyOSInfrastructure network mtu field
# =============================================================================

async def _inject_mtu_mismatch(name, namespace, router, interface, parameters, write_mode='direct'):
    """
    Inject MTU mismatch by patching the VyOSInfrastructure CRD.
    Finds the network connected to router/interface and sets its mtu field.
    """
    mtu = parameters.get('mtu', 1400)
    infra = await _get_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1')
    if not infra:
        return {'success': False, 'error': 'VyOSInfrastructure CRD not found in namespace'}

    infra_body = infra[0]  # Assume single infra CRD per namespace
    networks = infra_body.get('spec', {}).get('networks', [])

    original_mtu = None
    target_network_name = None

    for net in networks:
        for conn in net.get('connected_routers', []):
            if conn.get('router_name') == router and conn.get('interface') == interface:
                target_network_name = net['name']
                original_mtu = net.get('mtu', 1500)
                break
        if target_network_name:
            break

    if not target_network_name:
        return {
            'success': False,
            'error': f"No network found connecting router '{router}' interface '{interface}' in VyOSInfrastructure"
        }

    # Patch the network mtu in the CRD
    patched_networks = []
    for net in networks:
        net_copy = dict(net)
        if net_copy['name'] == target_network_name:
            net_copy['mtu'] = mtu
        patched_networks.append(net_copy)

    patch = {'spec': {'networks': patched_networks}}
    infra_name = infra_body['metadata']['name']
    success = await _patch_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name, patch, write_mode=write_mode, kind='VyOSInfrastructure')

    if success:
        logger.info(f"[operator-mode] MTU_MISMATCH: patched network '{target_network_name}' mtu={mtu} (was {original_mtu})")
        return {
            'success': True,
            'original_state': {
                'mtu': original_mtu,
                'network_name': target_network_name,
                'infra_name': infra_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSInfrastructure CRD'}


async def _restore_mtu_mismatch(name, namespace, router, interface, original_state, parameters, write_mode='direct'):
    original_mtu = original_state.get('mtu', 1500)
    network_name = original_state.get('network_name')
    infra_name = original_state.get('infra_name')

    # Read what we injected from spec.parameters
    injected_mtu = parameters.get('mtu', 1400)

    # Fallback: auto-discover infra_name and network_name if missing from original_state
    if not infra_name or not network_name:
        logger.warning(
            f"[operator-mode] MTU_MISMATCH restore: original_state missing infra_name/network_name "
            f"for '{name}' — auto-discovering VyOSInfrastructure CRD"
        )
        all_infras = await _get_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1')
        for infra_candidate in all_infras:
            networks = infra_candidate.get('spec', {}).get('networks', [])
            for net in networks:
                for conn in net.get('connected_routers', []):
                    if conn.get('router_name') == router and conn.get('interface') == interface:
                        infra_name = infra_candidate['metadata']['name']
                        network_name = net['name']
                        logger.info(
                            f"[operator-mode] MTU_MISMATCH restore: "
                            f"auto-discovered infra_name='{infra_name}', network_name='{network_name}'"
                        )
                        break
                if infra_name:
                    break
            if infra_name:
                break

    if not network_name or not infra_name:
        return {
            'success': False,
            'error': f'original_state missing network_name/infra_name and auto-discovery failed for router {router}/{interface}'
        }

    infra = await _get_crd_by_name(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name)
    if not infra:
        return {'success': False, 'error': f'VyOSInfrastructure {infra_name} not found'}

    # Check if the fault is still present before restoring
    # Read current MTU from the CR
    current_mtu = None
    networks = infra.get('spec', {}).get('networks', [])
    for net in networks:
        if net.get('name') == network_name:
            current_mtu = net.get('mtu', 1500)
            break

    # If current MTU doesn't match what we injected, skip restoration (manual remediation detected)
    if current_mtu != injected_mtu:
        logger.info(
            f"[operator-mode] MTU_MISMATCH restoration skipped for network '{network_name}': "
            f"current MTU ({current_mtu}) differs from injected MTU ({injected_mtu} from parameters). "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active, proceed with restoration
    patched_networks = []
    for net in networks:
        net_copy = dict(net)
        if net_copy['name'] == network_name:
            if original_mtu is None or original_mtu == 1500:
                net_copy.pop('mtu', None)
            else:
                net_copy['mtu'] = original_mtu
        patched_networks.append(net_copy)

    patch = {'spec': {'networks': patched_networks}}
    success = await _patch_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name, patch, write_mode=write_mode, kind='VyOSInfrastructure')
    return {'success': success, 'error': None if success else 'Failed to restore VyOSInfrastructure CRD'}


# =============================================================================
# BGP_SESSION_DOWN — VyOSL3VPN bgp.vrfs[*].neighbors
# =============================================================================

async def _inject_bgp_session_down(name, namespace, router, peer_ip, vrf, parameters, write_mode='direct'):
    """
    Inject BGP session down by clearing the neighbors list in the VyOSL3VPN CRD.
    """
    l3vpn = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
    if not l3vpn:
        return {'success': False, 'error': 'VyOSL3VPN CRD not found in namespace'}

    l3vpn_body = l3vpn[0]
    l3vpn_name = l3vpn_body['metadata']['name']
    routers = l3vpn_body.get('spec', {}).get('routers', [])

    original_neighbors = None
    target_router_idx = None
    target_vrf_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            bgp_vrfs = r.get('bgp', {}).get('vrfs', [])
            for v_idx, v in enumerate(bgp_vrfs):
                if v.get('name') == vrf:
                    original_neighbors = list(v.get('neighbors', []))
                    target_router_idx = r_idx
                    target_vrf_idx = v_idx
                    break
        if target_router_idx is not None:
            break

    if target_router_idx is None:
        return {
            'success': False,
            'error': f"Router '{router}' VRF '{vrf}' not found in VyOSL3VPN"
        }

    # Patch: clear neighbors list
    patched_routers = copy.deepcopy(routers)
    patched_routers[target_router_idx]['bgp']['vrfs'][target_vrf_idx]['neighbors'] = []

    patch = {'spec': {'routers': patched_routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch, write_mode=write_mode, kind='VyOSL3VPN')

    if success:
        logger.info(f"[operator-mode] BGP_SESSION_DOWN: cleared neighbors for {router}/{vrf} in {l3vpn_name}")
        return {
            'success': True,
            'original_state': {
                'original_neighbors': original_neighbors,
                'l3vpn_name': l3vpn_name,
                'vrf': vrf,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSL3VPN CRD'}


async def _restore_bgp_session_down(name, namespace, router, peer_ip, vrf, original_state, parameters, write_mode='direct'):
    original_neighbors = original_state.get('original_neighbors', [])
    l3vpn_name = original_state.get('l3vpn_name')

    # Fallback: auto-discover l3vpn_name if missing from original_state
    if not l3vpn_name:
        logger.warning(
            f"[operator-mode] BGP_SESSION_DOWN restore: original_state missing l3vpn_name "
            f"for '{name}' — auto-discovering VyOSL3VPN CRD containing {router}/{vrf}"
        )
        all_l3vpns = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
        for l3vpn_candidate in all_l3vpns:
            for r in l3vpn_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    for v in r.get('bgp', {}).get('vrfs', []):
                        if v.get('name') == vrf:
                            l3vpn_name = l3vpn_candidate['metadata']['name']
                            logger.info(
                                f"[operator-mode] BGP_SESSION_DOWN restore: "
                                f"auto-discovered l3vpn_name='{l3vpn_name}'"
                            )
                            break
                if l3vpn_name:
                    break
            if l3vpn_name:
                break

    if not l3vpn_name:
        return {
            'success': False,
            'error': f'original_state missing l3vpn_name and no VyOSL3VPN CRD found containing router {router} VRF {vrf}'
        }

    l3vpn = await _get_crd_by_name(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name)
    if not l3vpn:
        return {'success': False, 'error': f'VyOSL3VPN {l3vpn_name} not found'}

    # Check if the fault is still present before restoring
    # Read current neighbors list from the CR
    current_neighbors = None
    current_routers = l3vpn.get('spec', {}).get('routers', [])
    for r in current_routers:
        if r.get('name') == router:
            for v in r.get('bgp', {}).get('vrfs', []):
                if v.get('name') == vrf:
                    current_neighbors = v.get('neighbors', [])
                    break
            break

    # If current neighbors list is not empty, skip restoration (manual remediation detected)
    # The fault cleared the neighbors list, so if it's not empty anymore, someone fixed it
    if current_neighbors:
        logger.info(
            f"[operator-mode] BGP_SESSION_DOWN restoration skipped for {router}/{vrf}: "
            f"neighbors list is not empty ({len(current_neighbors)} neighbors). "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active (neighbors list still empty), proceed with restoration
    routers = copy.deepcopy(current_routers)
    for r in routers:
        if r.get('name') == router:
            for v in r.get('bgp', {}).get('vrfs', []):
                if v.get('name') == vrf:
                    v['neighbors'] = original_neighbors
                    break
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch, write_mode=write_mode, kind='VyOSL3VPN')
    return {'success': success, 'error': None if success else 'Failed to restore VyOSL3VPN CRD'}


# =============================================================================
# PACKET_CORRUPTION — VyOSUnderlay traffic_policy.network_emulator
# =============================================================================

async def _inject_packet_corruption(name, namespace, router, interface, parameters, write_mode='direct'):
    """
    Inject packet corruption by adding a traffic_policy.network_emulator to the
    VyOSUnderlay CRD for the target router.
    """
    error_rate = parameters.get('error_rate', '5%')
    delay = parameters.get('delay', '10ms')

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = underlay_body.get('spec', {}).get('routers', [])

    original_traffic_policy = None
    target_router_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            original_traffic_policy = copy.deepcopy(r.get('traffic_policy', None))
            target_router_idx = r_idx
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    patched_routers = copy.deepcopy(routers)
    patched_routers[target_router_idx]['traffic_policy'] = {
        'network_emulator': [
            {
                'name': f'FAULT_{name.upper().replace("-", "_")}',
                'delay': delay,
                'loss': error_rate,
                'corruption': error_rate,
            }
        ],
        'apply': [
            {'interface': interface, 'out': f'FAULT_{name.upper().replace("-", "_")}'}
        ]
    }

    patch = {'spec': {'routers': patched_routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')

    if success:
        logger.info(f"[operator-mode] PACKET_CORRUPTION: applied traffic_policy to {router}/{interface} in {underlay_name}")
        return {
            'success': True,
            'original_state': {
                'original_traffic_policy': original_traffic_policy,
                'underlay_name': underlay_name,
                'had_existing_qdisc': original_traffic_policy is not None,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_packet_corruption(name, namespace, router, interface, original_state, parameters, write_mode='direct'):
    original_traffic_policy = original_state.get('original_traffic_policy')
    underlay_name = original_state.get('underlay_name')

    # The fault creates a traffic_policy named FAULT_<name>
    fault_policy_name = f'FAULT_{name.upper().replace("-", "_")}'

    # Fallback: auto-discover underlay_name if missing from original_state
    if not underlay_name:
        logger.warning(
            f"[operator-mode] PACKET_CORRUPTION restore: original_state missing underlay_name "
            f"for '{name}' — auto-discovering VyOSUnderlay CRD containing router {router}"
        )
        all_underlays = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
        for underlay_candidate in all_underlays:
            for r in underlay_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    underlay_name = underlay_candidate['metadata']['name']
                    logger.info(
                        f"[operator-mode] PACKET_CORRUPTION restore: "
                        f"auto-discovered underlay_name='{underlay_name}'"
                    )
                    break
            if underlay_name:
                break

    if not underlay_name:
        return {
            'success': False,
            'error': f'original_state missing underlay_name and no VyOSUnderlay CRD found containing router {router}'
        }

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    # Check if the fault is still present before restoring
    # Read current traffic_policy from the CR
    fault_still_present = False
    current_routers = underlay.get('spec', {}).get('routers', [])
    for r in current_routers:
        if r.get('name') == router:
            traffic_policy = r.get('traffic_policy')
            if traffic_policy:
                network_emulators = traffic_policy.get('network_emulator', [])
                for ne in network_emulators:
                    if ne.get('name') == fault_policy_name:
                        fault_still_present = True
                        break
            break

    # If the fault-specific traffic policy is not present, skip restoration (manual remediation detected)
    if not fault_still_present:
        logger.info(
            f"[operator-mode] PACKET_CORRUPTION restoration skipped for {router}/{interface}: "
            f"fault-specific traffic_policy '{fault_policy_name}' not found. "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active, proceed with restoration
    routers = copy.deepcopy(current_routers)
    for r in routers:
        if r.get('name') == router:
            if original_traffic_policy is None:
                r.pop('traffic_policy', None)
            else:
                r['traffic_policy'] = original_traffic_policy
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')
    return {'success': success, 'error': None if success else 'Failed to restore VyOSUnderlay CRD'}


# =============================================================================
# OSPF_AREA_MISMATCH — VyOSUnderlay router ospf area
# =============================================================================

async def _inject_ospf_area_mismatch(name, namespace, router, interface, parameters, write_mode='direct'):
    """
    Inject OSPF area mismatch by moving an interface from its correct area to a wrong area.
    Stores the correct area in original_state for restoration.
    """
    wrong_area = parameters.get('wrong_area', '0.0.0.99')
    correct_area = parameters.get('correct_area', '0.0.0.0')

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = copy.deepcopy(underlay_body.get('spec', {}).get('routers', []))

    target_router_idx = None
    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            target_router_idx = r_idx
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    # Get OSPF configuration for the target router
    if 'protocols' not in routers[target_router_idx]:
        routers[target_router_idx]['protocols'] = {}
    if 'ospf' not in routers[target_router_idx]['protocols']:
        routers[target_router_idx]['protocols']['ospf'] = {}

    ospf_config = routers[target_router_idx]['protocols']['ospf']
    areas = ospf_config.get('areas', [])

    # Find and remove the interface from its current area
    found_in_area = None
    updated_areas = []
    for area_config in areas:
        area_copy = dict(area_config)
        area_id = area_copy.get('area')
        interfaces_list = area_copy.get('interfaces', [])

        if interface in interfaces_list:
            found_in_area = area_id
            # Remove the interface from this area
            interfaces_list = [iface for iface in interfaces_list if iface != interface]
            area_copy['interfaces'] = interfaces_list

        updated_areas.append(area_copy)

    # Add the interface to the wrong area (create area if needed)
    wrong_area_exists = False
    for area_config in updated_areas:
        if area_config.get('area') == wrong_area:
            wrong_area_exists = True
            if 'interfaces' not in area_config:
                area_config['interfaces'] = []
            if interface not in area_config['interfaces']:
                area_config['interfaces'].append(interface)
            break

    if not wrong_area_exists:
        # Create the wrong area and add the interface
        updated_areas.append({
            'area': wrong_area,
            'type': 'standard',
            'interfaces': [interface]
        })

    routers[target_router_idx]['protocols']['ospf']['areas'] = updated_areas

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')

    if success:
        logger.info(
            f"[operator-mode] OSPF_AREA_MISMATCH: moved {interface} from area {found_in_area or correct_area} "
            f"to area {wrong_area} on {router} in {underlay_name}"
        )
        return {
            'success': True,
            'original_state': {
                'correct_area': found_in_area or correct_area,
                'wrong_area': wrong_area,
                'interface': interface,
                'underlay_name': underlay_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_ospf_area_mismatch(name, namespace, router, interface, original_state, parameters, write_mode='direct'):
    """
    Restore OSPF area mismatch by moving the interface back to its correct area.
    """
    correct_area = original_state.get('correct_area', '0.0.0.0')
    wrong_area = original_state.get('wrong_area', '0.0.0.99')
    underlay_name = original_state.get('underlay_name')

    # Fallback: auto-discover underlay_name if missing from original_state
    if not underlay_name:
        logger.warning(
            f"[operator-mode] OSPF_AREA_MISMATCH restore: original_state missing underlay_name "
            f"for '{name}' — auto-discovering VyOSUnderlay CRD containing router {router}"
        )
        all_underlays = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
        for underlay_candidate in all_underlays:
            for r in underlay_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    underlay_name = underlay_candidate['metadata']['name']
                    logger.info(
                        f"[operator-mode] OSPF_AREA_MISMATCH restore: "
                        f"auto-discovered underlay_name='{underlay_name}'"
                    )
                    break
            if underlay_name:
                break

    if not underlay_name:
        return {
            'success': False,
            'error': f'original_state missing underlay_name and no VyOSUnderlay CRD found containing router {router}'
        }

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    # Check if the fault is still present before restoring
    # Find which area currently contains the interface
    current_area = None
    current_routers = underlay.get('spec', {}).get('routers', [])
    for r in current_routers:
        if r.get('name') == router:
            areas = r.get('protocols', {}).get('ospf', {}).get('areas', [])
            for area in areas:
                if interface in area.get('interfaces', []):
                    current_area = area.get('area')
                    break
            break

    # If current area doesn't match what we injected, skip restoration (manual remediation detected)
    if current_area != wrong_area:
        logger.info(
            f"[operator-mode] OSPF_AREA_MISMATCH restoration skipped for {router}/{interface}: "
            f"current area ({current_area}) differs from injected area ({wrong_area}). "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active, proceed with restoration
    routers = copy.deepcopy(current_routers)

    target_router_idx = None
    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            target_router_idx = r_idx
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    ospf_config = routers[target_router_idx].get('protocols', {}).get('ospf', {})
    areas = ospf_config.get('areas', [])

    # Remove interface from wrong area
    updated_areas = []
    for area_config in areas:
        area_copy = dict(area_config)
        area_id = area_copy.get('area')
        interfaces_list = area_copy.get('interfaces', [])

        if area_id == wrong_area and interface in interfaces_list:
            # Remove the interface from the wrong area
            interfaces_list = [iface for iface in interfaces_list if iface != interface]
            area_copy['interfaces'] = interfaces_list
            # Remove the area completely if it has no more interfaces and it's not the backbone
            if not interfaces_list and area_id != '0.0.0.0':
                continue  # Skip adding this area

        updated_areas.append(area_copy)

    # Add interface back to correct area
    correct_area_found = False
    for area_config in updated_areas:
        if area_config.get('area') == correct_area:
            correct_area_found = True
            if 'interfaces' not in area_config:
                area_config['interfaces'] = []
            if interface not in area_config['interfaces']:
                area_config['interfaces'].append(interface)
            break

    if not correct_area_found:
        # Recreate the correct area if it was removed
        updated_areas.append({
            'area': correct_area,
            'type': 'backbone' if correct_area == '0.0.0.0' else 'standard',
            'interfaces': [interface]
        })

    routers[target_router_idx]['protocols']['ospf']['areas'] = updated_areas

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')

    if success:
        logger.info(
            f"[operator-mode] OSPF_AREA_MISMATCH restore: moved {interface} back to area {correct_area} "
            f"on {router} in {underlay_name}"
        )
        return {'success': True}
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD during restoration'}


# =============================================================================
# OSPF_COST_INFLATION — VyOSUnderlay router ospf interface cost
# Fault 9: Set OSPF cost to 65535 on a core link. Adjacency stays Full.
# =============================================================================

async def _inject_ospf_cost_inflation(name, namespace, router, interface, parameters, write_mode='direct'):
    """
    Inject OSPF cost inflation by adding an interface_costs entry in VyOSUnderlay.
    The ospf cost is set to 65535 (legal value — no alarms fire).
    """
    ospf_cost = parameters.get('ospf_cost', 65535)

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = copy.deepcopy(underlay_body.get('spec', {}).get('routers', []))

    target_router_idx = None
    original_ospf_cost = 1  # OSPF default cost

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            target_router_idx = r_idx
            # Check if there's already an interface cost configured
            existing_costs = r.get('protocols', {}).get('ospf', {}).get('interface_costs', [])
            for ic in existing_costs:
                if ic.get('interface') == interface:
                    original_ospf_cost = ic.get('cost', 1)
                    break
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    # Add or update interface_costs for this interface
    ospf_config = routers[target_router_idx].setdefault('protocols', {}).setdefault('ospf', {})
    interface_costs = ospf_config.get('interface_costs', [])

    # Remove existing cost for this interface if present
    interface_costs = [ic for ic in interface_costs if ic.get('interface') != interface]
    interface_costs.append({'interface': interface, 'cost': ospf_cost})
    ospf_config['interface_costs'] = interface_costs

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')

    if success:
        logger.info(f"[operator-mode] OSPF_COST_INFLATION: set cost={ospf_cost} on {router}/{interface} in {underlay_name}")
        return {
            'success': True,
            'original_state': {
                'original_ospf_cost': original_ospf_cost,
                'interface': interface,
                'underlay_name': underlay_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_ospf_cost_inflation(name, namespace, router, interface, original_state, parameters, write_mode='direct'):
    original_ospf_cost = original_state.get('original_ospf_cost', 1)
    underlay_name = original_state.get('underlay_name')

    # Read what we injected from spec.parameters
    injected_ospf_cost = parameters.get('ospf_cost', 65535)

    # Fallback: auto-discover underlay_name if missing from original_state
    if not underlay_name:
        logger.warning(
            f"[operator-mode] OSPF_COST_INFLATION restore: original_state missing underlay_name "
            f"for '{name}' — auto-discovering VyOSUnderlay CRD containing router {router}"
        )
        all_underlays = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
        for underlay_candidate in all_underlays:
            for r in underlay_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    underlay_name = underlay_candidate['metadata']['name']
                    logger.info(
                        f"[operator-mode] OSPF_COST_INFLATION restore: "
                        f"auto-discovered underlay_name='{underlay_name}'"
                    )
                    break
            if underlay_name:
                break

    if not underlay_name:
        return {
            'success': False,
            'error': f'original_state missing underlay_name and no VyOSUnderlay CRD found containing router {router}'
        }

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    # Check if the fault is still present before restoring
    # Read current OSPF cost from the CR
    current_cost = None
    current_routers = underlay.get('spec', {}).get('routers', [])
    for r in current_routers:
        if r.get('name') == router:
            interface_costs = r.get('protocols', {}).get('ospf', {}).get('interface_costs', [])
            for ic in interface_costs:
                if ic.get('interface') == interface:
                    current_cost = ic.get('cost')
                    break
            break

    # If current cost doesn't match what we injected, skip restoration (manual remediation detected)
    if current_cost != injected_ospf_cost:
        logger.info(
            f"[operator-mode] OSPF_COST_INFLATION restoration skipped for {router}/{interface}: "
            f"current cost ({current_cost}) differs from injected cost ({injected_ospf_cost} from parameters). "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active, proceed with restoration
    routers = copy.deepcopy(current_routers)
    for r in routers:
        if r.get('name') == router:
            ospf_config = r.get('protocols', {}).get('ospf', {})
            interface_costs = ospf_config.get('interface_costs', [])
            # Remove the inflated cost entry
            interface_costs = [ic for ic in interface_costs if ic.get('interface') != interface]
            # Restore original cost only if it was non-default
            if original_ospf_cost != 1:
                interface_costs.append({'interface': interface, 'cost': original_ospf_cost})
            ospf_config['interface_costs'] = interface_costs
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch, write_mode=write_mode, kind='VyOSUnderlay')
    return {'success': success, 'error': None if success else 'Failed to restore VyOSUnderlay CRD'}


# =============================================================================
# VRF_RT_MISCONFIGURATION — VyOSL3VPN vrfs[*].rt_import
# Fault 4: Change import RT to non-matching community. Zero alarms.
# =============================================================================

async def _inject_vrf_rt_misconfiguration(name, namespace, router, vrf, parameters, write_mode='direct'):
    """
    Inject VRF RT misconfiguration by changing rt_import in the VyOSL3VPN CRD.
    """
    wrong_rt = parameters.get('wrong_rt', '65035:9999')
    correct_rt = parameters.get('correct_rt', '65035:1030')

    l3vpn = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
    if not l3vpn:
        return {'success': False, 'error': 'VyOSL3VPN CRD not found in namespace'}

    l3vpn_body = l3vpn[0]
    l3vpn_name = l3vpn_body['metadata']['name']
    routers = copy.deepcopy(l3vpn_body.get('spec', {}).get('routers', []))

    original_rt_import = None
    target_router_idx = None
    target_vrf_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            for v_idx, v in enumerate(r.get('vrfs', [])):
                if v.get('name') == vrf:
                    original_rt_import = list(v.get('rt_import', []))
                    target_router_idx = r_idx
                    target_vrf_idx = v_idx
                    break
        if target_router_idx is not None:
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' VRF '{vrf}' not found in VyOSL3VPN"}

    # Replace correct_rt with wrong_rt in rt_import list
    new_rt_import = [rt if rt != correct_rt else wrong_rt for rt in original_rt_import]
    if correct_rt not in original_rt_import:
        # If correct_rt wasn't there, just set wrong_rt
        new_rt_import = [wrong_rt]

    routers[target_router_idx]['vrfs'][target_vrf_idx]['rt_import'] = new_rt_import

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch, write_mode=write_mode, kind='VyOSL3VPN')

    if success:
        logger.info(f"[operator-mode] VRF_RT_MISCONFIGURATION: changed rt_import from {original_rt_import} to {new_rt_import} on {router}/{vrf}")
        return {
            'success': True,
            'original_state': {
                'original_rt_import': original_rt_import,
                'correct_rt': correct_rt,
                'wrong_rt': wrong_rt,
                'l3vpn_name': l3vpn_name,
                'vrf': vrf,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSL3VPN CRD'}


async def _restore_vrf_rt_misconfiguration(name, namespace, router, vrf, original_state,
                                            parameters: Optional[Dict[str, Any]] = None, write_mode='direct'):
    """
    Restore VRF RT misconfiguration by patching rt_import back to the correct value.

    Resilient to missing original_state (status patch race on fast delete):
    - If l3vpn_name is missing, auto-discovers it by listing all VyOSL3VPN CRDs
      and finding the one containing the target router/VRF.
    - If original_rt_import is missing, reconstructs it from correct_rt in
      original_state or spec parameters.
    """
    if parameters is None:
        parameters = {}

    original_rt_import = original_state.get('original_rt_import', [])
    l3vpn_name = original_state.get('l3vpn_name')

    # Fallback: auto-discover l3vpn_name when original_state is empty due to a
    # status patch race (resource deleted very quickly after creation).
    if not l3vpn_name:
        logger.warning(
            f"[operator-mode] VRF_RT_MISCONFIGURATION restore: original_state missing l3vpn_name "
            f"for '{name}' — auto-discovering VyOSL3VPN CRD containing {router}/{vrf}"
        )
        all_l3vpns = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
        for l3vpn_candidate in all_l3vpns:
            for r in l3vpn_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    for v in r.get('vrfs', []):
                        if v.get('name') == vrf:
                            l3vpn_name = l3vpn_candidate['metadata']['name']
                            logger.info(
                                f"[operator-mode] VRF_RT_MISCONFIGURATION restore: "
                                f"auto-discovered l3vpn_name='{l3vpn_name}'"
                            )
                            break
                if l3vpn_name:
                    break
            if l3vpn_name:
                break

    if not l3vpn_name:
        return {
            'success': False,
            'error': (
                f"original_state missing l3vpn_name and no VyOSL3VPN CRD found "
                f"containing router '{router}' VRF '{vrf}' in namespace '{namespace}'"
            )
        }

    # Fallback: reconstruct original_rt_import from correct_rt when missing.
    # correct_rt is saved in original_state by the inject path, and is also
    # available directly from spec.parameters as a last resort.
    if not original_rt_import:
        correct_rt = (
            original_state.get('correct_rt')
            or parameters.get('correct_rt', '')
        )
        if correct_rt:
            original_rt_import = [correct_rt]
            logger.warning(
                f"[operator-mode] VRF_RT_MISCONFIGURATION restore: original_rt_import missing "
                f"for '{name}' — reconstructed from correct_rt='{correct_rt}'"
            )
        else:
            return {
                'success': False,
                'error': (
                    "original_state missing original_rt_import and correct_rt — "
                    "cannot determine what RT to restore. Manual remediation required."
                )
            }

    l3vpn = await _get_crd_by_name(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name)
    if not l3vpn:
        return {'success': False, 'error': f'VyOSL3VPN {l3vpn_name} not found'}

    # Check if the fault is still present before restoring
    # Read what we injected from spec.parameters
    wrong_rt = parameters.get('wrong_rt', '65035:9999')

    # Read current rt_import from the CR
    current_rt_import = None
    current_routers = l3vpn.get('spec', {}).get('routers', [])
    for r in current_routers:
        if r.get('name') == router:
            for v in r.get('vrfs', []):
                if v.get('name') == vrf:
                    current_rt_import = v.get('rt_import', [])
                    break
            break

    # If current rt_import doesn't contain the wrong_rt, skip restoration (manual remediation detected)
    if current_rt_import is not None and wrong_rt not in current_rt_import:
        logger.info(
            f"[operator-mode] VRF_RT_MISCONFIGURATION restoration skipped for {router}/{vrf}: "
            f"current rt_import ({current_rt_import}) does not contain injected wrong RT ({wrong_rt} from parameters). "
            f"Manual remediation detected."
        )
        return {'success': True, 'remediation': 'skipped'}

    # Fault still active, proceed with restoration
    routers = copy.deepcopy(current_routers)
    for r in routers:
        if r.get('name') == router:
            for v in r.get('vrfs', []):
                if v.get('name') == vrf:
                    v['rt_import'] = original_rt_import
                    break
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch, write_mode=write_mode, kind='VyOSL3VPN')
    if success:
        logger.info(
            f"[operator-mode] VRF_RT_MISCONFIGURATION: restored rt_import={original_rt_import} "
            f"on {router}/{vrf} in {l3vpn_name}"
        )
    return {'success': success, 'error': None if success else 'Failed to restore VyOSL3VPN CRD'}


# =============================================================================
# Kubernetes CRD helpers
# =============================================================================

async def _get_crd(namespace: str, plural: str, group: str, version: str):
    """List all CRDs of the given type in the namespace. Returns list of body dicts."""
    loop = asyncio.get_event_loop()

    def _list():
        try:
            client = kubernetes.client.CustomObjectsApi()
            result = client.list_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural
            )
            return result.get('items', [])
        except Exception as e:
            logger.error(f"Failed to list {plural} in {namespace}: {e}")
            return []

    return await loop.run_in_executor(None, _list)


async def _get_crd_by_name(namespace: str, plural: str, group: str, version: str, name: str):
    """Get a single CRD by name. Returns body dict or None."""
    loop = asyncio.get_event_loop()

    def _get():
        try:
            client = kubernetes.client.CustomObjectsApi()
            return client.get_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural, name=name
            )
        except Exception as e:
            logger.error(f"Failed to get {plural}/{name} in {namespace}: {e}")
            return None

    return await loop.run_in_executor(None, _get)


async def _patch_crd_via_git(namespace: str, kind: str, name: str, patch: dict, repo_name: str = 'network') -> bool:
    """
    Patch a CRD by committing changes to git instead of patching k8s directly.

    Args:
        namespace: k8s namespace (used for logging, not needed for git path)
        kind: k8s CRD kind (e.g., "VyOSInfrastructure", "VyOSUnderlay", "VyOSL3VPN")
        name: k8s resource name
        patch: Patch dict to apply to the CR spec
        repo_name: Gitea repository name (default: "network")

    Returns:
        bool: True on success, False on failure
    """
    loop = asyncio.get_event_loop()

    def _patch_via_git():
        try:
            # Find the file that actually defines this CR. The old code computed
            # a filename from the naming convention, which only holds for files
            # this operator created itself — a repo seeded from examples/ keeps
            # VyOSUnderlay/l3vpn-underlay in transport/underlay.yaml, so every
            # git-mode injection failed on a seeded network.
            filename = find_crd_git_path(repo_name, kind, name)
            if not filename:
                logger.error(
                    f"[git-mode] No file in the {repo_name} repo defines {kind}/{name} — "
                    f"cannot patch. Is the CR managed by ConfigSync from this repo?"
                )
                return False
            logger.info(f"[git-mode] Patching {kind}/{name} via git file {filename}")

            # Read current YAML from git
            try:
                yaml_content = get_git_file(repo_name, filename)
            except FileNotFoundError:
                logger.error(f"[git-mode] {filename} not found in {repo_name} repo — cannot patch")
                return False

            # Parse YAML. find_crd_git_path() matches inside multi-document
            # files too, so patch the one document that names this CR and write
            # the rest back untouched.
            try:
                docs = [d for d in yaml_lib.safe_load_all(yaml_content)]
            except Exception as e:
                logger.error(f"[git-mode] Failed to parse YAML from {filename}: {e}")
                return False

            cr_dict = None
            for doc in docs:
                if (isinstance(doc, dict) and doc.get('kind') == kind
                        and (doc.get('metadata') or {}).get('name') == name):
                    cr_dict = doc
                    break
            if cr_dict is None:
                logger.error(f"[git-mode] {filename} does not define {kind}/{name}")
                return False

            # Deep-merge patch into spec
            if 'spec' not in cr_dict:
                cr_dict['spec'] = {}
            _deep_merge(cr_dict['spec'], patch.get('spec', {}))

            # Convert back to YAML
            if len(docs) == 1:
                updated_yaml = yaml_lib.dump(cr_dict, default_flow_style=False, sort_keys=False)
            else:
                updated_yaml = yaml_lib.dump_all(docs, default_flow_style=False, sort_keys=False)

            # Commit to git
            commit_message = f"NetworkFailure operator: patch {kind}/{name} in {namespace}"
            success = commit_git_file(filename, commit_message, updated_yaml, repo_name=repo_name)

            if success:
                logger.info(
                    f"[git-mode] Successfully committed {filename} to {repo_name}. "
                    f"ConfigSync will apply the change to k8s within seconds."
                )
            return success

        except Exception as e:
            logger.error(f"[git-mode] Failed to patch {kind}/{name} via git: {e}")
            return False

    return await loop.run_in_executor(None, _patch_via_git)


def _deep_merge(target: dict, source: dict):
    """
    Deep-merge source dict into target dict (modifies target in-place).

    For each key in source:
    - If the value is a dict and target[key] is also a dict, recurse
    - Otherwise, target[key] = source[key]
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


async def _patch_crd(namespace: str, plural: str, group: str, version: str, name: str, patch: dict, write_mode: str = 'direct', kind: str = None) -> bool:
    """
    Patch a CRD using either direct k8s API or git commit (based on write_mode).

    Args:
        namespace: k8s namespace
        plural: k8s CRD plural (e.g., "vyosinfrastructures")
        group: k8s CRD group (e.g., "google.dev")
        version: k8s CRD version (e.g., "v1")
        name: k8s resource name
        patch: Patch dict to apply
        write_mode: "direct" (patch k8s API) or "git" (commit to git)
        kind: k8s CRD kind (required for git mode, e.g., "VyOSInfrastructure")

    Returns:
        bool: True on success, False on failure
    """
    if write_mode == 'git':
        if not kind:
            logger.error(f"[git-mode] kind parameter is required for git mode but was not provided")
            return False
        return await _patch_crd_via_git(namespace, kind, name, patch)
    else:
        # Direct mode: patch k8s API (original behavior)
        loop = asyncio.get_event_loop()

        def _patch():
            try:
                client = kubernetes.client.CustomObjectsApi()
                client.patch_namespaced_custom_object(
                    group=group, version=version, namespace=namespace,
                    plural=plural, name=name, body=patch
                )
                return True
            except Exception as e:
                logger.error(f"Failed to patch {plural}/{name} in {namespace}: {e}")
                return False

        return await loop.run_in_executor(None, _patch)
