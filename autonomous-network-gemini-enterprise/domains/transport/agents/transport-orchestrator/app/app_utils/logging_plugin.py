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

"""Development Logging Plugin for ADK Agent Execution Tracing.

Provides real-time console logging for agent lifecycle events, tool calls,
model calls, and error handling during local development.

NOTE: This plugin is only loaded when DEBUG=true in .env
"""

import logging
import os, sys
from typing import Any
from google.adk.plugins import BasePlugin

# Configure logger for the plugin
logger = logging.getLogger("transport-orchestrator.dev_logger")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# Optional ANSI colors for terminal readability
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"

def _is_tty() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class DevLoggerPlugin(BasePlugin):
    """ADK plugin that logs execution callbacks to console for local debugging."""

    def __init__(self, name: str = "dev_logger"):
        super().__init__(name=name)
        self.use_color = _is_tty() and os.getenv("NO_COLOR") is None
        # When DEBUG=true, disable truncation to show full content
        self.debug_mode = os.getenv("DEBUG", "").lower() == "true"
        self.max_length = None if self.debug_mode else 400

    def _format(self, color: str, prefix: str, msg: str) -> str:
        if self.use_color:
            return f"{color}{prefix}{_RESET} {msg}"
        return f"{prefix} {msg}"

    def _format_ctx(
        self,
        ctx: Any = None,
        agent: Any = None,
        tool_context: Any = None,
        invocation_context: Any = None,
    ) -> list[str]:
        """Extract and format context details into readable log lines."""
        lines = []
        effective_ctx = ctx or tool_context or invocation_context

        agent_name = None
        if agent and hasattr(agent, "name"):
            agent_name = agent.name
        elif effective_ctx and hasattr(effective_ctx, "agent_name") and effective_ctx.agent_name:
            agent_name = effective_ctx.agent_name
        elif effective_ctx and hasattr(effective_ctx, "agent") and hasattr(effective_ctx.agent, "name"):
            agent_name = effective_ctx.agent.name

        if agent_name:
            lines.append(f"Agent: {agent_name}")

        if effective_ctx:
            inv_id = getattr(effective_ctx, "invocation_id", None)
            if inv_id:
                lines.append(f"Invocation ID: {inv_id}")

            session = getattr(effective_ctx, "session", None)
            if session and hasattr(session, "id"):
                lines.append(f"Session ID: {session.id}")

            branch = getattr(effective_ctx, "branch", None)
            if branch:
                lines.append(f"Branch: {branch}")

            func_id = getattr(effective_ctx, "function_call_id", None)
            if func_id:
                lines.append(f"Function Call ID: {func_id}")

            user_id = getattr(effective_ctx, "user_id", None)
            if user_id:
                lines.append(f"User ID: {user_id}")

            app_name = getattr(effective_ctx, "app_name", None)
            if app_name:
                lines.append(f"App Name: {app_name}")

            state = getattr(effective_ctx, "state", None)
            if state is not None:
                try:
                    if hasattr(state, "keys"):
                        keys = list(state.keys())
                        if keys:
                            lines.append(f"State Keys: {keys}")
                    elif isinstance(state, dict) and state:
                        lines.append(f"State Keys: {list(state.keys())}")
                except Exception:
                    pass

        return lines

    def _format_content(self, content: Any, max_length: int = None) -> str:
        """Format content or message parts into a human-readable string."""
        if max_length is None:
            max_length = self.max_length or 999999  # No limit in debug mode

        if not content:
            return "None"
        if isinstance(content, str):
            return content if (self.debug_mode or len(content) <= max_length) else content[:max_length] + "..."
        if hasattr(content, "parts") and content.parts:
            parts_str = []
            for p in content.parts:
                if hasattr(p, "text") and p.text:
                    t = p.text.strip()
                    if not self.debug_mode and len(t) > max_length:
                        t = t[:max_length] + "..."
                    parts_str.append(f"text: '{t}'")
                elif hasattr(p, "function_call") and p.function_call:
                    fc = p.function_call
                    args_str = str(getattr(fc, "args", ""))
                    if not self.debug_mode and len(args_str) > max_length:
                        args_str = args_str[:max_length] + "..."
                    parts_str.append(f"function_call: {getattr(fc, 'name', 'unnamed')}({args_str})")
                elif hasattr(p, "function_response") and p.function_response:
                    fr = p.function_response
                    resp_str = str(getattr(fr, "response", ""))
                    if not self.debug_mode and len(resp_str) > max_length:
                        resp_str = resp_str[:max_length] + "..."
                    parts_str.append(f"function_response: {getattr(fr, 'name', 'unnamed')} -> {resp_str}")
                elif hasattr(p, "inline_data") and p.inline_data:
                    # In debug mode, show the actual data
                    mime = getattr(p.inline_data, 'mime_type', 'binary')
                    if self.debug_mode and hasattr(p.inline_data, 'data'):
                        data = getattr(p.inline_data, 'data', '')
                        if isinstance(data, bytes):
                            try:
                                data = data.decode('utf-8')
                            except:
                                data = f"<binary {len(data)} bytes>"
                        parts_str.append(f"inline_data [{mime}]: {data}")
                    else:
                        parts_str.append(f"inline_data: {mime}")
                elif hasattr(p, "file_data") and p.file_data:
                    parts_str.append(f"file_data: {getattr(p.file_data, 'file_uri', 'file')}")
                else:
                    parts_str.append(str(p))
            return " | ".join(parts_str)

        content_str = str(content)
        return content_str if (self.debug_mode or len(content_str) <= max_length) else content_str[:max_length] + "..."

    def _format_contents(self, contents: Any, max_length: int = None) -> str:
        """Format a list of content items (prompt messages)."""
        if max_length is None:
            max_length = self.max_length or 999999

        if not contents:
            return "None"
        if not isinstance(contents, (list, tuple)):
            contents = [contents]

        formatted_items = []
        for item in contents:
            role = getattr(item, "role", None)
            role_prefix = f"[{role}] " if role else ""
            content_str = self._format_content(item, max_length=max_length)
            formatted_items.append(f"{role_prefix}{content_str}")
        return "\n   ".join(formatted_items)

    def _format_args(self, args: Any, max_length: int = None) -> str:
        """Format arguments or result objects for logging."""
        if args is None:
            return "None"
        if max_length is None:
            max_length = self.max_length or 999999

        formatted = str(args)
        if not self.debug_mode and len(formatted) > max_length:
            return formatted[:max_length] + "..."
        return formatted

    async def before_agent_callback(
        self,
        *,
        agent: Any = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        agent_obj = agent or kwargs.get("agent")
        agent_name = getattr(agent_obj, "name", str(agent_obj))
        ctx = callback_context or kwargs.get("callback_context")

        msg = f"Executing agent: {agent_name}"
        logger.info(self._format(_CYAN, ">>> [AGENT START]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent_obj)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if ctx:
            user_content = getattr(ctx, "user_content", None)
            if user_content:
                logger.info(f"   User Content Input: {self._format_content(user_content)}")

    async def after_agent_callback(
        self,
        *,
        agent: Any = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        agent_obj = agent or kwargs.get("agent")
        agent_name = getattr(agent_obj, "name", str(agent_obj))
        ctx = callback_context or kwargs.get("callback_context")

        msg = f"Completed agent: {agent_name}"
        logger.info(self._format(_GREEN, "<<< [AGENT DONE ]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent_obj)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if ctx:
            output = getattr(ctx, "output", None)
            if output is not None:
                logger.info(f"   Agent Output: {self._format_args(output)}")

    async def before_model_callback(
        self,
        *,
        callback_context: Any = None,
        llm_request: Any = None,
        **kwargs: Any,
    ) -> None:
        ctx = callback_context or kwargs.get("tool_context")
        logger.info(self._format(_CYAN, "🧠 [MODEL REQUEST]", "Sending request to LLM"))

        ctx_lines = self._format_ctx(ctx)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if llm_request:
            model_name = getattr(llm_request, "model", "default")
            logger.info(f"   Model: {model_name}")

            config = getattr(llm_request, "config", None)
            if config and getattr(config, "system_instruction", None):
                sys_inst = str(config.system_instruction)
                if not self.debug_mode and len(sys_inst) > 200:
                    sys_inst = sys_inst[:200] + "..."
                logger.info(f"   System Instruction: '{sys_inst}'")

            contents = getattr(llm_request, "contents", None)
            if contents:
                formatted_contents = self._format_contents(contents)
                logger.info(f"   Prompt Inputs:\n   {formatted_contents}")

                # In debug mode, check for and highlight function_response parts (tool results)
                if self.debug_mode and isinstance(contents, (list, tuple)):
                    func_responses = []
                    for content_item in contents:
                        if hasattr(content_item, "parts"):
                            for part in content_item.parts:
                                if hasattr(part, "function_response") and part.function_response:
                                    fr = part.function_response
                                    func_responses.append({
                                        "name": getattr(fr, "name", "unknown"),
                                        "response": getattr(fr, "response", None)
                                    })
                    if func_responses:
                        logger.info(f"   Function Responses in Prompt: {len(func_responses)}")
                        for fr in func_responses:
                            logger.info(f"      - Tool: {fr['name']}")
                            try:
                                import json
                                formatted = json.dumps(fr['response'], indent=8, default=str) if fr['response'] else "null"
                                logger.info(f"        Response:\n{formatted}")
                            except:
                                logger.info(f"        Response: {fr['response']}")

            # Log tool availability
            tools_dict = getattr(llm_request, "tools_dict", None)
            tools_list = getattr(llm_request, "tools", None)
            if tools_dict:
                logger.info(f"   Available Tools (dict): {list(tools_dict.keys())}")
            elif tools_list:
                tool_names = [getattr(t, "name", str(t)) for t in tools_list] if isinstance(tools_list, list) else ["<tools object>"]
                logger.info(f"   Available Tools (list): {tool_names}")
            else:
                logger.info(f"   Available Tools: None")

    async def after_model_callback(
        self,
        *,
        callback_context: Any = None,
        llm_response: Any = None,
        **kwargs: Any,
    ) -> None:
        ctx = callback_context or kwargs.get("tool_context")
        logger.info(self._format(_GREEN, "🧠 [MODEL RESPONSE]", "Received response from LLM"))

        ctx_lines = self._format_ctx(ctx)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if llm_response:
            err_code = getattr(llm_response, "error_code", None)
            if err_code:
                err_msg = getattr(llm_response, "error_message", "Unknown error")
                logger.info(f"   ❌ Error Code: {err_code} | Message: {err_msg}")
            else:
                content = getattr(llm_response, "content", None)
                formatted_content = self._format_content(content)
                logger.info(f"   Model Output: {formatted_content}")

                # Check if response contains function calls
                if content and hasattr(content, "parts"):
                    func_calls = [p for p in content.parts if hasattr(p, "function_call") and p.function_call]
                    if func_calls:
                        logger.info(f"   Function Calls Requested: {len(func_calls)}")
                        for fc_part in func_calls:
                            fc = fc_part.function_call
                            fc_name = getattr(fc, 'name', 'unknown')
                            logger.info(f"      - {fc_name}")

                            # In debug mode, show function call arguments
                            if self.debug_mode and hasattr(fc, 'args'):
                                try:
                                    import json
                                    fc_args = dict(fc.args)
                                    formatted_args = json.dumps(fc_args, indent=8, default=str)
                                    logger.info(f"        Args:\n{formatted_args}")
                                except Exception as e:
                                    logger.info(f"        Args: {getattr(fc, 'args', 'none')}")

                if getattr(llm_response, "turn_complete", None) is not None:
                    logger.info(f"   Turn Complete: {llm_response.turn_complete}")

            usage = getattr(llm_response, "usage_metadata", None)
            if usage:
                in_tokens = getattr(usage, "prompt_token_count", None)
                out_tokens = getattr(usage, "candidates_token_count", None)
                if in_tokens is not None or out_tokens is not None:
                    logger.info(f"   Token Usage - Input: {in_tokens}, Output: {out_tokens}")

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any = None,
        llm_request: Any = None,
        error: Exception = None,
        **kwargs: Any,
    ) -> None:
        ctx = callback_context or kwargs.get("tool_context")
        err = error or kwargs.get("error")
        logger.error(self._format(_RED, "!!! [MODEL ERROR]", f"Model call error: {err}"))

        ctx_lines = self._format_ctx(ctx)
        for line in ctx_lines:
            logger.error(f"   {line}")

        if llm_request:
            model_name = getattr(llm_request, "model", "default")
            logger.error(f"   Model: {model_name}")

    async def before_tool_callback(
        self,
        *,
        tool: Any = None,
        tool_args: dict[str, Any] = None,
        tool_context: Any = None,
        agent: Any = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        tool_obj = tool or kwargs.get("tool")
        tool_name = getattr(tool_obj, "name", str(tool_obj))
        args = tool_args if tool_args is not None else kwargs.get("tool_args", {})
        ctx = tool_context or callback_context or kwargs.get("ctx")

        msg = f"Tool: {tool_name}"
        logger.info(self._format(_YELLOW, "  -> [TOOL CALL ]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent)
        for line in ctx_lines:
            logger.info(f"   {line}")

        # Show tool description in debug mode
        if self.debug_mode and hasattr(tool_obj, "description"):
            logger.info(f"   Tool Description: {tool_obj.description}")

        # Format args as pretty JSON in debug mode
        if self.debug_mode and args:
            try:
                import json
                formatted_args = json.dumps(args, indent=2, default=str)
                logger.info(f"   Tool Input (JSON):\n{formatted_args}")
            except Exception:
                logger.info(f"   Tool Input Arguments: {self._format_args(args)}")
        else:
            logger.info(f"   Tool Input Arguments: {self._format_args(args)}")

    async def after_tool_callback(
        self,
        *,
        tool: Any = None,
        tool_args: dict[str, Any] = None,
        tool_result: Any = None,
        result: Any = None,
        tool_context: Any = None,
        agent: Any = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        tool_obj = tool or kwargs.get("tool")
        tool_name = getattr(tool_obj, "name", str(tool_obj))
        args = tool_args if tool_args is not None else kwargs.get("tool_args", {})
        res = tool_result if tool_result is not None else (result if result is not None else kwargs.get("result"))
        ctx = tool_context or callback_context or kwargs.get("ctx")

        msg = f"Tool: {tool_name}"
        logger.info(self._format(_MAGENTA, "  <- [TOOL RESULT]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent)
        for line in ctx_lines:
            logger.info(f"   {line}")

        # Show input args (brief unless debug mode)
        if self.debug_mode:
            logger.info(f"   Tool Input Arguments: {self._format_args(args)}")

        # Format result as pretty JSON in debug mode
        if self.debug_mode and res is not None:
            try:
                import json
                # Try to format as JSON if it's a dict/list or JSON string
                if isinstance(res, (dict, list)):
                    formatted_result = json.dumps(res, indent=2, default=str)
                    logger.info(f"   Tool Result (JSON):\n{formatted_result}")
                elif isinstance(res, str) and (res.strip().startswith('{') or res.strip().startswith('[')):
                    # Try to parse and reformat if it's a JSON string
                    try:
                        parsed = json.loads(res)
                        formatted_result = json.dumps(parsed, indent=2, default=str)
                        logger.info(f"   Tool Result (JSON):\n{formatted_result}")
                    except:
                        logger.info(f"   Tool Result Output: {self._format_args(res)}")
                else:
                    logger.info(f"   Tool Result Output: {self._format_args(res)}")
            except Exception:
                logger.info(f"   Tool Result Output: {self._format_args(res)}")
        else:
            logger.info(f"   Tool Result Output: {self._format_args(res)}")

    async def on_agent_error_callback(
        self,
        *,
        agent: Any = None,
        error: Exception = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        agent_obj = agent or kwargs.get("agent")
        agent_name = getattr(agent_obj, "name", str(agent_obj))
        ctx = callback_context or kwargs.get("callback_context")
        err = error or kwargs.get("error")

        msg = f"Agent {agent_name} raised error: {err}"
        logger.error(self._format(_RED, "!!! [AGENT ERROR]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent_obj)
        for line in ctx_lines:
            logger.error(f"   {line}")

    async def on_tool_error_callback(
        self,
        *,
        tool: Any = None,
        tool_args: dict[str, Any] = None,
        error: Exception = None,
        tool_context: Any = None,
        agent: Any = None,
        callback_context: Any = None,
        **kwargs: Any,
    ) -> None:
        tool_obj = tool or kwargs.get("tool")
        tool_name = getattr(tool_obj, "name", str(tool_obj))
        args = tool_args if tool_args is not None else kwargs.get("tool_args", {})
        ctx = tool_context or callback_context or kwargs.get("ctx")
        err = error or kwargs.get("error")

        msg = f"Tool {tool_name} raised error: {err}"
        logger.error(self._format(_RED, "!!! [TOOL ERROR ]", msg))

        ctx_lines = self._format_ctx(ctx, agent=agent)
        for line in ctx_lines:
            logger.error(f"   {line}")

        logger.error(f"   Tool Input Arguments: {self._format_args(args)}")

    async def on_user_message_callback(
        self,
        *,
        invocation_context: Any = None,
        user_message: Any = None,
        **kwargs: Any,
    ) -> None:
        ctx = invocation_context or kwargs.get("invocation_context")
        logger.info(self._format(_CYAN, "🚀 [USER MESSAGE]", "User message received"))

        ctx_lines = self._format_ctx(ctx)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if user_message:
            logger.info(f"   User Message Input: {self._format_content(user_message)}")

    async def on_event_callback(
        self,
        *,
        invocation_context: Any = None,
        event: Any = None,
        **kwargs: Any,
    ) -> None:
        ctx = invocation_context or kwargs.get("invocation_context")
        event_id = getattr(event, "id", "unknown") if event else "unknown"
        logger.info(self._format(_CYAN, "📢 [EVENT YIELDED]", f"Event ID: {event_id}"))

        ctx_lines = self._format_ctx(ctx)
        for line in ctx_lines:
            logger.info(f"   {line}")

        if event:
            author = getattr(event, "author", None)
            if author:
                logger.info(f"   Author: {author}")
            content = getattr(event, "content", None)
            if content:
                logger.info(f"   Event Content Output: {self._format_content(content)}")

