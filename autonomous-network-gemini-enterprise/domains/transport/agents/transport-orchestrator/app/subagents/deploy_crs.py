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

"""Validate CRs Subagent"""

import logging
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from .deploy_crs_prompt import DEPLOY_CRS_PROMPT
from app.config import DEBUG, MCP_AUTH_ENABLED, DEFAULT_MODEL, SHARED_MCP_URL
from app.mcp_auth import create_authenticated_mcp_client
from app.app_utils.callbacks import after_deploy_callback
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

logger = logging.getLogger(__name__)

class DeploymentResult(BaseModel):
    """Structured output matching CreateIntentResponse protocol."""
    success: bool = Field(description="Whether L3VPN service was created successfully")
    service_name: Optional[str] = Field(default=None, description="Name of the created L3VPN service from validation_result")
    status: Literal["Acknowledged", "Rejected"] = Field(description="TMF921 intent state - 'Acknowledged' if successful, 'Rejected' if failed")
    error_code: Optional[int] = Field(default=None, description="Error code if failed: 500 for deployment errors")
    message: str = Field(description="Human-readable result message summarizing deployment")

def create_deploy_crs_agent() -> LlmAgent:
    """Create the deploy CRs subagent

    Returns:
        Configured LlmAgent instance
    """
    # Create agent with structured output
    agent = LlmAgent(
        name="DeployCRsAgent",
        model=DEFAULT_MODEL,
        description="Deploys generated CRs to gitea",
        instruction=DEPLOY_CRS_PROMPT,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                thinking_level="medium",
                include_thoughts=False
            )
        ),
        tools=[
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=SHARED_MCP_URL, 
                    **({"httpx_client_factory": create_authenticated_mcp_client} if MCP_AUTH_ENABLED else {})
                ),
                tool_filter=['deployDescriptors']
            )
        ],
        output_schema=DeploymentResult,
        output_key='deployment_result',
        after_agent_callback=after_deploy_callback,
    )

    return agent