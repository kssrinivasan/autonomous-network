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

from opsagent.lifecycle_tasks import rebuild_opsagent_config

logger = logging.getLogger(__name__)

#########################################################################
# Opsagent lifecycle watchers
#
# These handlers observe VyOSRouter, Device, and NetworkFunction IP
# assignments and keep the Google Cloud Ops Agent scrape config on the
# VyOS VM in sync.
#
# Strategy:
# - RESUME handlers: rebuild config when operator restarts
# - FIELD handlers: rebuild config when IP addresses are assigned/changed
# - NO CREATE handlers: would fire before IPs assigned (wasteful)
# - NO DELETE handlers: parent infrastructure clears config before
#   deleting children, and get_scrape_targets filters deletionTimestamp
#########################################################################

@kopf.on.resume('google.dev', 'v1', 'vyosrouter', id='vyosrouter-opsagent-resume')
async def vyosrouter_opsagent_resume(namespace, logger, **kwargs):
    """Rebuild opsagent config when operator resumes and finds existing routers."""
    await rebuild_opsagent_config(namespace, logger)


@kopf.on.field('google.dev', 'v1', 'vyosrouter', field='status.ip_address', id='vyosrouter-opsagent-ip')
async def vyosrouter_ip_change(namespace, logger, old, new, **kwargs):
    """Trigger opsagent config rebuild when router IP address is assigned or changes."""
    if new and new != old:
        logger.info(f"VyOSRouter IP changed from {old} to {new}, updating opsagent config")
        await rebuild_opsagent_config(namespace, logger)


@kopf.on.resume('google.dev', 'v1', 'device', id='device-opsagent-resume')
async def device_opsagent_resume(namespace, logger, **kwargs):
    """Rebuild opsagent config when operator resumes and finds existing devices."""
    await rebuild_opsagent_config(namespace, logger)


@kopf.on.field('google.dev', 'v1', 'device', field='status.mgmt_ip', id='device-opsagent-ip')
async def device_ip_change(namespace, logger, old, new, **kwargs):
    """Trigger opsagent config rebuild when device mgmt IP is assigned or changes."""
    if new and new != old:
        logger.info(f"Device mgmt IP changed from {old} to {new}, updating opsagent config")
        await rebuild_opsagent_config(namespace, logger)


@kopf.on.resume('google.dev', 'v1', 'networkfunction', id='nf-opsagent-resume')
async def nf_opsagent_resume(namespace, logger, **kwargs):
    """Rebuild opsagent config when operator resumes and finds existing network functions."""
    await rebuild_opsagent_config(namespace, logger)


@kopf.on.field('google.dev', 'v1', 'networkfunction', field='status.ip_address', id='nf-opsagent-ip')
async def nf_ip_change(namespace, logger, old, new, **kwargs):
    """Trigger opsagent config rebuild when NF IP address is assigned or changes."""
    if new and new != old:
        logger.info(f"NetworkFunction IP changed from {old} to {new}, updating opsagent config")
        await rebuild_opsagent_config(namespace, logger)
