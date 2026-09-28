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
import utils.constants as constants
from utils.compute import *
from graph.transport_tasks import (
    sync_vyos_infrastructure, delete_vyos_infrastructure,
    sync_physical_router, delete_physical_router,
    sync_l3vpn_service, delete_l3vpn_service,
)
from graph.infra_tasks import (
    sync_compute_host, delete_compute_host,
    sync_linux_network, delete_linux_network,
)
from graph.core_tasks import (
    sync_fivegcore, delete_fivegcore,
    sync_network_function, delete_network_function,
)
from graph.radio_tasks import sync_cellsite, delete_cellsite
from graph.service_tasks import sync_traffic_flow, delete_traffic_flow

logger = logging.getLogger(__name__)

# --- Transport Handlers ---

# GRAPH transportGraph
# MATCH router_path = (r1:PhysicalRouter)-[:HasInterface]->(:PhysicalInterface)
#       -[:ConnectsTo]->(:PhysicalLink)
#       -[:LinkedTo]->(:PhysicalInterface)
#       <-[:HasInterface]-(r2:PhysicalRouter)
# WHERE r1.id < r2.id
# RETURN SAFE_TO_JSON(router_path) AS router_path
# LIMIT 40;


@kopf.on.event('vyosinfrastructures.google.dev', id='vyosinfrastructure-graph')
async def obj_vyosinfrastructure(body, spec, name, uid, logger, event, **kwargs):
    if event['type'] == 'DELETED':
        await delete_vyos_infrastructure(uid, spec, logger)
    else:
        await sync_vyos_infrastructure(body, spec, name, uid, logger)

@kopf.on.create('vyosrouters.google.dev', id='vyosrouter-create-graph')
@kopf.on.resume('vyosrouters.google.dev', id='vyosrouter-resume-graph')
async def obj_vyosrouter_create(body, spec, name, uid, logger, **kwargs):
    await sync_physical_router(body, spec, name, uid, logger)

@kopf.on.update('vyosrouters.google.dev', field='spec', id='vyosrouter-spec-graph')
async def obj_vyosrouter_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_physical_router(body, spec, name, uid, logger)

@kopf.on.delete('vyosrouters.google.dev', id='vyosrouter-delete-graph')
async def obj_vyosrouter_delete(name, uid, logger, **kwargs):
    await delete_physical_router(uid, name=name)

@kopf.on.create('vyosl3vpns.google.dev', id='vyosl3vpn-create-graph')
@kopf.on.resume('vyosl3vpns.google.dev', id='vyosl3vpn-resume-graph')
async def obj_vyosl3vpn_create(body, spec, name, uid, logger, **kwargs):
    await sync_l3vpn_service(body, spec, name, uid, logger)

@kopf.on.update('vyosl3vpns.google.dev', field='spec', id='vyosl3vpn-spec-graph')
async def obj_vyosl3vpn_update(body, spec, name, uid, logger, **kwargs):
    await sync_l3vpn_service(body, spec, name, uid, logger)

@kopf.on.delete('vyosl3vpns.google.dev', id='vyosl3vpn-delete-graph')
async def obj_vyosl3vpn_delete(uid, logger, **kwargs):
    await delete_l3vpn_service(uid)

# --- Infrastructure Handlers ---

@kopf.on.create('devices.google.dev', id='device-create-graph')
@kopf.on.resume('devices.google.dev', id='device-resume-graph')
async def obj_device_create(body, spec, name, uid, logger, **kwargs):
    await sync_compute_host(body, spec, name, uid, logger)

@kopf.on.update('devices.google.dev', field='spec', id='device-spec-graph')
async def obj_device_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_compute_host(body, spec, name, uid, logger)

@kopf.on.field('devices.google.dev', field='status.phase', id='device-status-graph')
async def obj_device_status_update(body, spec, name, uid, logger, old, new, **kwargs):
    # Skip if device is being deleted
    if body.get('metadata', {}).get('deletionTimestamp'):
        return

    # Skip if phase didn't actually change
    if old == new:
        return

    await sync_compute_host(body, spec, name, uid, logger)

@kopf.on.delete('devices.google.dev', id='device-delete-graph')
async def obj_device_delete(name, uid, logger, **kwargs):
    await delete_compute_host(uid, name=name)

@kopf.on.create('linuxnetworks.google.dev', id='linuxnetwork-create-graph')
@kopf.on.resume('linuxnetworks.google.dev', id='linuxnetwork-resume-graph')
async def obj_linuxnetwork_create(body, spec, name, uid, logger, **kwargs):
    await sync_linux_network(body, spec, name, uid, logger)

@kopf.on.update('linuxnetworks.google.dev', field='spec', id='linuxnetwork-spec-graph')
async def obj_linuxnetwork_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_linux_network(body, spec, name, uid, logger)

@kopf.on.update('linuxnetworks.google.dev', field='status', id='linuxnetwork-status-graph')
async def obj_linuxnetwork_status_update(body, spec, name, uid, logger, **kwargs):
    await sync_linux_network(body, spec, name, uid, logger)

@kopf.on.delete('linuxnetworks.google.dev', id='linuxnetwork-delete-graph')
async def obj_linuxnetwork_delete(name, uid, logger, **kwargs):
    await delete_linux_network(uid, name=name)

# --- TrafficTest Handlers ---

@kopf.on.create('google.dev', 'v1', 'traffictest', id='traffictest-create-graph')
@kopf.on.resume('google.dev', 'v1', 'traffictest', id='traffictest-resume-graph')
async def obj_traffictest_create(body, spec, name, uid, logger, **kwargs):
    await sync_traffic_flow(body, spec, name, uid, logger)

@kopf.on.update('google.dev', 'v1', 'traffictest', field='spec', id='traffictest-spec-graph')
async def obj_traffictest_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_traffic_flow(body, spec, name, uid, logger)

@kopf.on.delete('google.dev', 'v1', 'traffictest', id='traffictest-delete-graph')
async def obj_traffictest_delete(name, uid, logger, **kwargs):
    await delete_traffic_flow(name=name)

# --- 5G Core Handlers ---

@kopf.on.create('google.dev', 'v1', 'fivegcore', id='fivegcore-create-graph')
@kopf.on.resume('google.dev', 'v1', 'fivegcore', id='fivegcore-resume-graph')
async def obj_fivegcore_create(body, spec, name, uid, logger, **kwargs):
    await sync_fivegcore(body, spec, name, uid, logger)

@kopf.on.update('google.dev', 'v1', 'fivegcore', field='spec', id='fivegcore-spec-graph')
async def obj_fivegcore_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_fivegcore(body, spec, name, uid, logger)

@kopf.on.delete('google.dev', 'v1', 'fivegcore', id='fivegcore-delete-graph')
async def obj_fivegcore_delete(name, uid, logger, **kwargs):
    await delete_fivegcore(uid, name=name)

@kopf.on.create('google.dev', 'v1', 'networkfunction', id='nf-create-graph')
@kopf.on.resume('google.dev', 'v1', 'networkfunction', id='nf-resume-graph')
async def obj_nf_create(body, spec, name, uid, logger, **kwargs):
    await sync_network_function(body, spec, name, uid, logger)

@kopf.on.update('google.dev', 'v1', 'networkfunction', field='spec', id='nf-spec-graph')
async def obj_nf_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_network_function(body, spec, name, uid, logger)

@kopf.on.delete('google.dev', 'v1', 'networkfunction', id='nf-delete-graph')
async def obj_nf_delete(name, uid, logger, **kwargs):
    await delete_network_function(uid, name=name)

# --- Radio Handlers ---

@kopf.on.create('google.dev', 'v1', 'cellsite', id='cellsite-create-graph')
@kopf.on.resume('google.dev', 'v1', 'cellsite', id='cellsite-resume-graph')
async def obj_cellsite_create(body, spec, name, uid, logger, **kwargs):
    await sync_cellsite(body, spec, name, uid, logger)

@kopf.on.update('google.dev', 'v1', 'cellsite', field='spec', id='cellsite-spec-graph')
async def obj_cellsite_spec_update(body, spec, name, uid, logger, **kwargs):
    await sync_cellsite(body, spec, name, uid, logger)

@kopf.on.delete('google.dev', 'v1', 'cellsite', id='cellsite-delete-graph')
async def obj_cellsite_delete(name, uid, logger, **kwargs):
    await delete_cellsite(uid, name=name)
