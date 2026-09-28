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

import logging
import traceback
from enum import Enum
from typing import Optional, Dict, Any
from uuid import uuid4
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    Role,
    Message,
    Part,
)

def new_agent_text_message(text: str, context_id: str = "", task_id: str = "") -> Message:
    """Create an agent text message."""
    msg = Message()
    msg.role = Role.ROLE_AGENT
    msg.message_id = uuid4().hex

    if context_id:
        msg.context_id = context_id
    if task_id:
        msg.task_id = task_id

    # Add text part
    part = Part()
    part.text = text
    msg.parts.append(part)

    return msg


logger = logging.getLogger(__name__)

class ErrorSeverity(Enum):
    """Enum for error severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class ChaosAgentError(Exception):
    """Base exception class for Designer Agent errors."""
    
    def __init__(
        self, 
        message: str, 
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        details: Optional[Dict[str, Any]] = None,
        original_exception: Optional[Exception] = None
    ):
        self.message = message
        self.severity = severity
        self.details = details or {}
        self.original_exception = original_exception
        
        # Add traceback information if there's an original exception
        if original_exception:
            self.details["traceback"] = traceback.format_exception(
                type(original_exception), 
                original_exception, 
                original_exception.__traceback__
            )
        
        super().__init__(self.message)

class ToolError(ChaosAgentError):
    """Exception raised for errors in tool execution."""
    
    def __init__(
        self, 
        message: str, 
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "tool_name": tool_name,
            "tool_args": tool_args or {}
        })
        super().__init__(message, details=details, **kwargs)

class AuthenticationError(ChaosAgentError):
    """Exception raised for authentication errors."""
    pass

def create_error_status_event(
    error: ChaosAgentError,
    context_id: str,
    task_id: str
) -> TaskStatusUpdateEvent:
    """
    Create a TaskStatusUpdateEvent from an error.

    Args:
        error: The error that occurred
        context_id: The context ID
        task_id: The task ID

    Returns:
        A TaskStatusUpdateEvent containing the error information
    """
    # Format the error message with severity
    error_message = f"[{error.severity.value}] {error.message}"
    
    # Add details if available
    if error.details:
        error_details = "\n\nDetails:\n"
        for key, value in error.details.items():
            if key == "traceback":
                # Format traceback in a more readable way
                error_details += f"\nTraceback:\n{''.join(value)}"
            else:
                error_details += f"\n{key}: {value}"
        error_message += error_details
    
    # Determine the appropriate task state based on severity
    if error.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL]:
        state = TaskState.TASK_STATE_FAILED
    else:
        state = TaskState.TASK_STATE_WORKING
    
    return TaskStatusUpdateEvent(
        status=TaskStatus(
            state=state,
            message=new_agent_text_message(
                error_message,
                context_id,
                task_id,
            ),
        ),
        context_id=context_id,
        task_id=task_id,
    )
