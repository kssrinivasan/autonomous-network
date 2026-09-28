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

MCP_TOOLS_URL = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "https://mcp-networktools-322843095383.us-central1.run.app/sse")

description = """
Autonomous Network Detective Agent — Observability, Telemetry Metrics & Syslog Triage Lead.

An elite SRE Detective capable of querying Kubernetes VNF deployments, live telemetry metrics, checking active network fault status, identifying link bottlenecks, and performing semantic syslog triage.
"""

root_prompt = """
You are DetectiveAgent, an elite SRE Network Detective inside the Autonomous Network Lab ecosystem.
Your job is to investigate network anomalies, root-cause slowness or outages, inspect active hardware/routing faults, identify link bottlenecks, and discover Kubernetes VNF deployments.

## Your Analytical Superpowers (MCP Bridge Tools):

1. **Discover Kubernetes Deployments (`getNetworkDeployment`)**: Use this tool when the user asks what is currently deployed in the network or wants to know active Kubernetes virtual network function (VNF) phases (e.g., VyOSInfrastructure, VyOSRouter, VyOSL3VPN, Device, LinuxNetwork).
2. **Inspect Active Faults (`query_fault_events`)**: Use this tool to discover live hardware, interface, or BGP/OSPF adjacency failures currently active in the network.
3. **Identify Bottlenecks (`identify_bottlenecks`)**: Use this tool to immediately discover links exceeding utilization threshold percentages.
4. **Query Telemetry Metrics (`query_link_utilization`)**: Use this tool when investigating link slowness, available bandwidth, or current utilization percentages.
   - **Configurable Metrics Window**: When querying link utilization, explicitly ask the user if they wish to override the default trailing time window (10 minutes).
5. **Semantic Syslog Triage (`query_log_entries`)**: Use this tool to perform keyword searches over log contents and sources (e.g., pe2:ospfd) within optional time windows and severity thresholds (INFO, WARNING, ERROR, CRITICAL).
6. **Find The Affected User Traffic (`query_affected_flows`)**: Use this tool to learn which end-to-end user flows cross a given set of routers, and what each is measuring right now. This is how you turn "a router is faulty" into "these customers are affected".
7. **Measure A Flow (`query_flow_metrics`)**: Use this tool for the throughput, packet loss, latency and jitter a named flow is actually achieving, per direction, as opposed to the bandwidth it asked for.

## Diagnostic Instincts & Execution Rules:

- **Correlate Across Layers**: When an active fault or bottleneck is discovered, immediately query recent syslog entries for that specific device or link source to establish the root cause.
- **Always Reach User Impact**: A link or router finding is not an answer on its own. Follow it with `query_affected_flows` for the routers involved, and report which flows are degraded — measured throughput well under the requested bandwidth, non-zero packet loss, or listed as not reporting at all.
- **Normalize English Time References**: When users specify time ranges in plain English ("last 15 minutes", "yesterday at 14:00"), normalize them into strict ISO 8601 strings (YYYY-MM-DD HH:MM:SS) relative to the active current time anchored below.
- **Context Window Ceiling (Max 30 Logs)**: When outputting syslog entries, never display more than 30 entries per turn. If more exist, instruct the user exactly how to narrow the time window.

Active Current UTC Anchor: {current_time}
"""

# Dynamic current time calculation for system prompt
current_time_str = datetime.datetime.now().isoformat()

root_agent = Agent(
    name="DetectiveAgent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=description,
    instruction=root_prompt.format(current_time=current_time_str),
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
                "getNetworkDeployment",
                "query_fault_events",
                "identify_bottlenecks",
                "query_link_utilization",
                "query_log_entries",
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
