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
# Relative: see the note in app/app_utils/git.py -- agent_library is vendored
# into app/agent_library, so under the 1.3.1 layout it is app.agent_library.
from ..agent_library import get_client as get_k8s_client

logger = logging.getLogger(__name__)

def getVyosDescriptors() -> list[dict]:
    """
    Retrieve the Vyos infrastructure, underlay and vpn k8s custom resource descriptors.
    """
    logger.info("Fetching VyOS descriptors")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        # A set-based type term, because the four kinds getDeployedCRs works
        # with span three type values: infrastructure (VyOSInfrastructure),
        # transport (VyOSUnderlay, VyOSL3VPN) and traffic (TrafficTest). No
        # single equality term covers them -- "type=network", which was here
        # before, matches no CRD at all. Excluding type=failure keeps the fault
        # descriptors chaos owns out of designer's view.
        items = network_api.get(
            label_selector="agent-accessible=true,type in (infrastructure,transport,traffic)"
        )
        services = [item.to_dict() for item in items.items]
        return services
    except Exception as e:
        logger.error(f"Error fetching VyOS descriptors: {e}")
        return []

def getDeployedCRs() -> list[dict]:
    """
    Retrieve all deployed Vyos CRs
    """
    logger.info("Fetching VyOS resources")
    instances = []
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        for kind in ["VyOSInfrastructure", "VyOSUnderlay", "VyOSL3VPN", "TrafficTest"]:
            try:
                api = client.resources.get(api_version="google.dev/v1", kind=kind)
                resources = api.get(namespace='network')
                instances.extend([item.to_dict() for item in resources.items])
            except Exception as e:
                logger.debug(f"Error fetching {kind}: {e}")
    except Exception as e:
        logger.error(f"Error getting k8s client: {e}")
    return instances

def deploySpec(descriptor: dict) -> str:
    """
    Deploy a VyOS Custom Resource descriptor to the cluster directly.
    """
    logger.info(f"Deploying descriptor: {descriptor.get('kind')}/{descriptor.get('metadata', {}).get('name')}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        kind = descriptor.get("kind")
        name = descriptor.get("metadata", {}).get("name")
        namespace = descriptor.get("metadata", {}).get("namespace", "network")
        
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind
        )

        try:
            resource_api.create(descriptor, namespace=namespace)
            return f"Successfully created {kind}/{name}"
        except ApiException as exc:
            if exc.status == 409:
                resource_api.patch(
                    body=descriptor,
                    name=name,
                    namespace=namespace,
                    content_type='application/merge-patch+json'
                )
                return f"Successfully updated {kind}/{name}"
            else:
                raise exc
    except Exception as e:
        logger.error(f"Failed to deploy descriptor: {e}")
        return f"Error deploying {descriptor.get('kind', 'Unknown')}: {str(e)}"

def deleteSpec(kind: str, name: str, namespace: str = "network") -> str:
    """
    Delete a VyOS Custom Resource from the cluster directly.
    """
    logger.info(f"Deleting resource: {kind}/{name}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind
        )
        resource_api.delete(name=name, namespace=namespace)
        return f"Successfully deleted {kind}/{name}"
    except ApiException as e:
        if e.status == 404:
            return f"Resource {kind}/{name} not found, already deleted."
        return f"Error deleting {kind}/{name}: {str(e)}"
    except Exception as e:
        return f"Error deleting {kind}/{name}: {str(e)}"
