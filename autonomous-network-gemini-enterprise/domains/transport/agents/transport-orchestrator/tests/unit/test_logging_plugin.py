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
import pytest
from unittest.mock import MagicMock, patch
from app.app_utils.logging_plugin import DevLoggerPlugin


@pytest.mark.asyncio
async def test_dev_logger_plugin_agent_callbacks():
    plugin = DevLoggerPlugin()
    agent = MagicMock()
    agent.name = "TestAgent"

    ctx = MagicMock()
    ctx.invocation_id = "inv-123"
    ctx.session.id = "sess-456"
    ctx.agent_name = "TestAgent"
    ctx.user_content = "Hello agent"
    ctx.output = {"result": "success"}
    ctx.state = {"intent_id": "999"}

    await plugin.before_agent_callback(agent=agent, callback_context=ctx)
    await plugin.after_agent_callback(agent=agent, callback_context=ctx)
    await plugin.on_agent_error_callback(agent=agent, error=ValueError("boom"), callback_context=ctx)


@pytest.mark.asyncio
async def test_dev_logger_plugin_model_callbacks():
    plugin = DevLoggerPlugin()

    ctx = MagicMock()
    ctx.invocation_id = "inv-123"
    ctx.session.id = "sess-456"
    ctx.agent_name = "TestAgent"

    llm_req = MagicMock()
    llm_req.model = "gemini-2.5-flash"
    llm_req.config.system_instruction = "System prompt"
    llm_req.contents = ["User prompt"]
    llm_req.tools_dict = {"tool_a": MagicMock()}

    llm_resp = MagicMock()
    llm_resp.error_code = None
    llm_resp.content = "LLM response text"
    llm_resp.turn_complete = True
    llm_resp.usage_metadata.prompt_token_count = 100
    llm_resp.usage_metadata.candidates_token_count = 50

    await plugin.before_model_callback(callback_context=ctx, llm_request=llm_req)
    await plugin.after_model_callback(callback_context=ctx, llm_response=llm_resp)
    await plugin.on_model_error_callback(callback_context=ctx, llm_request=llm_req, error=RuntimeError("timeout"))


@pytest.mark.asyncio
async def test_dev_logger_plugin_tool_callbacks():
    plugin = DevLoggerPlugin()

    tool = MagicMock()
    tool.name = "validate_intent_tool"

    tool_ctx = MagicMock()
    tool_ctx.invocation_id = "inv-123"
    tool_ctx.function_call_id = "func-789"
    tool_ctx.agent_name = "PlannerAgent"

    tool_args = {"intent_json": "{\"id\": 1}"}
    tool_result = {"status": "valid"}

    await plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_ctx)
    await plugin.after_tool_callback(
        tool=tool, tool_args=tool_args, tool_result=tool_result, tool_context=tool_ctx
    )
    await plugin.on_tool_error_callback(
        tool=tool, tool_args=tool_args, error=Exception("tool fail"), tool_context=tool_ctx
    )


@pytest.mark.asyncio
async def test_debug_mode_disabled_truncates_content():
    """Test that content is truncated when DEBUG is not true"""
    with patch.dict(os.environ, {"DEBUG": "false"}, clear=False):
        plugin = DevLoggerPlugin()
        assert plugin.debug_mode is False
        assert plugin.max_length == 400

        # Test with long content
        long_text = "x" * 1000
        result = plugin._format_content(long_text)
        assert len(result) < 500  # Should be truncated
        assert result.endswith("...")


@pytest.mark.asyncio
async def test_debug_mode_enabled_shows_full_content():
    """Test that content is NOT truncated when DEBUG=true"""
    with patch.dict(os.environ, {"DEBUG": "true"}, clear=False):
        plugin = DevLoggerPlugin()
        assert plugin.debug_mode is True
        assert plugin.max_length is None

        # Test with long content
        long_text = "x" * 1000
        result = plugin._format_content(long_text)
        assert len(result) == 1000  # Should NOT be truncated
        assert not result.endswith("...")


@pytest.mark.asyncio
async def test_debug_mode_shows_inline_data():
    """Test that inline_data content is shown in debug mode"""
    with patch.dict(os.environ, {"DEBUG": "true"}, clear=False):
        plugin = DevLoggerPlugin()

        # Create mock content with inline_data
        mock_part = MagicMock()
        mock_part.text = None
        mock_part.function_call = None
        mock_part.function_response = None
        mock_part.file_data = None
        mock_part.inline_data = MagicMock()
        mock_part.inline_data.mime_type = "application/json"
        mock_part.inline_data.data = b'{"key": "value"}'

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        result = plugin._format_content(mock_content)
        assert "application/json" in result
        assert '{"key": "value"}' in result


@pytest.mark.asyncio
async def test_model_callback_shows_tools():
    """Test that model request logs show available tools"""
    plugin = DevLoggerPlugin()

    ctx = MagicMock()
    ctx.invocation_id = "inv-123"
    ctx.session.id = "sess-456"
    ctx.agent_name = "TestAgent"

    llm_req = MagicMock()
    llm_req.model = "gemini-3.6-flash"
    llm_req.config.system_instruction = "System prompt"
    llm_req.contents = []
    llm_req.tools_dict = {"getTransportDesignDoc": MagicMock(), "getTransportCRDs": MagicMock()}

    # This should log "Available Tools (dict): ['getTransportDesignDoc', 'getTransportCRDs']"
    await plugin.before_model_callback(callback_context=ctx, llm_request=llm_req)


@pytest.mark.asyncio
async def test_tool_logging_with_debug_mode():
    """Test that tool calls show detailed JSON input/output in debug mode"""
    with patch.dict(os.environ, {"DEBUG": "true"}, clear=False):
        plugin = DevLoggerPlugin()
        assert plugin.debug_mode is True

        tool = MagicMock()
        tool.name = "getTransportDesignDoc"
        tool.description = "Retrieves the transport network design document"

        tool_ctx = MagicMock()
        tool_ctx.invocation_id = "inv-123"
        tool_ctx.agent_name = "PlannerAgent"

        tool_args = {"repo_name": "transport-design", "file_path": "design.md"}
        tool_result = {"content": "# Design\nSome design content", "status": "success"}

        # Should log with JSON formatting
        await plugin.before_tool_callback(tool=tool, tool_args=tool_args, tool_context=tool_ctx)
        await plugin.after_tool_callback(
            tool=tool, tool_args=tool_args, tool_result=tool_result, tool_context=tool_ctx
        )
