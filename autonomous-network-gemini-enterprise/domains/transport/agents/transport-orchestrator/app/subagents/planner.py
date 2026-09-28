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

"""Planner Subagent"""

import logging
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from .planner_prompt import PLANNER_PROMPT

from app.config import DEBUG, MCP_AUTH_ENABLED, DEFAULT_MODEL, SHARED_MCP_URL
from app.app_utils.callbacks import after_plan_callback
from app.mcp_auth import create_authenticated_mcp_client

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

logger = logging.getLogger(__name__)

# Pydantic models for structured output
class ProposedChange(BaseModel):
    """A single proposed network change."""

    action: Literal['Create', 'Update', 'Delete'] = Field(
        description="The action to take on the CRD. Must be exactly 'Create', 'Update', or 'Delete'."
    )
    resource_type: str = Field(
        description="The resource type: VyOSInfrastructure, VyOSUnderlay, or VyOSL3VPN"
    )
    resource_name: str = Field(
        description="The name of the resource to create, update or delete"
    )
    description: str = Field(
        description=(
            "Technical description including all key parameters needed to generate "
            "the CRD (e.g. RD, RT, interface names, IP addresses, loopback addresses)"
        )
    )
    depends_on: List[str] = Field(
        default=[],
        description="Names of other resources in this plan that must be created first"
    )


class NetworkChangePlan(BaseModel):
    """Structured output from the network planner."""

    needs_clarification: Optional[str] = Field(
        default=None,
        description=(
            "If the planner is missing critical information to produce a correct plan, "
            "set this to a specific question for the user. Leave null if a plan can be generated."
        )
    )
    reasoning: Optional[str] = Field(
        default=None,
        description=(
            "Step-by-step reasoning for the plan based on design rules and current state. "
            "Only populated when proposed_changes is also set."
        )
    )
    proposed_changes: Optional[List[ProposedChange]] = Field(
        default=None,
        description=(
            "The ordered list of proposed network changes. "
            "Only populated when the planner has enough information to generate a complete plan."
        )
    )


def create_planner_agent() -> LlmAgent:
    """Create the planner subagent

    Returns:
        Configured LlmAgent instance
    """
    # Create agent with structured output
    agent = LlmAgent(
        name="PlannerAgent",
        model=DEFAULT_MODEL,
        description="Decomposes Transport Intent into a network configuration plan",
        instruction=PLANNER_PROMPT,
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
                tool_filter=['getTransportDesignDoc', 'getTransportCRDs','getDeployedTransportCRs']
            )
        ],
        output_schema=NetworkChangePlan,
        output_key='change_plan',
        after_agent_callback=after_plan_callback,
    )

    return agent

