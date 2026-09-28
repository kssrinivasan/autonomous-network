import asyncio
import json
import os
import subprocess
import utils.constants as constants
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

ANSIBLE_TIMEOUT_SECONDS = 180

_cellsite_locks: Dict[str, asyncio.Lock] = {}


def _get_cellsite_lock(name: str) -> asyncio.Lock:
    lock = _cellsite_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _cellsite_locks[name] = lock
    return lock


async def create_cellsite(networkvm_ip: str, name: str, spec: Dict[str, Any],
                          gnb_ip: str, amf_ip: str) -> Dict[str, Any]:
    """Start nr-gnb and nr-ue processes in a Device container."""
    gnb_spec = spec.get('gnb', {})
    ues = spec.get('ues', [])
    container_name = spec.get('container_name', '')

    slices = gnb_spec.get('slices', [{'sst': 1, 'sd': '010203'}])

    extravars = {
        'operation': 'create',
        'container_name': container_name,
        'gnb_ip': gnb_ip,
        'amf_ip': amf_ip,
        'amf_port': gnb_spec.get('amf_port', 38412),
        'mcc': gnb_spec.get('mcc', '208'),
        'mnc': gnb_spec.get('mnc', '93'),
        'tac': gnb_spec.get('tac', 1),
        'nci': gnb_spec.get('nci', '0x000000010'),
        'gnb_id_length': gnb_spec.get('gnbIdLength', 32),
        'slices': slices,
        'ues': [
            {
                'imsi': ue.get('imsi'),
                'key': ue.get('key'),
                'opc': ue.get('opc'),
                'amf': ue.get('amf', '8000'),
                'apn': ue.get('apn', 'internet'),
                'sst': ue.get('sst', 1),
                'sd': ue.get('sd', ''),
                'enabled': ue.get('enabled', True),
            }
            for ue in ues
        ],
    }

    lock = _get_cellsite_lock(name)
    if lock.locked():
        logger.warning(
            f"CellSite {name}: create already in progress (concurrent invocation) — waiting"
        )
    async with lock:
        result = await _run_ansible_playbook(networkvm_ip, 'cellsite.yaml', extravars)
    logger.info(f"CellSite {name} create result: {result}")
    return result


async def delete_cellsite(networkvm_ip: str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Stop nr-gnb and nr-ue processes in the Device container."""
    container_name = spec.get('container_name', '')

    extravars = {
        'operation': 'delete',
        'container_name': container_name,
        'gnb_ip': '',
        'amf_ip': '',
        'amf_port': 38412,
        'mcc': '',
        'mnc': '',
        'tac': 1,
        'nci': '',
        'gnb_id_length': 32,
        'slices': [],
        'ues': [],
    }

    return await _run_ansible_playbook(networkvm_ip, 'cellsite.yaml', extravars)


async def _run_ansible_playbook(networkvm_ip: str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook via subprocess (fork+exec)."""
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()

    inventory = {
        'all': {
            'hosts': {
                'monitor': {
                    'ansible_host': networkvm_ip,
                    'ansible_user': os.getenv("GOOGLE_VM_USER"),
                    'ansible_connection': 'ssh',
                    'ansible_ssh_private_key_file': constants.basedir + '/google-compute',
                    'ansible_ssh_common_args': '-o StrictHostKeyChecking=no',
                }
            }
        }
    }

    logger.info(f"Running Ansible playbook: {playbook}")

    def run_ansible(temp_dir):
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'radio/playbooks', playbook)

        with open(inv_file, 'w') as fh:
            json.dump(inventory, fh)
        with open(extravars_file, 'w') as fh:
            json.dump(extravars, fh)

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
            logger.error(f"Ansible playbook '{playbook}' timed out after {ANSIBLE_TIMEOUT_SECONDS}s")
            return None
        except Exception as e:
            logger.error(f"Ansible execution failed: {e}")
            return None

    async with semaphore:
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="ansible_radio_")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_ansible, temp_dir)

            if result is None:
                return {'success': False, 'error': 'Ansible playbook timed out or failed to launch'}

            from utils.ansible import log_ansible_output
            log_ansible_output(playbook, result.stdout, logger)

            if result.returncode == 0:
                return {'success': True}

            error_msg = f"Ansible playbook failed with rc={result.returncode}"
            try:
                output = json.loads(result.stdout)
                for play in output.get('plays', []):
                    for task in play.get('tasks', []):
                        task_name = task.get('task', {}).get('name', '')
                        for _host, host_result in task.get('hosts', {}).items():
                            if host_result.get('unreachable'):
                                error_msg = f"Host unreachable: {host_result.get('msg', '')}"
                                raise StopIteration
                            if host_result.get('failed') and not host_result.get('ignored'):
                                msg = (
                                    host_result.get('msg', '')
                                    or host_result.get('stderr', '')
                                    or str(host_result)
                                )
                                rc = host_result.get('rc')
                                if 'amf' in task_name.lower():
                                    error_msg = f"AMF unreachable — transport network not ready"
                                elif rc == 124 or 'Killed' in msg:
                                    error_msg = f"exit code 124: timed out in '{task_name}'"
                                else:
                                    error_msg = msg or error_msg
                                raise StopIteration
            except StopIteration:
                pass
            except Exception:
                pass

            if error_msg.startswith("Ansible playbook failed with rc="):
                raw = (result.stderr or result.stdout or '').strip()
                if raw:
                    error_msg = f"Ansible output: {raw[-800:]}"

            logger.error(f"Ansible playbook failed: {error_msg}")
            return {'success': False, 'error': error_msg}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
