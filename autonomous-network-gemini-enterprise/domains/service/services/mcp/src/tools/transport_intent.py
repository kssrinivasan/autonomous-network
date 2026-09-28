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

"""Transport Domain Intent API MCP Tools"""

import logging
from utils.globals import domain_mcp
from utils.http_client import http_client, TRANSPORT_INTENT_API_URL

logger = logging.getLogger(__name__)


@domain_mcp.tool()
async def create_transport_intent(intent_data: dict) -> dict:
    """Create a transport L3 VPN intent

    Args:
        intent_data: Intent specification (TMF921 format)

    Returns:
        Created intent with ID and status
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.post(
        f"{TRANSPORT_INTENT_API_URL}/intent",
        json=intent_data
    )


@domain_mcp.tool()
async def list_transport_intents() -> dict:
    """List all transport intents

    Returns:
        List of transport intents with status
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.get(f"{TRANSPORT_INTENT_API_URL}/intents")


@domain_mcp.tool()
async def get_transport_intent(intent_id: str) -> dict:
    """Get transport intent by ID

    Args:
        intent_id: Intent identifier

    Returns:
        Intent details with current status
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.get(f"{TRANSPORT_INTENT_API_URL}/intent/{intent_id}")


@domain_mcp.tool()
async def delete_transport_intent(intent_id: str) -> dict:
    """Delete transport intent

    Args:
        intent_id: Intent identifier to delete

    Returns:
        Deletion confirmation
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.delete(f"{TRANSPORT_INTENT_API_URL}/intent/{intent_id}")


@domain_mcp.tool()
async def get_transport_intent_api_schema() -> dict:
    """Get OpenAPI schema for Transport Intent API

    Returns:
        OpenAPI 3.0 schema document
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.get(f"{TRANSPORT_INTENT_API_URL}/openapi.json")


@domain_mcp.tool()
async def check_transport_intent_api_health() -> dict:
    """Health check for Transport Intent API

    Returns:
        Health status
    """
    if not TRANSPORT_INTENT_API_URL:
        raise ValueError("Transport Intent API not configured (TRANSPORT_INTENT_API_URL)")

    return await http_client.get(f"{TRANSPORT_INTENT_API_URL}/health")
