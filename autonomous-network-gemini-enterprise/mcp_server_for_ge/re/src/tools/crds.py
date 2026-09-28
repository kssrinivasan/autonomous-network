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

"""CRD discovery: what an agent is allowed to ask the cluster to build.

The Spanner tools elsewhere in this package answer "what does the network look
like right now". These answer the prior question -- "what kinds of thing can I
declare, and with what schema" -- by reading the CustomResourceDefinitions the
controller installs, which carry a ``type=<domain>`` label.

Handing the agent the live OpenAPI schema rather than a prompt-embedded copy
means a CRD change cannot silently invalidate what the agent believes it can
deploy.
"""

import json
import logging
import os

import kubernetes
from mcp.types import ToolAnnotations

import utils.globals as globals
from utils.git_helpers import get_git_file
from utils.k8s import get_client

logger = logging.getLogger(__name__)

#: Namespace the controller deploys network CRs into.
NAMESPACE = os.getenv("GOOGLE_NAMESPACE", "network")

#: API group of every CRD this controller owns.
CR_API_VERSION = "google.dev/v1"


def _crds_labelled(domain):
    """Return every CRD labelled ``type=<domain>``, as fenced JSON blocks."""
    logger.info("Getting %s domain CRD definitions", domain)
    try:
        client = kubernetes.dynamic.DynamicClient(get_client())
        api = client.resources.get(
            api_version="apiextensions.k8s.io/v1",
            kind="CustomResourceDefinition",
        )
        items = api.get(label_selector=f"type={domain}")
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return ""
        # Returning the error beats returning None: the agent can act on
        # "the cluster refused me", but a null tool result just looks empty.
        logger.error("Error listing %s CRDs: %s", domain, e)
        return f"Error retrieving {domain} CRDs: {e}"
    except Exception as e:
        logger.error("Error listing %s CRDs: %s", domain, e)
        return f"Error retrieving {domain} CRDs: {e}"

    return "\n".join(f"```json\n{item}\n```" for item in items.items)


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getTransportCRDs() -> str:
    """
    Fetch the available Transport Network Domain CRDs.

    Returns:
        The Kubernetes CRDs that orchestrate Transport domain resources
        (VyOS routers, underlay and L3VPN). Each CRD carries a description of
        the connectivity service and an OpenAPI schema for its 'kind',
        describing everything required to instantiate it.
    """
    return _crds_labelled("transport")


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getCoreCRDs() -> str:
    """
    Fetch the available Core Network Domain CRDs.

    Returns:
        The Kubernetes CRDs that orchestrate 5G Core resources (FiveGCore and
        the individual NetworkFunctions). Each CRD carries a description of
        the function and an OpenAPI schema for its 'kind', describing
        everything required to instantiate it.
    """
    return _crds_labelled("core")


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getRadioCRDs() -> str:
    """
    Fetch the available Radio Network Domain CRDs.

    Returns:
        The Kubernetes CRDs that orchestrate Radio domain resources (cell
        sites, gNodeBs and UEs). Each CRD carries a description of the
        resource and an OpenAPI schema for its 'kind', describing everything
        required to instantiate it.
    """
    return _crds_labelled("radio")


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDeviceDefinition() -> str:
    """
    Fetch the Device Custom Resource Definition (CRD) specification.

    This provides the complete schema and documentation for creating Device
    resources, including all configuration options and field descriptions.

    Returns:
        The Device CRD as a JSON object containing the OpenAPI schema.
    """
    logger.info("Getting Device CRD definition")
    try:
        client = kubernetes.dynamic.DynamicClient(get_client())
        api = client.resources.get(
            api_version="apiextensions.k8s.io/v1",
            kind="CustomResourceDefinition",
        )
        crd = api.get(name="devices.google.dev")
        return json.dumps(crd.to_dict(), indent=2, default=str)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "Device CRD not found. It may not be installed in the cluster."
        logger.error("Error retrieving Device CRD: %s", e)
        return f"Error retrieving Device CRD: {e}"
    except Exception as e:
        logger.error("Error retrieving Device CRD: %s", e)
        return f"Error retrieving Device CRD: {e}"


#: Gitea repo and path holding each domain's design document. services/gitea's
#: seed.sh pushes one repo per domain from domains/<domain>/knowledge/, so the
#: path is fixed and only transport currently has a designdoc.md.
DESIGN_DOC_PATH = "knowledge/designdoc.md"


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getTransportDesignDoc() -> str:
    """
    Fetch the Transport Network Domain design document from Gitea.

    The document describes the transport domain's architecture and design
    decisions, implementation patterns, component interactions and workflows,
    and its configuration and deployment guidelines.

    Returns:
        The design document as markdown, or a message explaining why it could
        not be read.
    """
    logger.info("Getting Transport domain design document")
    try:
        content = get_git_file("transport", DESIGN_DOC_PATH)
    except Exception as e:
        logger.error("Failed to retrieve the transport design doc: %s", e)
        return (
            "Unable to retrieve the transport design document; Gitea may be "
            f"unavailable. Error: {e}"
        )

    if content is None:
        logger.error("Transport design doc not found at %s", DESIGN_DOC_PATH)
        return (
            f"Design document not found: '{DESIGN_DOC_PATH}' does not exist in "
            "the 'transport' Gitea repository. It is seeded from "
            "domains/transport/knowledge/, so add it there and re-run "
            "services/gitea/seed.sh."
        )

    logger.debug("Retrieved the transport design doc (%d bytes)", len(content))
    return content


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getDeployedTransportCRs() -> str:
    """
    Fetch all deployed Transport custom resources.

    Retrieves every VyOSInfrastructure, VyOSRouter, VyOSUnderlay and VyOSL3VPN
    custom resource from the network namespace.

    Returns:
        JSON array of all Transport CRs, or "[]" if none exist.
    """
    logger.info("Retrieving all Transport CRs deployed in namespace %s", NAMESPACE)

    try:
        client = kubernetes.dynamic.DynamicClient(get_client())
    except Exception as e:
        logger.error("Could not reach the cluster: %s", e)
        return json.dumps({"error": f"Could not reach the cluster: {e}"})

    all_crs = []
    for kind in ("VyOSInfrastructure", "VyOSRouter", "VyOSUnderlay", "VyOSL3VPN"):
        try:
            api = client.resources.get(api_version=CR_API_VERSION, kind=kind)
            items = api.get(namespace=NAMESPACE).get("items", [])
            all_crs.extend(
                item.to_dict() if hasattr(item, "to_dict") else item for item in items
            )
        except Exception as e:
            # A kind with no CRD installed, or no instances, is normal here --
            # the caller asked for whatever transport exists, not for all four.
            logger.info("Skipping %s: %s", kind, e)

    logger.info("Total Transport CRs found: %d", len(all_crs))
    return json.dumps(all_crs, indent=2, default=str)
