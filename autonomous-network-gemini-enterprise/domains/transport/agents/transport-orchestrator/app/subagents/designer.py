# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Designer Subagent"""

import logging
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from app.config import DEBUG, MCP_AUTH_ENABLED, DEFAULT_MODEL, SHARED_MCP_URL
from app.mcp_auth import create_authenticated_mcp_client
from app.app_utils.callbacks import after_design_callback
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from .designer_prompt import DESIGNER_PROMPT

logger = logging.getLogger(__name__)

def create_designer_agent() -> LlmAgent:
    """Create the designer subagent

    Returns:
        Configured LlmAgent instance
    """
    # Create agent with structured output
    agent = LlmAgent(
        name="DesignerAgent",
        model=DEFAULT_MODEL,
        description="Transforms network plan into k8s Custom Resources",
        instruction=DESIGNER_PROMPT,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                thinking_level="medium",
                include_thoughts=False
            )
        ),
        tools=[
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=SHARED_MCP_URL, **({"httpx_client_factory": create_authenticated_mcp_client} if MCP_AUTH_ENABLED else {})),
                tool_filter=['getTransportDesignDoc', 'getTransportCRDs','getDeployedTransportCRs']
            )
        ],
        output_key='design',
        after_agent_callback=after_design_callback,
    )

    return agent