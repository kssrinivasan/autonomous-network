# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import kubernetes
from kubernetes.client.rest import ApiException
# Relative: see the note in app/app_utils/docs.py -- agent_library is vendored
# into app/agent_library, so under the 1.3.1 layout it is app.agent_library.
from ..agent_library import get_client as get_k8s_client


logger = logging.getLogger(__name__)

def getInfrastructure():
    """
    Retrieve all deployed VyOS network infrastructure managed by the Kubernetes operator.

    Use this tool to discover what network components are available before introducing
    failures or chaos experiments. This returns the complete topology including:
    - Linux networks (namespaces, bridges, interfaces)
    - VyOS routers (names, configurations, routing state)
    - Devices and their connectivity

    Essential for selecting valid targets for chaos operations and understanding the
    blast radius of potential failures.

    Returns:
        list[dict]: VyosInfrastructure custom resources with network topology details
    """
    logger.info("Getting VyosInfrastructure")

    infra_instances = []
    client = kubernetes.dynamic.DynamicClient(get_k8s_client())

    try:
        api = client.resources.get(api_version="google.dev/v1", kind="VyOSInfrastructure")
        resources = api.get(namespace='network')
        for item in resources.items:
            infra_instances.append(item.to_dict())
    except Exception as e:
        logger.error(f"Error fetching infrastructure resources: {e}", exc_info=True)

    return infra_instances
