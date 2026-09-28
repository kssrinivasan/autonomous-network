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
import json as _json
import os
import subprocess
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based Linux Network Management
#########################################################################

async def create_linux_network(ip_address:str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Linux network using Ansible"""
    logger.info(f"Creating Linux network: {spec['name']}")

    # Prepare extra variables for Ansible playbook
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
        'bandwidth': spec.get('bandwidth'),
        'gateway_ip': spec.get('gateway'),
        'operation': 'create'
    }

    result = await _run_ansible_playbook(ip_address, 'create_network.yaml', extravars)
    logger.info(f"Linux network creation result: {result}")

    if result['success']:
        # Capture the default interface
        default_interface = result.get('default_interface', 'unknown')
        interface_ip = result.get('interface_ip', 'unknown')
        default_gateway = result.get('default_gateway', 'unknown')
        return {
            'success': True,
            'default_interface': default_interface,
            'interface_ip': interface_ip,
            'default_gateway': default_gateway
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during network creation')
        }

async def delete_linux_network(ip_address:str, spec: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Linux network using Ansible"""
    logger.info(f"Deleting Linux network: {spec.get('name')}")
    
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
    }

    # If network_type is management, pass the interface from status
    if spec.get('network_type') == 'management' and status:
        extravars['parent_interface'] = status.get('interface')
        extravars['interface_ip'] = status.get('interface_ip')
        extravars['default_gateway'] = status.get('gateway')

    result = await _run_ansible_playbook(ip_address, 'delete_network.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }


#########################################################################
# Ansible Execution Helper
#########################################################################
    
async def _run_ansible_playbook(ip_address: str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables.

    Uses subprocess.run() to invoke ansible-playbook directly (fork+exec) rather
    than ansible_runner.run_async() (fork-only multiprocessing).  The fork+exec
    path means the child process image is replaced by ansible-playbook before any
    gRPC C-extension cleanup code runs, which completely avoids the
    ev_poll_posix / wakeup_fd crash that occurs when gRPC's internal eventfds are
    inherited into a forked-but-not-exec'd Python worker.
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
    # Prevents a hung SSH connection or stalled command from keeping
    # a resource permanently stuck in "Creating".
    ANSIBLE_TIMEOUT_SECONDS = 120

    def run_ansible(temp_dir):
        """Write temp files and call ansible-playbook via subprocess (fork+exec)."""
        import shutil

        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'infrastructure/linuxnetwork/playbooks', playbook)

        with open(inv_file, 'w') as fh:
            _json.dump(inventory, fh)
        with open(extravars_file, 'w') as fh:
            _json.dump(extravars, fh)

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
        temp_dir = tempfile.mkdtemp(prefix="ansible_network_")
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
                # Parse the JSON callback output and extract ansible_facts from
                # every task result, exactly mirroring the runner.events logic.
                result_data = {}
                _FACT_KEYS = (
                    'default_interface', 'default_gateway', 'interface_ip',
                )
                if result.stdout:
                    try:
                        output = _json.loads(result.stdout)
                        for play in output.get('plays', []):
                            for task in play.get('tasks', []):
                                for _host, host_result in task.get('hosts', {}).items():
                                    facts = host_result.get('ansible_facts', {})
                                    for key in _FACT_KEYS:
                                        if key in facts:
                                            result_data[key] = facts[key]
                                            logger.debug(f"Captured {key}: {result_data[key]}")
                    except Exception as parse_err:
                        logger.warning(f"Could not parse ansible JSON output: {parse_err}")

                logger.info(f"Final extracted data: {result_data}")
                return {
                    'success': True,
                    **result_data
                }
            else:
                # Build a useful error message from stderr / stdout.
                error_msg = f"Ansible playbook failed with rc={result.returncode}"
                logger.error(
                    f"Ansible playbook '{playbook}' failed (rc={result.returncode})"
                )

                # Try to parse the JSON output for task-level failure details
                try:
                    output = _json.loads(result.stdout)
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
                                        or host_result.get('stdout', '')
                                        or str(host_result)
                                    )
                                    rc = host_result.get('rc')
                                    if rc == 124 or 'Killed' in msg or 'exit code 124' in msg:
                                        error_msg = f"exit code 124: command timed out in task '{task_name}'"
                                    else:
                                        error_msg = (
                                            f"Task '{task_name}' failed: {msg}"
                                            if task_name else msg
                                        )
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
