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

from fastmcp import FastMCP

# Create an MCP server for all Network Domains
domain_mcp = FastMCP(
    name="Multi-Domain MCP Server",
    instructions="""
Provides MCP tools for Network Domains (Transport, Core, Radio).

This MCP server exposes tools that proxy the Intent API (TMF921) and
Topology API (TMF639) REST endpoints from all domains, enabling AI agents to:

- Create, list, get, and delete intents across domains
- Query resource inventory from all domain topologies
- Execute graph queries against domain topologies
- Retrieve API schemas for validation

All tools are thin proxies over the underlying REST APIs, providing a
standardized MCP interface for multi-domain network operations.
"""
)
