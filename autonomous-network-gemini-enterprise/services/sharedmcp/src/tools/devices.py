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
# Get Device CRD Definition
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDeviceDefinition()-> str:
    """
    Fetch the Device Custom Resource Definition (CRD) specification.
    
    This provides the complete schema and documentation for creating Device resources,
    including all configuration options and field descriptions.

    Returns:
        The Device CRD as a JSON object containing the OpenAPI schema and documentation
    """
    logger.info("Getting Device CRD definition")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        
        # Get the Device CRD
        try:
            crd = network_api.get(name="devices.google.dev")
            crd_dict = crd.to_dict()
            text_representation = json.dumps(crd_dict, indent=2)
            logger.debug(f"Found Device CRD: {text_representation}")
            return text_representation
        except kubernetes.client.rest.ApiException as get_e:
            if get_e.status == 404:
                return "Device CRD not found. It may not be installed in the cluster."
            else:
                raise get_e
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "Unable to access CRD definitions"
        else:
            logger.error(f"Error retrieving Device CRD: {e}")
            return f"Error retrieving Device CRD: {e}"


######################################################################
# Get all devices
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDevices()-> str:
    """
    Fetch all Device instances currently deployed in the cluster.
    
    Devices represent virtual end-user devices or customer premise equipment (CPE) 
    that can be used as source or destination for traffic tests.

    Returns:
        A list of all Device instances with their configuration and status as JSON objects
    """
    logger.info("Getting all devices")

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        device_list = ""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="Device",
        )
        devices = network_api.get(namespace="network")
        for device in devices.items:
            logger.debug(device)
            device_dict = device.to_dict()
            text_representation = json.dumps(device_dict, indent=2)
            device_list = device_list + text_representation + "\n"
        
        logger.debug(device_list)
        return device_list if device_list else "No devices found"

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No devices found"
        else:
            logger.error(e)
            return f"Error retrieving devices: {e}"


######################################################################
# Get device by name
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDeviceByName(name: Annotated[str, "The name of the device to retrieve"])-> str:
    """
    Find a specific Device instance by name.
    
    Use this to verify a device exists before using it in a traffic test,
    or to check the status and configuration of a specific device.

    Args:
        name: The name of the device to find

    Returns:
        The Device instance with its configuration and status as a JSON object
    """
    logger.info(f"Getting device by name: {name}")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="Device",
        )

        # Get the specific device by name
        try:
            device = network_api.get(name=name, namespace="network")
            device_dict = device.to_dict()
            text_representation = json.dumps(device_dict, indent=2)
            logger.debug(f"Found device: {text_representation}")
            return text_representation
        except kubernetes.client.rest.ApiException as get_e:
            if get_e.status == 404:
                return f"Device '{name}' not found"
            else:
                raise get_e

    except kubernetes.client.rest.ApiException as e:
        logger.error(f"Error retrieving device: {e}")
        return f"Error retrieving device: {e}"


######################################################################
# Search devices by criteria
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def searchDevices(
    network_name: Annotated[str, "Filter devices by network name (optional)"] = None,
    phase: Annotated[str, "Filter devices by phase: Pending, Creating, Ready, Failed, Deleting, Updating (optional)"] = None
)-> str:
    """
    Search for Device instances matching specific criteria.
    
    This is useful for finding devices on a specific network or in a specific state.
    For example, find all Ready devices on a particular network to use in traffic tests.

    Args:
        network_name: Optional - filter by network name (e.g., "customer-lan")
        phase: Optional - filter by lifecycle phase (e.g., "Ready")

    Returns:
        A list of matching Device instances as JSON objects
    """
    logger.info(f"Searching devices - network_name: {network_name}, phase: {phase}")

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="Device",
        )
        devices = network_api.get(namespace="network")
        
        matching_devices = []
        for device in devices.items:
            device_dict = device.to_dict()
            
            # Apply filters
            match = True
            if network_name:
                device_network = device_dict.get('status', {}).get('network_name', '')
                if device_network != network_name:
                    match = False
            
            if phase:
                device_phase = device_dict.get('status', {}).get('phase', '')
                if device_phase != phase:
                    match = False
            
            if match:
                matching_devices.append(device_dict)
        
        if matching_devices:
            result = "\n".join([json.dumps(d, indent=2) for d in matching_devices])
            logger.debug(f"Found {len(matching_devices)} matching devices")
            return result
        else:
            return f"No devices found matching criteria (network_name={network_name}, phase={phase})"

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "No devices found"
        else:
            logger.error(e)
            return f"Error searching devices: {e}"
