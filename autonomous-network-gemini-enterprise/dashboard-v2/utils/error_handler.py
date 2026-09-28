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
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

class ErrorSeverity(Enum):
    """Enum for error severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class SupervisorAgentError(Exception):
    """Base exception class for Supervisor Agent errors."""
    
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

class ToolError(SupervisorAgentError):
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

class RemoteAgentError(SupervisorAgentError):
    """Exception raised for errors in remote agent communication."""
    
    def __init__(
        self, 
        message: str, 
        agent_name: str,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "agent_name": agent_name
        })
        super().__init__(message, details=details, **kwargs)

class AuthenticationError(SupervisorAgentError):
    """Exception raised for authentication errors."""
    pass

async def send_error_message(sio_sessions, error: SupervisorAgentError):
    """
    Send an error message to connected dashboard clients via plain 'chat_response' events.

    Args:
        sio_sessions: Dictionary of {sid: sio} socket sessions
        error: The error that occurred
    """
    # Format the error message with severity
    error_message = f"[{error.severity.value}] {error.message}"
    
    # Append human-readable details (skip raw traceback)
    if error.details:
        detail_lines = []
        for key, value in error.details.items():
            if key != "traceback":
                detail_lines.append(f"{key}: {value}")
        if detail_lines:
            error_message += "\n\nDetails:\n" + "\n".join(detail_lines)

    # Emit a plain chat_response event to every connected session
    for sid, sio in sio_sessions.items():
        await sio.emit('chat_response', {
            'text': error_message,
            'done': True,
            'error': True
        }, room=sid)
        logger.info(f"Sent error message to {sid}: {error_message}")

def with_error_handling(error_handler: Callable):
    """
    Decorator for handling exceptions in async functions.
    
    This decorator catches exceptions, logs them, and calls the provided
    error handler function to handle the error (e.g., send a message to the user).
    
    Args:
        error_handler: A callable that takes an exception and handles it
        
    Returns:
        A wrapped function that handles exceptions
    """
    import functools
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except SupervisorAgentError as e:
                # Log the error with appropriate severity
                if e.severity == ErrorSeverity.INFO:
                    logger.info(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.WARNING:
                    logger.warning(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.ERROR:
                    logger.error(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.CRITICAL:
                    logger.critical(f"SupervisorAgentError: {e.message}", exc_info=True)
                
                await error_handler(e)
                raise
            except Exception as e:
                error = SupervisorAgentError(
                    message=f"Unexpected error: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                logger.error(f"Unexpected error: {str(e)}", exc_info=True)
                await error_handler(error)
                raise error
        
        return wrapper
    
    return decorator
