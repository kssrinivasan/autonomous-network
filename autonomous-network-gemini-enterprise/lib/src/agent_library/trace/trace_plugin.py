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

from google.cloud import spanner
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from typing import Any
import datetime
import json
import uuid
import logging
import os
from .trace_context import TracingContext
from ..credentials.creds import get_credentials

logger = logging.getLogger(__name__)

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

class TracePlugin(BasePlugin):
    """A custom plugin that publishes agent and tool events to Pub/Sub."""

    def _generate_span_id(self):
        return str(uuid.uuid4())[:16]

    def _generate_event_id(self):
        return str(uuid.uuid4())

    def __init__(self) -> None:
        """Initialize the plugin with counters."""
        logger.debug("init pub sub plugin")

        super().__init__(name="trace_publisher")

        # Store parent_span_id for each span so we can use it in handle_end
        # Key: span_id, Value: parent_span_id
        self.span_parents = {}

        # setup spanner
        creds, proj = get_credentials()
        self.spanner_client = spanner.Client(credentials=creds)
        self.spanner_instance = self.spanner_client.instance(SPANNER_INSTANCE)
        self.spanner_database = self.spanner_instance.database(SPANNER_DATABASE)

    def _write_to_spanner(self, event):
        """Write the event to Spanner."""
        details_json = None
        if "details" in event and event["details"] is not None:
            details_json = json.dumps(event["details"])

        def _insert_event(transaction):
            transaction.insert(
                table="AgentTrace",
                columns=("id", "event_type", "trace_id", "span_id", "parent_span_id", "operation_name", "timestamp", "details"),
                values=[
                    (
                        event["id"],
                        event["event_type"],
                        event["trace_id"],
                        event["span_id"],
                        event["parent_span_id"],
                        event["operation_name"],
                        spanner.COMMIT_TIMESTAMP,
                        details_json,
                    )
                ],
            )
        self.spanner_database.run_in_transaction(_insert_event)
        logger.debug(f"   📝 Written to Spanner: {event['id']}")

    def handle_start(self, event_type, name, details=None):
        logger.debug("handle start event")

        trace_id = TracingContext.get_trace_id()
        parent_span_id = TracingContext.get_current_parent_id()
        current_span_id = self._generate_span_id()

        # Store the parent for this span so we can use it in handle_end
        self.span_parents[current_span_id] = parent_span_id

        # Push onto the stack so any sub-agents see this as the parent
        TracingContext.push_span(current_span_id)

        # Publish Event
        logger.debug(f"📢 [{event_type}] {name}")
        logger.debug(f"   Trace: {trace_id} | Span: {current_span_id} | Parent: {parent_span_id}")
        
        event = {
            "id": self._generate_event_id(),
            "event_type": event_type,
            "trace_id": trace_id,
            "span_id": current_span_id,
            "parent_span_id": parent_span_id,
            "operation_name": name,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

        if details is not None:
            event["details"]=details

        logger.info(event)

        self._write_to_spanner(event)

    def handle_end(self, event_type, name, details=None):
        logger.debug("handle end event")
        
        # Get the current span BEFORE popping
        current_span_id = TracingContext.get_current_span_id()
        trace_id = TracingContext.get_trace_id()
        
        # Use the stored parent_span_id from when this span was created
        # This ensures we use the same parent as in the START event,
        # even if other async callbacks have modified the stack
        parent_span_id = self.span_parents.get(current_span_id)
        
        # Log what we're publishing
        logger.debug(f"🏁 [{event_type}] {name}")
        logger.debug(f"   Trace: {trace_id} | Span: {current_span_id} | Parent: {parent_span_id}")
        
        event = {
            "id": self._generate_event_id(),
            "event_type": event_type,
            "trace_id": trace_id,
            "span_id": current_span_id,  # Same as START event
            "parent_span_id": parent_span_id,  # Same as START event
            "operation_name": name,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        if details is not None:
            event["details"]=details

        self._write_to_spanner(event)

        # Pop the span and clean up the stored parent
        TracingContext.pop_span()
        if current_span_id in self.span_parents:
            del self.span_parents[current_span_id]
            
        logger.debug(f"   Published and popped span")

    async def before_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        """Publish agent start event."""
        details = {
            "agent_name": agent.name,
        }
        if hasattr(agent, "instruction") and isinstance(agent.instruction, str):
            details["instruction"] = agent.instruction
        self.handle_start("BEFORE_AGENT", callback_context.agent_name, details)

    async def after_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        """Publish agent end event."""
        details = {}
        self.handle_end("AFTER_AGENT", callback_context.agent_name, details)

    async def before_tool_callback(self, *, tool: BaseTool, tool_args: dict, tool_context: ToolContext) -> None:
        """Publish tool start event."""
        details = {
            "tool_name": tool.name,
            "tool_args": tool_args,
        }
        self.handle_start("BEFORE_TOOL", tool_context.agent_name, details)

    async def after_tool_callback( self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict) -> None:
        """Publish tool end event."""
        details = {
            "result": result,
        }
        self.handle_end("AFTER_TOOL", tool_context.agent_name, details)

    async def on_tool_error_callback(self, *, tool: BaseTool, tool_args: dict, tool_context: ToolContext, error: Exception) -> None:
        """Publish tool error event."""
        details = {
            "error": str(error),
        }
        self.handle_end("TOOL_ERROR", tool_context.agent_name, details)

    async def before_model_callback(self, *, callback_context: CallbackContext, llm_request: LlmRequest) -> None:
        """Publish model start event."""
        details = {}
        if llm_request.model:
            details["model_version"] = llm_request.model
        if llm_request.config.system_instruction:
            details["system_instruction"] = str(llm_request.config.system_instruction)
        
        self.handle_start("BEFORE_MODEL", callback_context.agent_name, details)

    async def after_model_callback(self, *, callback_context: CallbackContext, llm_response: LlmResponse) -> None:
        """Publish model end event."""
        details = {
            "model_version": llm_response.model_version,
        }
        if llm_response.usage_metadata:
            details["usage_metadata"] = {
                "prompt_token_count": llm_response.usage_metadata.prompt_token_count,
                "candidates_token_count": llm_response.usage_metadata.candidates_token_count,
                "total_token_count": llm_response.usage_metadata.total_token_count,
            }
        if llm_response.content and llm_response.content.parts:
            # Assuming the first part is text
            details["text"] = llm_response.content.parts[0].text

        self.handle_end("AFTER_MODEL", callback_context.agent_name, details)

    async def on_model_error_callback(self, *, callback_context: CallbackContext, llm_request: LlmRequest, error: Exception) -> None:
        """Publish model error event."""
        details = {
            "error": str(error),
        }
        self.handle_end("MODEL_ERROR", callback_context.agent_name, details)
