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

from dotenv import load_dotenv

# Load environment variables from .env immediately to make sure they bind during imports
load_dotenv()

import datetime
import json
import logging
import os
import requests
import google.auth
from google.auth.transport.requests import Request
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from app.mcp_auth import mcp_header_provider
from google.genai import types

# Set up project environment
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

logger = logging.getLogger(__name__)

# Authoritative Reasoning Engine ID Map
AGENT_RUNTIME_MAP = {
    "detective": os.environ.get("DETECTIVEAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_DETECTIVE_ENGINE_ID"),
    "detectiveagent": os.environ.get("DETECTIVEAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_DETECTIVE_ENGINE_ID"),
    "impactagent": os.environ.get("IMPACTAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_IMPACT_ENGINE_ID"),
    "business-impact-assessor": (
        os.environ.get("BUSINESS-IMPACT-ASSESSORAGENT_ENGINE_ID")
        or os.environ.get("BUSINESS_IMPACT_ASSESSORAGENT_ENGINE_ID")
        or "projects/YOUR_PROJECT_NUMBER/locations/us-central1/reasoningEngines/6289456649774039040"
    ),
    "business-impact-assessoragent": (
        os.environ.get("BUSINESS-IMPACT-ASSESSORAGENT_ENGINE_ID")
        or os.environ.get("BUSINESS_IMPACT_ASSESSORAGENT_ENGINE_ID")
        or "projects/YOUR_PROJECT_NUMBER/locations/us-central1/reasoningEngines/6289456649774039040"
    ),
    "designeragent": os.environ.get("DESIGNERAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_DESIGNER_ENGINE_ID"),
    "testeragent": os.environ.get("TESTERAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_TESTER_ENGINE_ID"),
    "chaosagent": os.environ.get("CHAOSAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_CHAOS_ENGINE_ID"),
    "trust": os.environ.get("TRUSTAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_TRUST_ENGINE_ID"),
    "trustagent": os.environ.get("TRUSTAGENT_ENGINE_ID", "projects/YOUR_PROJECT_NUMBER/locations/europe-west3/reasoningEngines/YOUR_TRUST_ENGINE_ID"),
}


def delegate_to_agent(
    agent_name: str, 
    instruction: str
) -> str:
    """
    Directly execute an instruction or custom query against a specialized peer Reasoning Engine agent.
    This allows dynamic multi-agent collaboration without requiring manual copy-pasting.

    Args:
        agent_name (str): The exact shortname of the agent ('detective', 'impactagent', 'business-impact-assessor', 'designeragent', 'testeragent', 'chaosagent', 'trustagent')
        instruction (str): The detailed prompt or custom query to delegate to that agent

    Returns:
        str: The raw response and findings returned by that peer agent
    """
    agent_name = agent_name.lower().strip()
    
    # Route common name aliases to canonical AGENT_RUNTIME_MAP keys
    alias_map = {
        "logs": "detective",
        "logsagent": "detective",
        "detectiveagent": "detective",
        "chaos": "chaosagent",
        "tester": "testeragent",
        "designer": "designeragent",
        "impact": "impactagent",
        "business-impact-assessor": "business-impact-assessor",
        "business_impact_assessor": "business-impact-assessor",
        "businessimpactassessor": "business-impact-assessor",
        "business-impact-assessoragent": "business-impact-assessor",
        "business_impact_assessor_agent": "business-impact-assessor",
        "trust": "trustagent",
        "trustagent": "trustagent",
    }
    target_agent = alias_map.get(agent_name, agent_name)

    if target_agent not in AGENT_RUNTIME_MAP:
        return f"❌ Error: Unknown agent '{agent_name}'. Valid peer agents are: {', '.join(AGENT_RUNTIME_MAP.keys())}"

    engine_id = AGENT_RUNTIME_MAP[target_agent]
    logger.info(f"🔄 Delegating task to {target_agent} ({engine_id}):\n{instruction}")

    try:
        # Acquire fresh auth tokens for direct REST API invocation
        credentials, _ = google.auth.default()
        credentials.refresh(Request())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

        # Dynamically extract region from engine_id e.g. "projects/.../locations/europe-west3/..."
        parts = engine_id.split("/")
        region = "europe-west3"  # Fallback region
        if len(parts) >= 4 and parts[2] == "locations":
            region = parts[3]

        url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{engine_id}:query"

        # Route to the peer's registered unary `query` method. The Reasoning Engine
        # :query endpoint dispatches on `classMethod`; `input` becomes its kwargs.
        payload = {
            "classMethod": "query",
            "input": {
                "request": instruction
            }
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=300)

        if resp.status_code != 200:
            return (
                f"⚠️ Peer agent '{agent_name}' returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )

        data = resp.json()

        # Reasoning Engine wraps the method return value under "output".
        return str(data.get("output", data))

    except Exception as e:
        logger.error(f"Failed direct agent delegation to {agent_name}: {e}", exc_info=True)
        return f"⚠️ Error executing query against peer agent '{agent_name}': {str(e)}"


MCP_TOOLS_URL = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "https://mcp-networktools-YOUR_PROJECT_NUMBER.YOUR_GCP_REGION.run.app/sse")

description = """
Autonomous Network Supervisor Agent — Dynamic Multi-Agent Orchestrator & Troubleshooting Dispatcher.

Acts as an authoritative operations supervisor empowered to directly talk to and delegate specific queries
or workflows across your specialized peer agent team (Detective, Impact Assessment, Business Impact Assessor, Designer, Tester, Chaos, Trust).
"""

root_prompt = """You are the Autonomous Network Supervisor Agent, an elite telecommunications operations orchestrator.

## Your Superpower (Direct Peer Delegation):
You are equipped with the `delegate_to_agent` tool, which allows you to programmatically talk directly to and execute queries against any specialized peer agent in your ecosystem:
- **`detective`**: Discovers Kubernetes VNF deployments, queries live telemetry metrics, inspects active network faults, flags bottlenecks, and filters syslog entries.
- **`impactagent`**: Simulates OSPF/GNN blast radiuses and bottlenecks.
- **`business-impact-assessor`**: Assesses the business, customer, and revenue impact (Revenue Impact Index / customer exposure in USD) of network resources such as links and upgrade jobs, and ranks or prioritizes links according to their customer, business, or revenue impact.
- **`designeragent`**: Authors declarative VyOS CRDs and commits to GitOps.
- **`testeragent`**: Orchestrates synthetic traffic workloads.
- **`chaosagent`**: Injects controlled network degradation.
- **`trustagent`**: Validates network configuration changes against design rules.

## Golden Rules of Engagement:
1. **Propose Plan & Ask for Confirmation (except for Business/Customer Impact Ranking)**: Before calling any tools or delegating via `delegate_to_agent`, present your planned steps to the user and ask: "Would you like me to proceed with this plan?" Wait for their positive confirmation — **EXCEPT** when asked to rank, assess, or prioritize links or upgrade jobs according to their customer, business, or revenue impact, in which case you MUST immediately call `delegate_to_agent(agent_name="business-impact-assessor", instruction=...)` without waiting for confirmation.
2. **Ask Clarifying Questions**: If the user's request is ambiguous or lacks necessary details (e.g., missing specific router/device names, time windows, or interface names), do not assume. Prompt the user immediately with a list of clarifying questions to get the required information first.
3. **State & Stage Labeling**: Prepend every response with the current operational state or stage (e.g. `[Stage: RCA Triage - Logs Search]`, `[Stage: Spot Request - BGP Check]`, `[Stage: Business Impact Assessment - Link Ranking]`) to keep the user and conversation context aligned.
4. **Graceful Peer Failure Handling**: If `delegate_to_agent` returns an error or exception, do not dump raw technical errors. Explain clearly which downstream agent failed, what it was trying to do, and offer manual checks or alternative actions.

## Core Operational Scenarios:

### 1. Root Cause Analysis (RCA)
When troubleshooting an error, packet loss, or routing protocol failure:
1. Delegate to the `detective` agent to query active alarms and check router logs (syslog) for errors.
2. Delegate to the `impactagent` to check OSPF paths and measure peak link utilization.
3. Combine findings to identify the root cause, determine the blast radius, and recommend remediation.

### 2. Current Network Status (Health Check)
When asked for the current state or health of the network:
1. Delegate to `detective` to verify VNF pod health and retrieve active alarms.
2. Delegate to `impactagent` to retrieve current OSPF path states and peak link utilization.
3. Query active synthetic tests to see if any tests are running.
4. Present a consolidated health dashboard report in a markdown table.

### 3. New Service Activation & Validation
When asked to activate or deploy a new service (e.g. a new L3VPN or routing link):
1. Delegate to `designeragent` to design the new service configuration, validate IPAM against guidelines, and deploy the VyOS CRD.
2. Once applied, delegate to `testeragent` to set up and run a synthetic traffic test between the newly configured endpoints to validate SLA compliance.
3. Present the configuration summary along with the traffic test results.

### 4. Business & Customer Impact Assessment (Link Ranking & Upgrade Prioritization)
When asked to rank, assess, or prioritize links or link upgrade jobs according to their customer impact, business impact, revenue impact, or customer exposure:
1. Immediately invoke `delegate_to_agent` with `agent_name="business-impact-assessor"` and pass the user's request as `instruction` (do NOT ask for confirmation first).
2. Return the ranked links and customer exposure findings clearly in a formatted markdown table.

### 5. Spot Requests & Custom Routing
When asked a specific query or action for a downstream agent (e.g. *"Ask detective if the BGP peers on pe2 are established"*):
1. Identify the appropriate specialized agent based on their roles.
2. Draft a routing plan for the request, present it to the user, and ask for confirmation before executing.
3. Once confirmed, invoke `delegate_to_agent` and return the formatted response.

Maintain an authoritative, highly technical executive SRE persona at all times.
"""

root_agent = Agent(
    name="SupervisorAgent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=description,
    instruction=root_prompt,
    tools=[
        delegate_to_agent,
        MCPToolset(
            # timeout is the MCP session handshake budget, not the tool call.
            # ADK defaults it to 5s; mcp-networktools scales to zero, so a cold
            # start blew past that and the toolset came up with no tools at all --
            # the agent then hallucinated a tool name against an empty list. See
            # 3.2 in MIGRATION_PLAN.md.
            connection_params=SseConnectionParams(url=MCP_TOOLS_URL, timeout=30.0),
            # The tool server requires a Google OIDC ID token; ADK calls
            # this per invocation so the token is always current.
            header_provider=mcp_header_provider(MCP_TOOLS_URL),
            tool_filter=[
                "query_fault_events",
                "getRunningTests",
            ]
        )
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
