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

import os
import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .app_utils.vyos import (
    getVyosDescriptors,
    getDeployedCRs,
    deploySpec,
    deleteSpec,
)
from .app_utils.design import (
    getDesignDoc,
    deployDescriptor,
    deleteDescriptor,
)
from .app_utils.notification import send_google_chat_notification

def notifyCompletion(
    summary: str,
    status: str = "SUCCESS",
    resources_changed: str = "",
    gitops_commit: str = "",
) -> str:
    """
    Send a Google Chat notification to the SRE/operator upon completing a design, validation, or deployment task.

    Args:
        summary: Concise summary of network design changes or actions completed.
        status: Task status ("SUCCESS", "WARNING", "FAILED").
        resources_changed: Comma-separated list of Custom Resources created/updated/deleted (e.g. "VyOSL3VPN/pe1-l3vpn, VyOSUnderlay/pe1-underlay").
        gitops_commit: GitOps commit ID or message reference.
    """
    res_list = [r.strip() for r in resources_changed.split(",") if r.strip()] if resources_changed else None
    return send_google_chat_notification(
        summary=summary,
        title="Network Designer Task Completed",
        status=status,
        resources_changed=res_list,
        gitops_commit=gitops_commit,
    )


# Set up project environment
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

description = """
Network Designer Agent — MPLS/L3VPN Infrastructure Design & Deployment.

Translates high-level network design intent into VyOS Kubernetes Custom Resource Definitions
and deploys changes to the running MPLS/L3VPN topology. Manages physical routers, underlay
routing protocols (OSPF, MPLS, LDP), and L3VPN services (VRFs, MP-BGP, route targets).
"""

root_prompt = """
You are a Network Designer Agent responsible for translating high-level network change requests
and design intent into precise Kubernetes Custom Resource (CR) definitions (YAML) for VyOS routers,
and applying them to the running topology.

The network uses VyOS routers managed via the following core CRD types:
1. **VyOSInfrastructure**: Physical routers, network segments, devices/CPEs with IPAM.
2. **VyOSUnderlay**: MPLS, LDP, and OSPF configurations for core transport routers.
3. **VyOSL3VPN**: Customer edge VRF/VPN configurations with MP-BGP peering and route targets.

## Your Workflow:

1. **Information Gathering** — Always inspect the current network state and rules first:
   - Call `getDesignDoc()` to verify authoritative design rules, IP addressing (IPAM) schemes, and naming conventions.
   - Call `getVyosDescriptors()` to inspect the exact schema and required fields for each CRD type.
   - Call `getDeployedCRs()` to retrieve the full existing configuration of all deployed network resources.

2. **Reasoning & Planning**:
   - Determine what resources need to be created, updated, or deleted.
   - **PREFER UPDATING EXISTING CRs** over creating new ones. For example, if adding a VRF or customer link to an existing PE router, update its existing CR YAML retrieved from `getDeployedCRs()` rather than creating a new resource from scratch.
   - Validate all proposed IP subnets, loopbacks, Route Distinguishers (RD), and Route Targets (RT) against existing assignments to prevent conflicts.

3. **Execution & GitOps Deployment**:
   - Once you have verified the configuration, generate the complete raw YAML descriptor.
   - For updates, ensure `metadata.name` exactly matches the deployed resource name to avoid duplicate creation.
   - Call `deployDescriptor(descriptor_yaml)` to commit the formatted YAML descriptor to GitOps, which validates the schema against the live cluster before saving.
   - Alternatively, call `deploySpec(descriptor_dict)` to apply a dictionary spec directly to Kubernetes.
   - For deletions, call `deleteDescriptor(name)` or `deleteSpec(kind, name)`.

4. **Completion & Notification (MANDATORY)**:
   - MANDATORY STEP: Once you have completed the design modifications and committed them via `deployDescriptor()` or `deleteDescriptor()`, summarize the final state.
   - You MUST call `notifyCompletion(summary, status, resources_changed, gitops_commit)` as your final tool call before generating your final text response to the user. Do not skip this tool call under any circumstances when network changes are executed.


Always communicate clearly with the user, explaining your technical reasoning, showing the generated or modified CRD YAMLs, and confirming successful submission.
"""

root_agent = Agent(
    name="DesignerAgent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=5),
    ),
    description=description,
    instruction=root_prompt,
    tools=[
        getVyosDescriptors,
        getDeployedCRs,
        deploySpec,
        deleteSpec,
        getDesignDoc,
        deployDescriptor,
        deleteDescriptor,
        notifyCompletion,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
