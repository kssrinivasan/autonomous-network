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

"""Radio Domain Topology API MCP Tools"""

import logging
from typing import Optional
from utils.globals import domain_mcp
from utils.http_client import http_client, RADIO_TOPOLOGY_API_URL

logger = logging.getLogger(__name__)


@domain_mcp.tool()
async def list_radio_resources(filters: Optional[dict] = None) -> dict:
    """List radio network resources (cell sites, gNodeBs, UEs)

    Args:
        filters: Optional filter parameters (e.g., resourceType, name)

    Returns:
        List of radio resources
    """
    if not RADIO_TOPOLOGY_API_URL:
        raise ValueError("Radio Topology API not configured (RADIO_TOPOLOGY_API_URL)")

    url = f"{RADIO_TOPOLOGY_API_URL}/tmf-api/resourceInventoryManagement/v4/radio/resource"
    return await http_client.get(url, params=filters)


@domain_mcp.tool()
async def get_radio_resource(resource_id: str) -> dict:
    """Get radio resource by ID

    Args:
        resource_id: Resource identifier

    Returns:
        Resource details
    """
    if not RADIO_TOPOLOGY_API_URL:
        raise ValueError("Radio Topology API not configured (RADIO_TOPOLOGY_API_URL)")

    url = f"{RADIO_TOPOLOGY_API_URL}/tmf-api/resourceInventoryManagement/v4/radio/resource/{resource_id}"
    return await http_client.get(url)


@domain_mcp.tool()
async def query_radio_topology_graph(gql_query: str) -> dict:
    """Execute graph query against radio topology

    Args:
        gql_query: GQL (Graph Query Language) query string

    Returns:
        Query results
    """
    if not RADIO_TOPOLOGY_API_URL:
        raise ValueError("Radio Topology API not configured (RADIO_TOPOLOGY_API_URL)")

    url = f"{RADIO_TOPOLOGY_API_URL}/tmf-api/resourceInventoryManagement/v4/radio/graph/query"
    return await http_client.post(url, json={"query": gql_query})


@domain_mcp.tool()
async def get_radio_topology_api_schema() -> dict:
    """Get OpenAPI schema for Radio Topology API

    Returns:
        OpenAPI 3.0 schema document
    """
    if not RADIO_TOPOLOGY_API_URL:
        raise ValueError("Radio Topology API not configured (RADIO_TOPOLOGY_API_URL)")

    return await http_client.get(f"{RADIO_TOPOLOGY_API_URL}/openapi.json")


@domain_mcp.tool()
async def check_radio_topology_api_health() -> dict:
    """Health check for Radio Topology API

    Returns:
        Health status
    """
    if not RADIO_TOPOLOGY_API_URL:
        raise ValueError("Radio Topology API not configured (RADIO_TOPOLOGY_API_URL)")

    return await http_client.get(f"{RADIO_TOPOLOGY_API_URL}/health")
