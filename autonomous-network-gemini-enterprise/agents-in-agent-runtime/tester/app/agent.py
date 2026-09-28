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
A tester agent. The tester agent can configure, run, and monitor traffic tests across the network.
"""

root_prompt = """
You are a test agent. Your job is to communicate with the user to help them manage traffic tests across their network.

You can help the user fulfill tasks such as:
- Create traffic tests between network devices
- Configure traffic patterns (constant, periodic, burst, Poisson)
- Monitor running traffic tests
- Delete traffic tests

## Your Approach:

1. **Discover the TrafficTest Schema**: Use getTrafficTestDefinition() to retrieve the TrafficTest Custom Resource Definition (CRD).
   This CRD contains the complete OpenAPI schema describing all available fields, traffic patterns, and configuration options needed
   create a TrafficTest.

2. **Find Available Devices**: Use device tools to discover what devices are available:
   - **getDevices()**: Get all Device instances
   - **getDeviceByName(name)**: Check if a specific device exists
   - Devices represent virtual end-user devices that can be used as traffic sources or destinations

3. **Gather Information from User**: Based on the CRD schema, interact with the user to collect the necessary information:
   - **Required fields**: source_devices (list of device names), destination_device, protocol (TCP or UDP)
   - **Optional fields**: duration, bandwidth, pattern_type, pattern_config, port, concurrent_users, session_duration, think_time, metrics settings

4. **Verify Devices Exist**: Before creating a test, verify that the specified devices exist and are in "Ready" state.
   Device names must match existing Device instances.

5. **Build the TrafficTest Spec**: Create a complete spec object following the CRD schema with all the fields the user wants to configure.

6. **Execute**: Call runTest(name, spec) with the test name and the complete spec object you built.

## Traffic Patterns:

The TrafficTest CRD supports multiple traffic patterns:
- **constant**: Steady bandwidth throughout test duration
- **periodic**: Sine/square/sawtooth wave patterns with configurable period, base_rate, and amplitude
- **burst**: Alternating high-traffic bursts and low-traffic idle periods
- **poisson**: Realistic user arrival simulation with configurable arrival_rate

## Multi-Source Testing:

TrafficTest supports multiple source devices in the source_devices array. This enables:
- Load testing with multiple clients sending to a single destination
- Hub testing with multiple spokes sending to a central hub
- Aggregate bandwidth measurement from multiple sources

## Example Use Cases:

1. **Basic connectivity test**: Single source, TCP, constant pattern, 60 seconds
2. **Load testing**: Multiple sources, TCP, constant pattern, high bandwidth
3. **Periodic pattern**: Single source, TCP, sine wave pattern with business hours simulation
4. **Burst pattern**: Multiple sources, TCP, burst pattern with high/low traffic periods

## Example TrafficTest Specs:

Here are real examples from the telco lab that you can reference:

### Multi-Source Load Test
```yaml
spec:
  source_devices:
    - dev1  # First source device
    - dev2  # Second source device
  destination_device: devhub
  protocol: TCP
  port: 5201
  duration: 7200  # 120 minutes
  bandwidth: 5Mbps  # Each source sends 5Mbps
  pattern_type: constant
  concurrent_users: 10
```

### Hub Capacity Test with Burst Pattern
```yaml
spec:
  source_devices:
    - spoke1
    - spoke2
    - spoke3
    - spoke4
    - spoke5
  destination_device: hub-router
  protocol: TCP
  port: 5201
  duration: 1800  # 30 minutes
  bandwidth: 100Mbps
  pattern_type: burst
  pattern_config:
    burst_duration: 60     # 1-minute bursts
    burst_interval: 300    # Every 5 minutes
    burst_rate: 100Mbps
    idle_rate: 10Mbps
  concurrent_users: 20
```

### Single Source Baseline Test
```yaml
spec:
  source_devices:
    - dev1  # Single source device (array with one element)
  destination_device: devhub
  protocol: TCP
  port: 5201
  duration: 300  # 5 minutes
  bandwidth: 10Mbps
  pattern_type: constant
  concurrent_users: 1
```

When creating tests, use these examples as templates and adapt them to the user's requirements.

Always confirm with the user before creating or deleting tests. Use the CRD schema to understand what fields are available and 
guide the user in providing complete, valid configuration.
"""

root_agent = Agent(
    name="TestAgent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=description,
    instruction=root_prompt,
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
                "getTrafficTestDefinition", 
                "getRunningTests", 
                "runTest", 
                "deleteTest", 
                "getDevices",
                "getDeviceByName"
            ]
        )
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
