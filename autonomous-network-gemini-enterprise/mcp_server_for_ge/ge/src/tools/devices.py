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

import json
import logging
from typing import Annotated
from mcp.types import ToolAnnotations
from utils.globals import callable_tool, mutating_tool, networkagent_mcp
from utils.k8s import get_deployed_crs
from tools.descriptors import deleteDescriptor, deploy_descriptor

logger = logging.getLogger(__name__)

# callable_tool, not networkagent_mcp.tool: getDevices below aliases this.
@callable_tool()
def list_devices() -> str:
    """List deployed virtual network devices and routers from Kubernetes cluster."""
    logger.info("list_devices")
    crs = get_deployed_crs("vyosrouters.google.dev", "network")
    formatted = []
    for cr in crs:
        formatted.append({
            "name": cr["metadata"]["name"],
            "namespace": cr["metadata"]["namespace"],
            "status": cr.get("status", {}).get("phase", "Ready")
        })
    return json.dumps(formatted, indent=2)

@networkagent_mcp.tool()
def getDevices() -> str:
    """Legacy alias wrapper for listing deployed virtual network devices."""
    return list_devices()

@networkagent_mcp.tool()
def getDeviceByName(name: Annotated[str, "Target device name"]) -> str:
    """Fetch exact virtual network device metadata by Custom Resource name."""
    logger.info(f"getDeviceByName: {name}")
    crs = get_deployed_crs("vyosrouters.google.dev", "network")
    target = next((cr for cr in crs if cr["metadata"]["name"] == name), None)
    if not target:
        return f"Device '{name}' not found."
    return json.dumps(target, indent=2)

@networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def searchDevices(
    network_name: Annotated[str, "Filter by the LinuxNetwork the device is attached to"] = None,
    phase: Annotated[str, "Filter by lifecycle phase: Pending, Creating, Ready, Failed, Deleting, Updating"] = None
) -> str:
    """
    Search deployed Device resources by network and/or lifecycle phase.

    Useful for picking endpoints for a traffic test, e.g. all Ready devices on
    a given customer LAN. Both filters are optional; omitting both returns
    every Device.

    Returns:
        A JSON array of matching Device resources.
    """
    logger.info(f"searchDevices: network_name={network_name}, phase={phase}")
    crs = get_deployed_crs("google.dev", "network", kind="Device")

    matching = []
    for cr in crs:
        status = cr.get("status", {}) or {}
        # network_name is on the spec; the operator mirrors it into status
        # only once the device is up, so fall back to the spec to avoid
        # silently missing devices that are still Pending.
        cr_network = status.get("network_name") or (cr.get("spec", {}) or {}).get("network_name")
        if network_name and cr_network != network_name:
            continue
        if phase and status.get("phase") != phase:
            continue
        matching.append(cr)

    return json.dumps(matching, indent=2, default=str)

@mutating_tool()
def create_device(
    name: Annotated[str, "The name of the new device"],
    spec: Annotated[str, "JSON spec with at least network_name, ip_address and gateway"]
) -> str:
    """
    Create a Device resource by committing its descriptor to the GitOps repo.

    The controller reconciles from git, so this is the deployment path -- see
    tools/descriptors.py. Use getDeviceDefinition() for the full spec schema.
    """
    logger.info(f"create_device: {name}")
    try:
        parsed_spec = json.loads(spec) if isinstance(spec, str) else spec
    except json.JSONDecodeError as e:
        return f"Error: spec is not valid JSON: {e}"
    if not isinstance(parsed_spec, dict):
        return "Error: spec must be a JSON object"

    missing = [f for f in ("network_name", "ip_address", "gateway") if f not in parsed_spec]
    if missing:
        return f"Error: spec is missing required field(s): {', '.join(missing)}"

    return deploy_descriptor({
        "apiVersion": "google.dev/v1",
        "kind": "Device",
        "metadata": {"name": name, "namespace": "network"},
        "spec": parsed_spec,
    })

@mutating_tool()
def delete_device(
    name: Annotated[str, "The name of the device to delete"]
) -> str:
    """Delete a Device by removing its descriptor from the GitOps repo."""
    logger.info(f"delete_device: {name}")
    return deleteDescriptor(kind="Device", name=name)

@networkagent_mcp.tool()
def getNetworkDeployment() -> str:
    """
    Query Kubernetes to discover all virtual network functions and infrastructure
    currently deployed in the network (e.g., VyOSInfrastructure, VyOSUnderlay,
    VyOSL3VPN, VyOSRouter, LinuxNetwork, and Devices).

    Returns:
        A structured JSON string summarizing currently deployed network resources and their phases.
    """
    logger.info("getNetworkDeployment")
    deployed = {}
    kinds = ["VyOSInfrastructure", "VyOSUnderlay", "VyOSL3VPN", "VyOSRouter", "LinuxNetwork", "Device"]
    for k in kinds:
        crs = get_deployed_crs("google.dev", "network", kind=k)
        if crs:
            formatted = [{"name": c["metadata"]["name"], "status": c.get("status", {}).get("phase", "Ready")} for c in crs]
            deployed[k] = formatted
    return json.dumps(deployed, indent=2) if deployed else "No network deployment resources found."
