import asyncio
import json
import os
import subprocess
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based Device Management
#########################################################################

async def create_device(networkvm_ip_address:str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Device using Ansible"""
    logger.info(f"Creating Device: {name}")

    # Extract required fields from spec
    device_name = name
    network_name = spec.get('network_name')
    ip_address = spec.get('ip_address')
    vlan = spec.get('vlan')  # Optional VLAN ID
    gateway = spec.get('gateway')  # Optional gateway IP
    
    # Prepare extra variables for Ansible playbook
    mgmt_ip = spec.get('mgmt_ip', '')
    mgmt_network = spec.get('mgmt_network', '')

    image = spec.get('image', 'device:latest')

    extravars = {
        'operation': 'create',
        'device_name': device_name,
        'network_name': network_name,
        'ip_address': ip_address,
        'mgmt_ip': mgmt_ip,
        'mgmt_network': mgmt_network,
        'image': image,
        'extra_interfaces': spec.get('extra_interfaces', []),
    }

    # Add VLAN if specified
    if vlan:
        extravars['vlan'] = vlan
    else:
        extravars['vlan'] = ''

    # Add gateway if specified
    if gateway:
        extravars['gateway'] = gateway
    else:
        extravars['gateway'] = ''

    result = await _run_ansible_playbook(networkvm_ip_address, 'device.yaml', extravars)
    logger.info(f"Device creation result: {result}")

    if result['success']:
        return {
            'success': True,
            # Pass mgmt_ip back so lifecycle.py can write it into Device.status
            'mgmt_ip': mgmt_ip,
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during Device creation')
        }

async def delete_device(networkvm_ip_address:str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Device container using Ansible"""
    logger.info(f"Deleting Device: {name}")

    extravars = {
        'operation': 'delete',
        'device_name': name,
        'network_name': spec.get('network_name'),
        'ip_address': spec.get('ip_address'),
        # Required so the playbook's mgmt veth cleanup condition works
        'mgmt_ip': spec.get('mgmt_ip', ''),
        'mgmt_network': spec.get('mgmt_network', ''),
        'extra_interfaces': spec.get('extra_interfaces', []),
    }
    
    # Add VLAN if specified
    vlan = spec.get('vlan')
    if vlan:
        extravars['vlan'] = vlan

    result = await _run_ansible_playbook(networkvm_ip_address,'device.yaml', extravars)

    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }


#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(networkvm_ip_address: str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables.

    Uses subprocess.run() (fork+exec) instead of ansible_runner.run_async()
    (fork-only multiprocessing) to avoid the gRPC ev_poll_posix / wakeup_fd
    crash that occurs when gRPC's eventfds are inherited into a forked Python
    worker.
    """

    # Get the Ansible semaphore for throttling
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()

    # Inventory in the format expected by ansible-playbook -i <file>
    inventory = {
        'all': {
            'hosts': {
                'monitor': {
                    'ansible_host': networkvm_ip_address,
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
    # Devices do more work than a bridge create (docker run + veth setup +
    # traffic-agent startup), so allow slightly more time than the 60s used
    # by linuxnetwork/vyosrouter.  Without this, a hung SSH or slow docker
    # command parks the executor thread forever, holding a semaphore slot and
    # leaving the Device stuck in "Creating" indefinitely.
    ANSIBLE_TIMEOUT_SECONDS = 120

    def run_ansible(temp_dir):
        """Write temp files and call ansible-playbook via subprocess (fork+exec)."""
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'infrastructure/device/playbooks', playbook)

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
        temp_dir = tempfile.mkdtemp(prefix="ansible_device_")
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
                if result.stdout:
                    try:
                        output = json.loads(result.stdout)
                        for play in output.get('plays', []):
                            for task in play.get('tasks', []):
                                for _host, host_result in task.get('hosts', {}).items():
                                    for key in ('network_id', 'created_at', 'exists', 'network_info'):
                                        if key in host_result:
                                            result_data[key] = host_result[key]
                    except Exception as parse_err:
                        logger.warning(f"Could not parse ansible JSON output: {parse_err}")

                return {
                    'success': True,
                    **result_data
                }
            else:
                # Extract error information — check unreachable first (transient), then task failures.
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
                                        error_msg = f"exit code 124: command timed out in task '{task_name}'"
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
