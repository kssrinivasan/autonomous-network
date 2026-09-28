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

import json
import logging
import kubernetes
from utils.k8s import get_client
import utils.globals as globals
from utils.gitea_utils import get_git_file
from mcp.types import ToolAnnotations

logger = logging.getLogger(__name__)

######################################################################
# Get Transport CRD Definition
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getTransportCRDs()-> str:
    """
    Fetch the available Transport CRDs.

    Returns:
        A set of kubernetes custom resource CRDs that can orchestrate Tranpost Network Domain resources. 
        Each CRD provides the following information:
        - description of the connectivity service functionality
        - a spec section that has the name of the 'kind' for each connectivity service and an OpenAPI schema describing the information required to instantiate the kind connectivity service.
    """
    logger.info("Getting Transport Network domain CRD definitions")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=transport")
        logger.debug("Transport DOMAIN ITEMS")
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

######################################################################
# Get Transport Design Document
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getTransportDesignDoc() -> str:
    """
    Fetch the Transport Network Domain design document from Gitea.

    This document provides comprehensive information about the transport domain's:
    - Architecture and design decisions
    - Implementation details and patterns
    - Component interactions and workflows
    - Configuration and deployment guidelines

    Returns:
        The design document content as markdown text, or an error message if the
        document is not available in the Gitea repository.
    """
    logger.info("Getting Transport domain design document")

    file_path = "knowledge/designdoc.md"

    try:
        content = get_git_file(repo_name="transport", filename=file_path)

        if content is None:
            error_msg = (
                f"Design document not found for transport domain. "
                f"The file 'knowledge/designdoc.md' does not exist in the transport "
                f"Gitea repository. Please create this file to document the domain's "
                f"design and architecture."
            )
            logger.error(f"Design document not found for transport domain at {file_path}")
            return error_msg

        logger.debug(f"Retrieved design doc for transport domain ({len(content)} bytes)")
        return content

    except Exception as e:
        error_msg = (
            f"Unable to retrieve design document for transport domain. "
            f"Gitea service may not be available. Error: {str(e)}"
        )
        logger.error(f"Failed to retrieve design doc for transport domain: {str(e)}")
        return error_msg


######################################################################
# Get Deployed Transport CRs
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDeployedTransportCRs() -> str:
    """
    Fetch all Transport deployed k8s custom resources.

    Retrieves all VyOSInfrastructure, VyOSRouter, VyOSUnderlay, and VyOSL3VPN custom resources
    from the network namespace and returns them as a JSON string.

    Returns:
        JSON string representation of all Transport CRs, or "[]" if none exist.
    """
    namespace = "network"
    logger.info(f"Retrieving all Transport CRs deployed in namespace {namespace}")

    client = kubernetes.dynamic.DynamicClient(get_client())
    all_crs = []

    transport_kinds = ["VyOSInfrastructure", "VyOSRouter", "VyOSUnderlay", "VyOSL3VPN"]

    for kind in transport_kinds:
        try:
            api = client.resources.get(
                api_version="google.dev/v1",
                kind=kind
            )
            result = api.get(namespace=namespace)
            items = result.get("items", [])
            dict_items = [
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in items
            ]
            logger.info(f"Found {len(dict_items)} {kind} CRs")
            all_crs.extend(dict_items)
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                logger.info(f"No {kind} CRs found in namespace {namespace}")
            else:
                logger.error(f"Error fetching {kind} CRs: {e}")
                raise
        except Exception as e:
            if "not found" in str(e).lower() or "notfound" in type(e).__name__.lower():
                logger.info(f"CRD {kind} or resources not found in namespace {namespace}: {e}")
            else:
                logger.error(f"Error fetching {kind} CRs: {e}")
                raise

    logger.info(f"Total Transport CRs found: {len(all_crs)}")
    return json.dumps(all_crs, indent=2, default=str)





