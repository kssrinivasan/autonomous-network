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
import json
import os
import subprocess
import utils.constants as constants
import logging
from typing import Dict, Any
import kubernetes

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based VyOS Router Management
#########################################################################

async def create_vyos_router(ip_address:str, router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Create a VyOS router container using Ansible"""
    logger.info(f"Creating VyOS router: {router_config['name']}")
    
    # Prepare extra variables for Ansible playbook
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'vyos_image': router_config.get('image', 'vyos:1.5'),
        'source_network': router_config.get('source_network'),
        'interfaces': router_config.get('interfaces'),
        'operation': 'create'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_management.yaml', extravars)
    
    if result['success']:
        return {
            'success': True,
            'container_id': result.get('container_id'),
            'management_ip': result.get('management_ip'),
            'applied_config': result.get('applied_config')
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during router creation')
        }

async def configure_vyos_router(ip_address:str,router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Configure a VyOS router using Ansible"""
    logger.info(f"Configuring VyOS router: {router_config['name']}")
    
    # Prepare configuration data
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'interfaces': router_config.get('interfaces', []),
        'vrfs': router_config.get('vrfs', []),
        'protocols': router_config.get('protocols', {}),
        'services': router_config.get('services', {}),
        'qos_policies': router_config.get('qos', {}).get('policies', []),
        'firewall_policies': router_config.get('firewall', {}).get('policies', []),
        'operation': 'configure'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_configuration.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None,
        'applied_config': result.get('applied_config')
    }

async def update_vyos_router(ip_address:str, router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Update a VyOS router configuration using Ansible"""
    logger.info(f"Updating VyOS router: {router_config['name']}")
    
    # Prepare updated configuration data
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'interfaces': router_config.get('interfaces', []),
        'vrfs': router_config.get('vrfs', []),
        'protocols': router_config.get('protocols', {}),
        'services': router_config.get('services', {}),
        'qos_policies': router_config.get('qos', {}).get('policies', []),
        'firewall_policies': router_config.get('firewall', {}).get('policies', []),
        'operation': 'update'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_configuration.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None,
        'applied_config': result.get('applied_config')
    }

async def delete_vyos_router(ip_address:str, router_name: str, interfaces: list = None) -> Dict[str, Any]:
    """Delete a VyOS router container using Ansible"""
    logger.info(f"Deleting VyOS router: {router_name}")
    
    extravars = {
        'router_name': router_name,
        'interfaces': interfaces or [],
        'operation': 'delete'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_management.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }

#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(ip_address: str, playbook: str,
                                extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables.

    Uses subprocess.run() (fork+exec) instead of ansible_runner.run_async()
    (fork-only multiprocessing) to avoid the gRPC ev_poll_posix / wakeup_fd
    crash that occurs when gRPC's eventfds are inherited into a forked Python
    worker.
    """

    # Get the appropriate Ansible semaphore for throttling
    from utils.ansible import get_ansible_operational_semaphore
    semaphore = get_ansible_operational_semaphore()

    # Inventory in the format expected by ansible-playbook -i <file>
    inventory = {
        'all': {
            'hosts': {
                'monitor': {
                    'ansible_host': ip_address,
                    'ansible_user': os.getenv("GOOGLE_VM_USER"),
                    'ansible_connection': 'ssh',
                    'ansible_ssh_private_key_file': constants.basedir + '/google-compute',
                    'ansible_ssh_common_args': '-o StrictHostKeyChecking=no',
                }
            }
        }
    }

    logger.info(f"Running Ansible playbook: {playbook}")
    logger.info(f"Extra vars: {extravars}")

    # Hard timeout (seconds) for any single Ansible playbook run.
    # Prevents a hung SSH connection or stalled Docker command from keeping
    # a resource permanently stuck in "Creating" or "Configuring".
    ANSIBLE_TIMEOUT_SECONDS = 120

    def run_ansible(temp_dir):
        """Write temp files and call ansible-playbook via subprocess (fork+exec)."""
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'transport/vyosrouter/playbooks', playbook)

        with open(inv_file, 'w') as fh:
            json.dump(inventory, fh)
        with open(extravars_file, 'w') as fh:
            json.dump(extravars, fh)

        # Use the json stdout callback so we can parse facts from the output.
        # ANSIBLE_FORCE_COLOR=0 keeps the JSON clean (no ANSI escape codes).
        env = os.environ.copy()
        env['ANSIBLE_STDOUT_CALLBACK'] = 'json'
        env['ANSIBLE_FORCE_COLOR'] = '0'

        try:
            result = subprocess.run(
                [
                    'ansible-playbook',
                    '-i', inv_file,
                    playbook_path,
                    '--extra-vars', f'@{extravars_file}',
                ],
                capture_output=True,
                text=True,
                timeout=ANSIBLE_TIMEOUT_SECONDS,
                env=env,
            )
            return result
        except subprocess.TimeoutExpired:
            logger.error(
                f"Ansible playbook '{playbook}' did not finish within "
                f"{ANSIBLE_TIMEOUT_SECONDS}s — treating as failure"
            )
            return None
        except Exception as e:
            logger.error(f"Ansible execution failed: {e}")
            return None

    # Throttle concurrent Ansible executions using semaphore
    async with semaphore:
        logger.info(f"Acquired Ansible semaphore for playbook: {playbook}")
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="ansible_vyosrouter_")
        try:
            # Execute in thread pool to avoid blocking the async event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_ansible, temp_dir)

            if result is None:
                return {
                    'success': False,
                    'error': 'Failed to execute Ansible playbook (timeout or launch error)'
                }

            # Log all task results (changed→INFO, ok/skipped→DEBUG, failures→ERROR)
            from utils.ansible import log_ansible_output
            log_ansible_output(playbook, result.stdout, logger)

            if result.returncode == 0:
                # Parse the JSON callback output and extract results from every task,
                # mirroring the runner.events logic from the old ansible_runner path.
                result_data = {}
                if result.stdout and result.stdout.strip():
                    try:
                        output = json.loads(result.stdout)
                        for play in output.get('plays', []):
                            for task in play.get('tasks', []):
                                task_name = task.get('task', {}).get('name', '')
                                for _host, host_result in task.get('hosts', {}).items():
                                    # Applied config from the specific capture task
                                    if task_name == "Capture VyOS configuration":
                                        result_data['applied_config'] = host_result.get('vyos_config_commands', '')

                                    # Direct result fields
                                    for key in ('container_id', 'management_ip'):
                                        if key in host_result:
                                            result_data[key] = host_result[key]
                    except Exception as parse_err:
                        logger.debug(
                            f"Could not parse ansible JSON output: {parse_err}. "
                            f"Raw stdout (first 200 chars): {result.stdout[:200]!r}"
                        )

                logger.info(f"Extracted VyOS result data: {list(result_data.keys())}")
                return {
                    'success': True,
                    **result_data
                }
            else:
                error_msg = f"Ansible playbook failed with rc={result.returncode}"
                logger.error(f"Ansible playbook '{playbook}' failed (rc={result.returncode})")

                # Try to parse the JSON output for task-level failure details
                try:
                    output = json.loads(result.stdout)
                    for play in output.get('plays', []):
                        for task in play.get('tasks', []):
                            task_name = task.get('task', {}).get('name', '')
                            for _host, host_result in task.get('hosts', {}).items():
                                if host_result.get('unreachable'):
                                    msg = host_result.get('msg', 'unknown host')
                                    error_msg = f"Host unreachable: {msg}"
                                    raise StopIteration
                                if host_result.get('failed'):
                                    msg = (
                                        host_result.get('msg', '')
                                        or host_result.get('stderr', '')
                                        or str(host_result)
                                    )
                                    rc = host_result.get('rc')
                                    if rc == 124 or 'Killed' in msg or 'exit code 124' in msg:
                                        error_msg = f"exit code 124: vbash timed out applying configuration in task '{task_name}'"
                                    else:
                                        error_msg = msg if msg else error_msg
                                    raise StopIteration
                except StopIteration:
                    pass
                except Exception:
                    pass

                # Fallback: surface raw stderr/stdout tail if still generic
                if error_msg.startswith("Ansible playbook failed with rc="):
                    raw = (result.stderr or result.stdout or '').strip()
                    if raw:
                        error_msg = f"Ansible output: {raw[-800:]}"

                logger.error(f"Ansible playbook execution failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


#########################################################################
# LinuxNetwork Validation
#########################################################################

async def check_linux_networks_ready(interfaces: list, namespace: str) -> tuple[bool, list]:
    """
    Check if all LinuxNetwork CRs specified in interfaces exist and have status 'Ready'.
    
    Args:
        interfaces: List of interface configurations
        namespace: Kubernetes namespace
    
    Returns:
        tuple: (all_ready: bool, not_ready_networks: list)
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    
    not_ready_networks = []
    
    for interface in interfaces:
        network_name = interface.get('linux_network')
        if network_name:
            try:
                network = api.get(name=network_name, namespace=namespace)
                network_dict = network.to_dict()
                
                status = network_dict.get('status', {})
                phase = status.get('phase', 'Unknown')
                
                if phase != 'Ready':
                    logger.warning(f"LinuxNetwork '{network_name}' is in phase '{phase}', not Ready")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': phase,
                        'message': status.get('message', 'No message')
                    })
                else:
                    logger.info(f"LinuxNetwork '{network_name}' is Ready")

            except kubernetes.client.rest.ApiException as e:
                if e.status == 404:
                    logger.warning(f"LinuxNetwork '{network_name}' not found")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': 'NotFound',
                        'message': 'LinuxNetwork resource does not exist'
                    })
                else:
                    logger.error(f"Error checking LinuxNetwork '{network_name}': {e}")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': 'Error',
                        'message': str(e)
                    })
            except Exception as e:
                logger.error(f"Unexpected error checking LinuxNetwork '{network_name}': {e}")
                not_ready_networks.append({
                    'name': network_name,
                    'phase': 'Error',
                    'message': str(e)
                })
    
    return len(not_ready_networks) == 0, not_ready_networks
