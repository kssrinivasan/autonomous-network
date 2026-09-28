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
from typing import Annotated, Dict
import kubernetes
import yaml
from mcp.types import ToolAnnotations
from utils.globals import mutating_tool, networkagent_mcp
from utils.k8s import get_client
import utils.git_helpers as git

logger = logging.getLogger(__name__)

GITOPS = False

@networkagent_mcp.tool()
def getTrafficTestDefinition() -> str:
    """Fetch the TrafficTest Custom Resource Definition (CRD) specification."""
    logger.info("Getting TrafficTest CRD definition")
    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        try:
            crd = network_api.get(name="traffictests.google.dev")
            return json.dumps(crd.to_dict(), indent=2)
        except kubernetes.client.rest.ApiException as get_e:
            if get_e.status == 404:
                return "TrafficTest CRD not found."
            raise get_e
    except kubernetes.client.rest.ApiException as e:
        return f"Error retrieving TrafficTest CRD: {e}"

@networkagent_mcp.tool()
def getRunningTests() -> str:
    """Fetch the traffic test instances that are currently running."""
    logger.info("get running traffic tests")
    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        test_list = ""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="TrafficTest",
        )
        tests = network_api.get(namespace="network")
        for t in tests.items:
            test_list += json.dumps(t.to_dict(), indent=2) + "\n"
        return test_list if test_list else "No traffic tests found"
    except kubernetes.client.rest.ApiException as e:
        return "No traffic tests found" if e.status == 404 else f"Error retrieving traffic tests: {e}"

@mutating_tool()
def runTest(
    name: Annotated[str, "The name of the traffic test to create"], 
    spec: Annotated[Dict, "The kubernetes spec object for the TrafficTest"]
) -> str:
    """Instantiate a new traffic test between network devices."""
    logger.info("run a new traffic test %s", name)
    if "spec" in spec:
        spec = spec["spec"]

    test_manifest = { 
        "apiVersion": "google.dev/v1",
        "kind": "TrafficTest",
        "metadata": {
            "name": name,
            "namespace": "network",
        },
        "spec": spec
    }
    test_manifest_yaml = yaml.dump(test_manifest, indent=2)
    
    if GITOPS:
        filename = f"traffictest-{name}.yaml"
        result = git.commit_git_file(
            filename, f"Deployment of TrafficTest {name}", test_manifest_yaml, git.SERVICE_REPO
        )
        return f"Traffic test {name} successfully submitted" if result else f"Traffic test {name} could not be deployed"
    
    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="TrafficTest",
        )
        network_api.create(test_manifest)
        return f"Traffic test {name} started successfully"
    except kubernetes.client.rest.ApiException as e:
        return f"Traffic test {name} already exists" if e.status == 409 else f"Error creating traffic test: {e}"

@mutating_tool()
def deleteTest(
    name: Annotated[str, "The name of the traffic test to delete"], 
) -> str:
    """Delete a running traffic test."""
    logger.info("delete traffic test %s", name)
    if GITOPS:
        filename = f"traffictest-{name}.yaml"
        result = git.delete_git_file(filename, f"TrafficTest {name} deletion", git.SERVICE_REPO)
        return f"Traffic test {name} successfully submitted for deletion" if result else f"Traffic test {name} could not be deleted"
    
    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="TrafficTest",
        )
        network_api.delete(name=name, namespace="network")
        return f"Traffic test {name} deleted successfully"
    except kubernetes.client.rest.ApiException as e:
        return f"Traffic test {name} not found" if e.status == 404 else f"Error deleting traffic test: {e}"
