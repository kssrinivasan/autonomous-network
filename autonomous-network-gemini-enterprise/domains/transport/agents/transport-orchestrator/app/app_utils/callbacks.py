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

"""after_agent_callbacks that write intent phase updates to Spanner.

Each callback is attached to one subagent in the SequentialAgent pipeline.
They read `validation_result` from session state (set by ValidateIntentAgent)
to obtain the intent_id, then write the current phase to agentops-db so the
intent server can serve live status without querying the orchestrator.

Callbacks are no-ops when intent_id is absent (e.g. GetIntentStatus requests).
"""

import json
import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from app.utils.intent_ops_client import get_intent_ops_client

logger = logging.getLogger(__name__)

# Tags used by ADK's A2A part converter to wrap generic data parts as inline blobs
_A2A_DATA_START = b'<a2a_datapart_json>'
_A2A_DATA_END = b'</a2a_datapart_json>'


def _intent_id_from_user_content(callback_context: CallbackContext) -> Optional[str]:
    """Extract intent_id from the original A2A request stored in invocation context.

    The orchestrator client sends the request as a generic data part (part.data /
    proto struct). ADK's A2A converter wraps it as inline_data bytes with special
    start/end tags. This function decodes that envelope to retrieve intent_id.
    """
    try:
        user_content = callback_context._invocation_context.user_content
        if not user_content:
            return None
        for part in (user_content.parts or []):
            inline = getattr(part, 'inline_data', None)
            if inline is None:
                continue
            data = getattr(inline, 'data', None)
            if (
                isinstance(data, bytes)
                and data.startswith(_A2A_DATA_START)
                and data.endswith(_A2A_DATA_END)
            ):
                raw = data[len(_A2A_DATA_START):-len(_A2A_DATA_END)]
                try:
                    request_dict = json.loads(raw)
                    intent_id = request_dict.get('intent_id')
                    if intent_id:
                        return str(intent_id)
                except (json.JSONDecodeError, Exception):
                    pass
    except Exception:
        pass
    return None


def _get_validation_result(callback_context: CallbackContext) -> Optional[dict]:
    """Return validation_result from session state as a plain dict, or None."""
    vr = callback_context.state.get("validation_result")
    if vr is None:
        return None
    if isinstance(vr, dict):
        return vr
    if hasattr(vr, "model_dump"):
        return vr.model_dump()
    return None


def _get_intent_id(callback_context: CallbackContext) -> Optional[str]:
    """Return intent_id — from state cache (set by after_validate_callback), or from user message."""
    return (
        callback_context.state.get("intent_id")
        or _intent_id_from_user_content(callback_context)
    )


def after_validate_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """Runs after ValidateIntentAgent completes.

    - On success: updates Spanner to Planning and sets crName to service_name.
    - On failure: updates Spanner to Rejected.
    """
    # Extract intent_id from the original A2A request (reliable) and cache it
    intent_id = _intent_id_from_user_content(callback_context)
    logger.info(f"***** Intent {intent_id} - Update state after validate******")
    if not intent_id:
        logger.debug("after_validate: no intent_id in user content, skipping Spanner update")
        return None

    callback_context.state["intent_id"] = intent_id

    vr = _get_validation_result(callback_context)

    try:
        client = get_intent_ops_client()
        if vr and vr.get("success"):
            cr_name = vr.get("service_name") or ""
            client.update_phase(intent_id, "Planning", cr_name=cr_name)
            logger.info("after_validate: intent moved to Planning", extra={
                "intent_id": intent_id,
                "cr_name": cr_name,
            })
        else:
            client.update_phase(intent_id, "Rejected")
            logger.info("after_validate: intent Rejected (validation failed)", extra={
                "intent_id": intent_id,
                "errors": vr.get("errors") if vr else None,
            })
    except Exception as e:
        logger.error("after_validate_callback: Spanner update failed", extra={
            "intent_id": intent_id,
            "error": str(e),
        })

    return None


def after_plan_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """Runs after PlannerAgent completes. Advances to Designing."""
    vr = _get_validation_result(callback_context)
    if not vr or not vr.get("success"):
        return None

    intent_id = _get_intent_id(callback_context)
    logger.info(f"***** Intent {intent_id} - Update state after planner******")
    if not intent_id:
        return None

    try:
        get_intent_ops_client().update_phase(intent_id, "Designing")
        logger.info("after_plan: intent moved to Designing", extra={"intent_id": intent_id})
    except Exception as e:
        logger.error("after_plan_callback: Spanner update failed", extra={
            "intent_id": intent_id,
            "error": str(e),
        })

    return None


def after_design_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """Runs after DesignerAgent completes. Advances to Deploying."""
    vr = _get_validation_result(callback_context)
    if not vr or not vr.get("success"):
        return None

    intent_id = _get_intent_id(callback_context)
    logger.info(f"***** Intent {intent_id} - Update state after validate******")
    if not intent_id:
        return None

    try:
        get_intent_ops_client().update_phase(intent_id, "Deploying")
        logger.info("after_design: intent moved to Deploying", extra={"intent_id": intent_id})
    except Exception as e:
        logger.error("after_design_callback: Spanner update failed", extra={
            "intent_id": intent_id,
            "error": str(e),
        })

    return None


def after_deploy_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """Runs after DeployCRsAgent completes. Advances to Completed or Rejected."""
    intent_id = _get_intent_id(callback_context)
    logger.info(f"***** Intent {intent_id} - Update state after deploy ******")
    if not intent_id:
        return None

    dr = callback_context.state.get("deployment_result") or {}
    if hasattr(dr, "model_dump"):
        dr = dr.model_dump()

    try:
        if dr.get("success"):
            get_intent_ops_client().update_phase(intent_id, "Completed")
            logger.info("after_deploy: intent Completed", extra={"intent_id": intent_id})
        else:
            get_intent_ops_client().update_phase(intent_id, "Rejected")
            logger.info("after_deploy: intent Rejected (deployment failed)", extra={
                "intent_id": intent_id,
                "message": dr.get("message"),
            })
    except Exception as e:
        logger.error("after_deploy_callback: Spanner update failed", extra={
            "intent_id": intent_id,
            "error": str(e),
        })

    return None
