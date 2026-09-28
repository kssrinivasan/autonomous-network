import asyncio
import json
import os
import subprocess
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

VALID_NF_TYPES = {'amf', 'smf', 'upf', 'nrf', 'udr', 'udm', 'ausf', 'nssf', 'pcf', 'n3iwf', 'webui', 'bsf', 'nef', 'chf'}

ANSIBLE_TIMEOUT_SECONDS = 120

# Per-NetworkFunction-name locks to prevent two concurrent Ansible runs for the
# SAME NF (e.g. a watch-stream reconnection re-invoking the create handler while
# the first Ansible run is still in flight, racing ahead of the Kopf-level phase
# guard in lifecycle.py). This is a defense-in-depth measure on top of the
# 'Creating' phase check and the flock inside each playbook's start task — it
# avoids redundantly re-running the whole playbook (config render, NRF/Mongo
# wait loops, etc.) for a resource that's already being processed.
_nf_locks: Dict[str, asyncio.Lock] = {}


def _get_nf_lock(name: str) -> asyncio.Lock:
    lock = _nf_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _nf_locks[name] = lock
    return lock



async def create_nf(networkvm_ip: str, name: str, spec: Dict[str, Any], nf_ip: str, extra_interfaces: list = None) -> Dict[str, Any]:
    """Start a free5gc network function inside an existing Device container."""
    nf_type = spec.get('nf_type', '').lower()
    container_name = spec.get('container_name', '')
    nf_config = spec.get(nf_type) or {}

    # NRF derives its own URI from its IP and configured port
    if nf_type == 'nrf':
        port = nf_config.get('port', 8000)
        nrf_uri = f'http://{nf_ip}:{port}'
    else:
        nrf_uri = nf_config.get('nrfUri', '')

    mongodb_url = nf_config.get('mongodbUri', 'mongodb://db:27017')
    amf_ip = nf_config.get('amfIp', '')
    snssai_infos = nf_config.get('snssaiInfos', []) if nf_type in ('smf', 'amf', 'n3iwf') else []
    plmn_list = nf_config.get('plmnList', [])
    tac_hex = f'{nf_config.get("tac", 1):06x}' if nf_type == 'amf' else '000001'

    # Build UPF list for SMF topology config.
    # FiveGCore lifecycle injects upfList (list of dicts); standalone NF CRs use scalar upfIp.
    upf_list = nf_config.get('upfList', [])
    if not upf_list and nf_config.get('upfIp'):
        upf_ip = nf_config['upfIp']
        upf_list = [{'name': 'UPF', 'nodeID': upf_ip, 'addr': upf_ip}]

    # DNN list for UPF config — derived from SMF snssaiInfos by the FiveGCore lifecycle.
    # Standalone NF CRs may set dnnList directly; fallback to empty (template uses a default).
    upf_dnn_list = nf_config.get('dnnList', []) if nf_type == 'upf' else []

    # For UPF: use the first extra_interface as the dedicated N6 egress.
    # Falls back to eth0 / nf_ip for deployments without a separate data network.
    n6_interface = 'eth0'
    n6_ip = nf_ip
    if nf_type == 'upf' and extra_interfaces:
        n6_interface = 'eth2'
        n6_ip = extra_interfaces[0].get('ip_address', nf_ip)

    metrics_config = spec.get('metrics') or nf_config.get('metrics') or {}
    metrics_enabled = bool(metrics_config.get('enabled', False))
    metrics_port = int(metrics_config.get('port', 9089))
    metrics_scheme = str(metrics_config.get('scheme', 'http'))
    metrics_namespace = str(metrics_config.get('namespace', 'free5gc'))

    extravars = {
        'operation': 'create',
        'nf_type': nf_type,
        'container_name': container_name,
        'nf_ip': nf_ip,
        'nrf_uri': nrf_uri,
        'mongodb_url': mongodb_url,
        'upf_list': upf_list,
        'amf_ip': amf_ip,
        'snssai_infos': snssai_infos,
        'plmn_list': plmn_list,
        'upf_dnn_list': upf_dnn_list,
        'tac_hex': tac_hex,
        'n6_interface': n6_interface,
        'n6_ip': n6_ip,
        'metrics_enabled': metrics_enabled,
        'metrics_port': metrics_port,
        'metrics_scheme': metrics_scheme,
        'metrics_namespace': metrics_namespace,
    }

    playbook = f'{nf_type}.yaml'
    lock = _get_nf_lock(name)
    if lock.locked():
        logger.warning(
            f"NetworkFunction {name}: create already in progress "
            f"(concurrent handler invocation) — waiting for it to finish instead "
            f"of re-running the playbook"
        )
    async with lock:
        result = await _run_ansible_playbook(networkvm_ip, playbook, extravars)
    logger.info(f"NF {nf_type} create result: {result}")
    return result



async def delete_nf(networkvm_ip: str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a free5gc network function inside its Device container."""
    nf_type = spec.get('nf_type', '').lower()
    container_name = spec.get('container_name', '')

    extravars = {
        'operation': 'delete',
        'nf_type': nf_type,
        'container_name': container_name,
        'nf_ip': '',
        'nrf_uri': '',
        'mongodb_url': '',
        'upf_list': [],
        'amf_ip': '',
    }

    playbook = f'{nf_type}.yaml'
    result = await _run_ansible_playbook(networkvm_ip, playbook, extravars)
    return result


async def _run_ansible_playbook(networkvm_ip: str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook via subprocess (fork+exec, not fork-only) to avoid gRPC wakeup_fd crash."""
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

    logger.info(f"Running Ansible playbook: {playbook}, extravars: {extravars}")

    def run_ansible(temp_dir):
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(constants.basedir, 'core/xnf/playbooks', playbook)

        with open(inv_file, 'w') as fh:
            json.dump(inventory, fh)
        with open(extravars_file, 'w') as fh:
            json.dump(extravars, fh)

        env = os.environ.copy()
        env['ANSIBLE_STDOUT_CALLBACK'] = 'json'
        env['ANSIBLE_FORCE_COLOR'] = '0'

        try:
            import time
            pre_run = time.time()
            logger.info(f"[SUBPROCESS-PRE] About to run ansible-playbook for {playbook} at {pre_run}")

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

            post_run = time.time()
            logger.info(f"[SUBPROCESS-POST] Finished ansible-playbook for {playbook} at {post_run}, duration={post_run - pre_run:.3f}s, rc={result.returncode}")

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
        temp_dir = tempfile.mkdtemp(prefix="ansible_nf_")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_ansible, temp_dir)

            if result is None:
                return {'success': False, 'error': 'Ansible playbook timed out or failed to launch'}

            from utils.ansible import log_ansible_output
            log_ansible_output(playbook, result.stdout, logger)

            if result.returncode == 0:
                return {'success': True}
            else:
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
                                if host_result.get('failed'):
                                    msg = (
                                        host_result.get('msg', '')
                                        or host_result.get('stderr', '')
                                        or str(host_result)
                                    )
                                    rc = host_result.get('rc')
                                    if rc == 124 or 'Killed' in msg:
                                        error_msg = f"exit code 124: command timed out in task '{task_name}'"
                                    else:
                                        error_msg = msg if msg else error_msg
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
