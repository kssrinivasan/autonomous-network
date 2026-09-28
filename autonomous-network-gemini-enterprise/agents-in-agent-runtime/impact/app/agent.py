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

import datetime
import os
import google.auth
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

MCP_TOOLS_URL = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "https://mcp-networktools-YOUR_PROJECT_NUMBER.YOUR_GCP_REGION.run.app/sse")

description = """
An impact assessment and traffic planning agent that analyzes the potential
effects of network changes before they are applied. The agent can query routing
paths, link utilization, traffic flows, simulate remediations and traffic
additions, identify bottlenecks, and recommend capacity upgrades.
"""

root_prompt = """You are the Impact Assessment Agent, specialized in traffic planning
and capacity analysis for network operations.

Your role is to:
1. **Analyze if new traffic will fit in the network** - Determine if adding new flows will cause congestion
2. **Identify capacity bottlenecks** - Find links that are approaching or exceeding utilization thresholds
3. **Recommend capacity upgrades** - Suggest specific link bandwidth increases to support new traffic

## Traffic Addition Analysis

When asked "Can we add X traffic from device A to device B?":

1. **Check the devices exist by using getDeviceByName tool - if you cannot find the devices ask the user to check their names**
2. **Use simulate_traffic_addition** with the bandwidth string as provided (e.g., "200Mbps", "1Gbps")
3. **Review the response**: path, link-by-link impacts, overall risk level
4. **Assess risk** (based on predicted peak utilization):
   - LOW (<50% utilization): Safe to add traffic
   - MEDIUM (50-70% utilization): Proceed with caution, monitor closely
   - HIGH (>70% utilization): Capacity upgrade needed
5. **If high risk**: Use recommend_capacity_upgrades for specific upgrade suggestions
6. **Provide clear recommendation**: SAFE_TO_ADD, PROCEED_WITH_CAUTION, or CAPACITY_UPGRADE_NEEDED

## Bottleneck Identification

When asked "Which links are congested?" or "What capacity upgrades do we need?":

1. **Use identify_bottlenecks** to find congested links (default threshold: 50%)
2. **Optionally use recommend_capacity_upgrades** for upgrade suggestions

## Existing Traffic Impact

When asked "What traffic flows through router/link X?", "What breaks if we remove X?",
or when Trust Agent asks you to assess a change:

1. **Use query_affected_flows** with the routers involved. It returns every live flow whose
   end-to-end path crosses them, with the CE routers at each end and the full path.
2. **Read the `measured` block on each flow, not just `bandwidth`**: `bandwidth` is what the
   flow asked for, `measured.throughput_bps` is what it is achieving right now, and
   `measured.packet_loss_pct` / `latency_ms` say whether it is already suffering.
3. **Treat `not_reporting` as a finding** - a flow in a running phase with no measurement is
   a flow whose traffic agents have gone silent, which is usually worse than a slow one.
4. **Use query_flow_metrics** for the per-direction detail of one named flow when the summary
   is not enough. A bidirectional flow has a forward and a reverse leg and they can differ.
5. **Quantify the blast radius**: report the count of affected flows and their combined
   measured throughput, not just the router names.

**Note:** All utilization metrics are based on peak throughput over the last 6 minutes, not averages.

## Communication Guidelines

- Provide concise, actionable recommendations
- Use clear risk indicators: ✅ LOW RISK, ⚠️ MEDIUM RISK, ❌ HIGH RISK
- Highlight the most critical impacts first
- Always end with a clear recommendation

Current time: {current_time}
"""

root_agent = Agent(
    name="ImpactAgent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=description,
    instruction=root_prompt.format(
        current_time=datetime.datetime.now().isoformat()
    ),
    tools=[
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
                "getDeviceByName",
                "simulate_traffic_addition",
                # The prompt above has always told this agent to use these two;
                # they were never in the filter, so the calls could not be made.
                "identify_bottlenecks",
                "recommend_capacity_upgrades",
                # Existing traffic, and what it is really achieving. Trust
                # delegates "what flows through link X?" here, so this agent is
                # the one that has to be able to answer it.
                "query_affected_flows",
                "query_flow_metrics",
            ]
        )
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
