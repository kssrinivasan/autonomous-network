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

"""The one project-specific addition to the stock Agent Engine ``AdkApp``.

Under agents-cli 0.3.0 this module *was* the deployment entrypoint: an
``AdkApp`` subclass that Vertex AI packaged and served directly. Under 1.3.1 the
entrypoint is ``app/fast_api_app.py``, and the reasoning_engine contract is
served by ``app/app_utils/reasoning_engine_adapter.py`` over HTTP. Everything
this file used to do to stand up A2A (``set_up``, ``build_agent_card``, the
``a2a_extension`` operations and their ``on_message_send`` / ``on_get_task`` /
``on_cancel_task`` / ``handle_authenticated_agent_card`` handlers, plus the
``_sanitize_session`` helper that guarded them) is now done by
``app/app_utils/a2a.py``, so it is gone.

What survives is the Gemini Enterprise session-id workaround, which the
generated scaffold does not cover.

Unlike the peer agents, supervisor does *not* register a ``query`` operation.
``query`` exists on the peers so this agent can delegate to them over the
Reasoning Engine ``:query`` API -- see ``AGENT_RUNTIME_MAP`` and
``call_peer_agent`` in ``app/agent.py``. Supervisor is the caller, never the
callee, so it has nothing to expose.

The adapter instantiates this class instead of ``AdkApp``; that import is the
only edit to a generated file.
"""

import logging
from typing import Any, AsyncIterator

from vertexai.agent_engines.templates.adk import AdkApp

logger = logging.getLogger(__name__)


class AgentEngineApp(AdkApp):
    """Stock ``AdkApp`` plus unary query and the Gemini Enterprise session-id workaround."""

    def register_operations(self) -> dict[str, list[str]]:
        operations = super().register_operations()
        # "" is the synchronous bucket; the adapter awaits it if it is a
        # coroutine, and Agent Runtime exposes it as the :query classMethod.
        operations.setdefault("", []).append("query")
        return operations

    async def query(self, request: Any, user_id: str = "supervisor") -> str:
        """Unary entrypoint for direct execution over the Reasoning Engine :query API."""
        from google.genai import types as genai_types

        try:
            if isinstance(request, dict):
                request = (
                    request.get("request")
                    or request.get("input")
                    or request.get("message")
                    or ""
                )
            message = str(request)

            runner = self._tmpl_attrs["runner"]
            session = await runner.session_service.create_session(
                app_name=runner.app_name, user_id=user_id
            )
            content = genai_types.Content(
                role="user", parts=[genai_types.Part(text=message)]
            )

            parts: list[str] = []
            async for event in runner.run_async(
                user_id=user_id, session_id=session.id, new_message=content
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    for part in event.content.parts:
                        if getattr(part, "text", None):
                            parts.append(part.text)
            return "".join(parts).strip() or "(agent returned no text)"
        except Exception as e:
            logger.exception("query() failed")
            return f"Error executing query: {e}"

    async def streaming_agent_run_with_events(
        self, request_json: str
    ) -> AsyncIterator[str]:
        """Normalize Gemini Enterprise's session id before the standard stream.

        AgentSpace / Gemini Enterprise invokes this method with a fully-qualified
        session id (".../sessions/<id>"). VertexAiSessionService validates session
        ids against ^[A-Za-z0-9_-]+$ and raises ValueError on the slashes, which is
        not caught upstream and crashes the stream. Strip it to the bare id so
        validation passes.
        """
        import json

        try:
            payload = json.loads(request_json)
            for key in ("session_id", "sessionId"):
                sid = payload.get(key)
                if isinstance(sid, str) and "/" in sid:
                    payload[key] = sid.rstrip("/").split("/")[-1]
            request_json = json.dumps(payload)
        except Exception as e:
            logger.warning(f"streaming session-id normalization skipped: {e}")
        async for chunk in super().streaming_agent_run_with_events(request_json):
            yield chunk
