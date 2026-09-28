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
from utils.compute import *
from utils.vyosnetwork import update_status
from vyosvm.lifecycle_tasks import *

logger = logging.getLogger(__name__)

#########################################################################
# Error Classification
#########################################################################

def _is_transient_error(error_msg: str) -> bool:
    """
    Determine if an error is transient (worth retrying) vs permanent.

    Transient errors include SSH/network connectivity issues, VM not ready,
    and Ansible connection timeouts.
    """
    error_lower = error_msg.lower()
    transient_patterns = [
        'ssh',
        'connection refused',
        'connection timed out',
        'connection reset',
        'timeout',
        'unreachable',
        'temporarily unavailable',
        'host is down',
        'no route to host',
        'network is unreachable',
        'failed to execute ansible',
        'ansible playbook failed with status: failed',
        'no such host',
        'i/o timeout',
        'broken pipe',
        'eof',
        'not ready',
        'timed out waiting',
        'timed out',
        "'nonetype' object has no attribute 'get'",  # VM status not populated yet
        'nonetype',  # VM/resource not fully initialized
    ]
    return any(pattern in error_lower for pattern in transient_patterns)

@kopf.on.create('google.dev','v1','vyosvm')
async def create_vyosvm(spec, meta, name, namespace, logger, **kwargs):
    logger.info("Create vyosvm")
    await update_status(name, namespace, "VyosVM", "Creating", "Provisioning VM")

    # Create VM with only internal IP address
    await create_compute(namespace,
                         name,
                         name,
                         None, # no external IP - use internal IP only
                         None,
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"),
                         family="ubuntu-os-cloud",
                         release="ubuntu-2204-lts",
                         monitor=False, # set to false so this VM is not scraped by prometheus
                         graph=False, # set to false so this VM is not showing on topology graph
                         machine='e2-standard-16',# gcp compute machine type
                         scopes="cloud-platform")

    # Install software on VM
    await update_status(name, namespace, "VyosVM", "Installing", "Running Ansible playbook")

    try:
        await run_vyosvm_install(namespace, name)
        await update_status(name, namespace, "VyosVM", "Ready", "VM is fully provisioned")
    except kopf.TemporaryError:
        # Let TemporaryErrors propagate for retry
        raise
    except Exception as e:
        error_msg = str(e)
        if _is_transient_error(error_msg):
            logger.warning(f"Transient installation error for {name}, will retry: {error_msg}")
            await update_status(name, namespace, "VyosVM", "Pending", f"Installation pending retry: {error_msg}")
            raise kopf.TemporaryError(f"Transient installation error: {error_msg}", delay=30)
        else:
            logger.error(f"Permanent installation error for {name}: {error_msg}")
            await update_status(name, namespace, "VyosVM", "Failed", f"Installation failed: {error_msg}")
            raise kopf.PermanentError(f"Installation failed: {error_msg}")

    return {"status": "Ready"}
