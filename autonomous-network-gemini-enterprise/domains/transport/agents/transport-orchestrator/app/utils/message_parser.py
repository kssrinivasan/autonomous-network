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

"""Message parsing utilities for A2A structured data"""

import json
from typing import Dict, Any
from google.protobuf.json_format import MessageToDict


def extract_request_data_from_part(part) -> Dict[str, Any]:
    """Extract request data from A2A message Part (handles both data and text)

    Supports both structured data (part.data) and legacy text-based JSON (part.text).
    This enables backward compatibility while preferring the structured approach.

    Args:
        part: A2A Part object with either data or text field

    Returns:
        Dict containing the parsed request data

    Raises:
        ValueError: If part has neither data nor text, or if text is not valid JSON
    """
    # Prefer structured data if available (new approach)
    if part.HasField('data'):
        # Convert protobuf struct to dict
        return MessageToDict(part.data, preserving_proto_field_name=True)

    # Fallback to text parsing for backward compatibility
    if part.text:
        text = part.text.strip()

        # Remove markdown code blocks if present
        if text.startswith("```json"):
            text = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("```"):
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in part.text: {e}")

    raise ValueError("Message part has neither data nor text")


def extract_request_data(message) -> Dict[str, Any]:
    """Extract request data from A2A Message

    Args:
        message: A2A Message object with parts

    Returns:
        Dict containing the parsed request data

    Raises:
        ValueError: If message has no parts or data cannot be extracted
    """
    if not message.parts:
        raise ValueError("Message has no parts")

    return extract_request_data_from_part(message.parts[0])


def format_structured_response(data: Dict[str, Any]) -> str:
    """Format response data as JSON for agent output

    Since ADK agents return text, we format the structured response
    as clean JSON that the client can parse from part.data or part.text.

    Args:
        data: Response dictionary to format

    Returns:
        JSON-formatted string
    """
    return json.dumps(data, indent=2)
