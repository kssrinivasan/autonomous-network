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

import os
import kopf
import logging
from utils.compute import *
import utils.constants as constants
import ansible_runner

logger = logging.getLogger(__name__)

########################################################
# Install and configure networkvm software
########################################################
async def run_vyosvm_install(namespace, name, external_ip_address=None):
    logger.info("installing networkvm server")

    # Check VM is actually running before attempting SSH
    # This will raise TemporaryError if VM isn't RUNNING yet
    await get_vm_info(namespace, name)

    ip_address = await get_ip(namespace, name)
    logger.info("networkvm mgmt ip address is %s", ip_address)
    if ip_address is None:
        raise kopf.TemporaryError("waiting for vyosvm mgmt IP address")

    # Retrieve the public ssh key
    key=None
    with open(constants.basedir+'/google-compute.pub') as f: 
       key = f.read()
       logger.debug("ssh key = %s", key)

    # run ansible playbook to install prometheus on the VM
    extravars = {
        'GOOGLE_ORG_NAME': os.getenv("GOOGLE_ORG_NAME"),
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'GOOGLE_VM_USER': os.getenv("GOOGLE_VM_USER"),
        'WEBAPPS_LOGIN': os.getenv("WEBAPPS_LOGIN"),
        'WEBAPPS_PWD': os.getenv("WEBAPPS_PWD"),
        'BASEDIR': constants.basedir,
        'external_ip_address': external_ip_address,
        'mgmt_ip_address': ip_address,
        'GOOGLE_SSH_KEY': key
    }
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ConnectionAttempts=3 -o ServerAliveInterval=5'
            }
        }
    }
    logger.debug(hosts)
    logger.debug(extravars)
    def event_handler(data):
        logger.debug(data)
    r = ansible_runner.run(private_data_dir=constants.basedir+"/vyosvm/playbooks", 
                           inventory={'all': hosts},
                           playbook='install.yaml',
                           event_handler=event_handler,
                           extravars=extravars)

    logger.debug("status = %s", r.status)
    if r.status != 'successful':
        logger.error("Ansible playbook failed with status: %s", r.status)
        # Let the caller (lifecycle.py) determine if this should retry
        raise Exception(f"Ansible playbook failed with status: {r.status}")