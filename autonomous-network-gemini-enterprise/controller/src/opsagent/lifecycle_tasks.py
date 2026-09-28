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
import logging
import os
import shutil
import subprocess
import tempfile

import kopf
import kubernetes

import utils.constants as constants
from utils.compute import get_ip

logger = logging.getLogger(__name__)

VYOSVM_NAME = "networkvm"


async def get_scrape_targets(namespace: str) -> tuple:
    """Query all three CR types and return current Prometheus scrape targets.

    Returns (routers, devices, nfs) where:
      routers: list of {"name": str, "ip_address": str}
      devices: list of {"name": str, "mgmt_ip": str}
      nfs:     list of {"name": str, "ip_address": str, "metrics_port": int, "nf_type": str}

    Items with deletionTimestamp set are excluded so that delete handlers
    calling rebuild_opsagent_config see an accurate view of remaining targets.
    NFs are only included when spec.metrics.enabled is True and status.ip_address is set.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    routers = []
    devices = []
    nfs = []

    try:
        api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
        result = api.get(namespace=namespace)
        for item in (result.items if hasattr(result, 'items') else []):
            d = item.to_dict() if hasattr(item, 'to_dict') else item
            if d.get('metadata', {}).get('deletionTimestamp'):
                continue
            ip = d.get('status', {}).get('ip_address', '')
            if ip:
                routers.append({"name": d['metadata']['name'], "ip_address": ip})
    except Exception as e:
        logger.warning("Failed to query VyOSRouter CRs for opsagent config: %s", e)

    try:
        api = client.resources.get(api_version='google.dev/v1', kind='Device')
        result = api.get(namespace=namespace)
        for item in (result.items if hasattr(result, 'items') else []):
            d = item.to_dict() if hasattr(item, 'to_dict') else item
            if d.get('metadata', {}).get('deletionTimestamp'):
                continue
            mgmt_ip = d.get('status', {}).get('mgmt_ip', '')
            if mgmt_ip:
                devices.append({"name": d['metadata']['name'], "mgmt_ip": mgmt_ip})
    except Exception as e:
        logger.warning("Failed to query Device CRs for opsagent config: %s", e)

    try:
        api = client.resources.get(api_version='google.dev/v1', kind='NetworkFunction')
        result = api.get(namespace=namespace)
        for item in (result.items if hasattr(result, 'items') else []):
            d = item.to_dict() if hasattr(item, 'to_dict') else item
            if d.get('metadata', {}).get('deletionTimestamp'):
                continue
            spec = d.get('spec', {})
            metrics = spec.get('metrics', {})
            if not metrics.get('enabled', False):
                continue
            ip = d.get('status', {}).get('ip_address', '')
            if not ip:
                continue
            nfs.append({
                "name": d['metadata']['name'],
                "ip_address": ip,
                "metrics_port": int(metrics.get('port', 9089)),
                "nf_type": spec.get('nf_type', 'unknown'),
            })
    except Exception as e:
        logger.warning("Failed to query NetworkFunction CRs for opsagent config: %s", e)

    logger.info(
        "Opsagent scrape targets: %d router(s), %d device(s), %d NF(s)",
        len(routers), len(devices), len(nfs),
    )
    return routers, devices, nfs


async def update_opsagent_config(
    namespace: str,
    routers: list,
    devices: list,
    nfs: list,
    logger_obj=None,
) -> None:
    """Re-render the Ops Agent config on the VyOSVM and restart the agent.

    Args:
        namespace:  Kubernetes namespace (used to look up the VyOSVM IP).
        routers:    List of {"name": str, "ip_address": str} dicts for VyOS
                    router scrape targets (node_exporter :9100, frr_exporter :9101).
        devices:    List of {"name": str, "mgmt_ip": str} dicts for traffic-agent
                    Prometheus targets (port 9091).
        nfs:        List of {"name": str, "ip_address": str, "metrics_port": int,
                    "nf_type": str} dicts for free5gc NF Prometheus targets.
    """
    _log = logger_obj or logger

    _log.info(
        "Updating Ops Agent config on %s: %d router(s), %d device(s), %d NF(s)",
        VYOSVM_NAME, len(routers), len(devices), len(nfs),
    )

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError(
            "No ip address found on Network VM yet, temporary error - waiting", 10
        )
    _log.info("network vm address = %s", ip_address)

    extravars = {
        "routers": routers,
        "devices": devices,
        "nfs": nfs,
    }

    inventory = {
        "all": {
            "hosts": {
                "monitor": {
                    "ansible_host": ip_address,
                    "ansible_user": os.getenv("GOOGLE_VM_USER"),
                    "ansible_connection": "ssh",
                    "ansible_ssh_private_key_file": constants.basedir + "/google-compute",
                    "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
                }
            }
        }
    }

    ANSIBLE_TIMEOUT_SECONDS = 60

    def run_ansible(temp_dir):
        inv_file = os.path.join(temp_dir, 'inventory.json')
        extravars_file = os.path.join(temp_dir, 'extravars.json')
        playbook_path = os.path.join(
            constants.basedir,
            'opsagent/playbooks',
            'update-opsagent.yaml',
        )

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
            _log.error(
                "Ansible playbook 'update-opsagent.yaml' did not finish within "
                "%ds — treating as failure", ANSIBLE_TIMEOUT_SECONDS
            )
            return None
        except Exception as e:
            _log.error("Ansible execution failed: %s", e)
            return None

    from utils.ansible import get_ansible_semaphore, log_ansible_output
    semaphore = get_ansible_semaphore()

    async with semaphore:
        temp_dir = tempfile.mkdtemp(prefix="ansible_opsagent_")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_ansible, temp_dir)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if result is None:
        _log.error("Failed to execute Ops Agent update playbook")
        return

    log_ansible_output('update-opsagent.yaml', result.stdout, _log)

    if result.returncode == 0:
        _log.info(
            "Ops Agent config updated successfully: %d router(s), %d device(s), %d NF(s)",
            len(routers), len(devices), len(nfs),
        )
    else:
        _log.error(
            "Ansible failed while updating Ops Agent config (rc=%d)", result.returncode
        )


async def rebuild_opsagent_config(namespace: str, logger_obj=None) -> None:
    """Query all scrape targets from the cluster and update the Ops Agent config.

    Convenience wrapper: get_scrape_targets → update_opsagent_config.
    Called from VyOSRouter, Device, and NetworkFunction lifecycle handlers after
    each create/delete so the config stays current without a VyOSInfrastructure
    reconcile being required.
    """
    routers, devices, nfs = await get_scrape_targets(namespace)
    await update_opsagent_config(namespace, routers, devices, nfs, logger_obj)
