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

from typing import Annotated
import logging
import kubernetes
from utils.k8s import get_client
import json
import utils.globals as globals
from mcp.types import ToolAnnotations

logger = logging.getLogger(__name__)

######################################################################
# Get Radio CRD Definition
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getRadioCRDs()-> str:
    """
    Fetch the available Radio CRDs.

    Returns:
        A set of kubernetes custom resource CRDs that can orchestrate Radion Network Domain resources. 
        Each CRD provides the following information:
        - description of the connectivity service functionality
        - a spec section that has the name of the 'kind' for each connectivity service and an OpenAPI schema describing the information required to instantiate the kind connectivity service.
    """
    logger.info("Getting Radio Network domain CRD definitions")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=radio")
        logger.debug("RADIO DOMAIN ITEMS")
        logger.debug(items)
        services=[]
        for item in items.items:
            services.append(f"```json\n{item}\n```")

        if len(services)==0:
            return ""
        else:
            return "\n".join(services)
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return ""
        else:
            logger.debug(e)
