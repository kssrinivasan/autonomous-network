import asyncio
import json
import logging
import os
import subprocess
from typing import Any, Dict, List

import utils.constants as constants
from utils.ansible import get_ansible_semaphore, log_ansible_output

logger = logging.getLogger(__name__)

ANSIBLE_TIMEOUT_SECONDS = 120


async def provision_subscribers(
    networkvm_ip: str,
    webui_ip: str,
    mcc: str,
    mnc: str,
    subscribers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """POST each subscriber to the free5gc WebUI API via Ansible on the Network VM."""
    extravars = {
        'webui_address': f'http://{webui_ip}:5000',
        'mcc': mcc,
        'mnc': mnc,
        'subscribers': [
            {
                'imsi': s['imsi'],
                'key': s['key'],
                'opc': s['opc'],
                'amf': s.get('amf', '8000'),
                'apn': s.get('apn', 'internet'),
                'sst': s.get('sst', 1),
                'sd': s.get('sd', '010203'),
                'msisdn': s.get('msisdn', ''),
                'sqn': s.get('sqn', '000000000020'),
            }
            for s in subscribers
        ],
    }
    return await _run_ansible_playbook(networkvm_ip, 'provision_subscribers.yaml', extravars)


async def _run_ansible_playbook(networkvm_ip: str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
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
        import shutil
        import tempfile
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'core/core/playbooks', playbook)

        with open(inv_file, 'w') as fh:
            json.dump(inventory, fh)
        with open(extravars_file, 'w') as fh:
            json.dump(extravars, fh)

        env = os.environ.copy()
        env['ANSIBLE_STDOUT_CALLBACK'] = 'json'
        env['ANSIBLE_FORCE_COLOR'] = '0'

        try:
            result = subprocess.run(
                ['ansible-playbook', '-i', inv_file, playbook_path,
                 '--extra-vars', f'@{extravars_file}'],
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
        import shutil
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix="ansible_core_")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_ansible, temp_dir)

            if result is None:
                return {'success': False, 'error': 'Ansible timed out or failed to launch'}

            log_ansible_output(playbook, result.stdout, logger)

            if result.returncode == 0:
                return {'success': True}

            error_msg = f"Ansible playbook failed with rc={result.returncode}"
            try:
                output = json.loads(result.stdout)
                for play in output.get('plays', []):
                    for task in play.get('tasks', []):
                        for _host, host_result in task.get('hosts', {}).items():
                            if not host_result.get('failed'):
                                continue
                            # Unwrap loop results for better error detail
                            loop_results = host_result.get('results', [])
                            if loop_results:
                                for item in loop_results:
                                    if item.get('failed'):
                                        status = item.get('status', '')
                                        url = item.get('url', '')
                                        msg = item.get('msg', '') or item.get('json', '')
                                        error_msg = f"HTTP {status} for {url}: {msg}" if status else str(item)
                                        raise StopIteration
                            msg = (host_result.get('msg', '')
                                   or host_result.get('stderr', '')
                                   or str(host_result))
                            error_msg = msg or error_msg
                            raise StopIteration
            except StopIteration:
                pass
            except Exception:
                pass

            logger.error(f"Ansible playbook failed: {error_msg}")
            return {'success': False, 'error': error_msg}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
