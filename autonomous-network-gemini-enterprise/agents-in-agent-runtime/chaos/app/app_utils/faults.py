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

def getFaultDescriptors() -> list[dict]:
    """
    Retrieve the Vyos fault k8s custom resource descriptors.

    These descriptors provide the failures that can be injected to a running VyosNetwork

    Args:
        None

    Returns:
        list[dict]: An array of Kubernetes CustomResourceDefinition (CRD) objects
    """
    logger.info(f"Fetching Fault descriptors")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=failure,agent-accessible=true")
        logger.debug("FAILURE ITEMS")
        logger.debug(items)
        services=[]
        for item in items.items:
            services.append(item.to_dict())

        return services

    except Exception as e:
        logger.error(e)


def deploySpec(descriptor: dict) -> str:
    """
    Deploy a Fault Custom Resource descriptor to the cluster.
    If the resource already exists, it will be updated (merge-patch).

    Args:
        descriptor (dict): The Kubernetes resource descriptor (YAML/dict)

    Returns:
        str: Success or error message
    """
    logger.info(f"Deploying descriptor: {descriptor.get('kind')}/{descriptor.get('metadata', {}).get('name')}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        kind = "NetworkFailure"
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
                # Already exists — merge-patch
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


def deleteFault(kind: str, name: str, namespace: str = "network") -> str:
    """
    Delete a Fault Custom Resource from the cluster.

    Args:
        name (str): The name of the resource
        namespace (str): The namespace

    Returns:
        str: Success or error message
    """
    logger.info(f"Deleting resource: {kind}/{name}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="NetworkFailure"
        )
        resource_api.delete(name=name, namespace=namespace)
        return f"Successfully deleted {kind}/{name}"
    except ApiException as e:
        if e.status == 404:
            return f"Resource {kind}/{name} not found, already deleted."
        logger.error(f"Failed to delete resource: {e}")
        return f"Error deleting {kind}/{name}: {str(e)}"
    except Exception as e:
        logger.error(f"Failed to delete resource: {e}")
        return f"Error deleting {kind}/{name}: {str(e)}"

def getDeployedFaults() -> list[dict]:
    """
    Retrieve all deployed Fault CRs

    Returns:
        list[dict]: list of running faults. 
    """
    logger.info(f"Fetching Fault resources")

    instances = []
    client = kubernetes.dynamic.DynamicClient(get_k8s_client())

    try:
        api = client.resources.get(api_version="google.dev/v1", kind="NetworkFailure")
        resources = api.get(namespace='network')
        for item in resources.items:
            instances.append(item.to_dict())
    except Exception as e:
        logger.error(f"Error fetching fault resources: {e}", exc_info=True)

    return instances