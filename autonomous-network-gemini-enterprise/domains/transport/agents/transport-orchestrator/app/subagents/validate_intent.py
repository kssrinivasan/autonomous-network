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

"""Validate Intent Subagent"""

import logging
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from ..config import DEFAULT_MODEL
from ..app_utils.callbacks import after_validate_callback
from .validate_intent_prompt import VALIDATE_INTENT_PROMPT

logger = logging.getLogger(__name__)

class ValidationResult(BaseModel):
    """Validation result schema matching CreateIntentResponse protocol"""
    intent_id: Optional[str] = Field(None, description="Intent UUID copied from intent.id — used by Spanner callbacks")
    success: bool = Field(..., description="Whether validation passed")
    service_name: str = Field(..., description="Sanitized service name for CR generation")
    status: Literal["Acknowledged", "Rejected"] = Field(..., description="TMF921 intent state - Acknowledged if accepted, Rejected if failed")
    error_code: Optional[int] = Field(None, description="HTTP-style error code if failed (400, 409, 424, 500)")
    message: str = Field(..., description="Human-readable result message summarizing validation outcome")
    errors: List[str] = Field(default_factory=list, description="Detailed list of validation errors (for logging)")
    warnings: List[str] = Field(default_factory=list, description="List of validation warnings (for logging)")


def create_validate_intent_agent() -> LlmAgent:
    """Create the validate intent subagent

    Returns:
        Configured LlmAgent instance
    """

    # Create agent with structured output
    agent = LlmAgent(
        name="ValidateIntentAgent",
        model=DEFAULT_MODEL,
        description="Validates TMF921 L3ConnectivityIntent against schema and infrastructure",
        instruction=VALIDATE_INTENT_PROMPT,
        planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=-1)),
        output_schema=ValidationResult,
        output_key='validation_result',
        after_agent_callback=after_validate_callback,
    )

    return agent