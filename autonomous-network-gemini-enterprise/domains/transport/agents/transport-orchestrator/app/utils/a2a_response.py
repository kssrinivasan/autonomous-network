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

"""A2A response formatting utilities"""

from typing import Optional


def format_create_response(
    success: bool,
    cr_name: Optional[str] = None,
    error_code: Optional[int] = None,
    message: str = ""
) -> dict:
    """Format response for create_intent A2A call

    Args:
        success: Whether operation succeeded
        cr_name: Created CR name
        error_code: HTTP-style error code (400, 409, 424, 500)
        message: Human-readable message

    Returns:
        Response dict matching orchestrator_client expected format
    """
    return {
        "success": success,
        "cr_name": cr_name,
        "status": "Acknowledged" if success else "Rejected",
        "error_code": error_code,
        "message": message
    }


def format_status_response(phase: str, message: str) -> dict:
    """Format response for get_intent_status A2A call

    Args:
        phase: CR status phase (Waiting, Processing, Ready, Error, Deleted)
        message: Status message

    Returns:
        Response dict matching orchestrator_client expected format
    """
    return {
        "phase": phase,
        "message": message
    }


def format_delete_response(success: bool, message: str) -> dict:
    """Format response for delete_intent A2A call

    Args:
        success: Whether deletion succeeded
        message: Human-readable message

    Returns:
        Response dict matching orchestrator_client expected format
    """
    return {
        "success": success,
        "message": message
    }
