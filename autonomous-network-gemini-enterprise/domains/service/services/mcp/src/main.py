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

"""Multi-Domain MCP Server - Main Entrypoint"""

import logging
import os
import asyncio
import uvicorn
from utils.globals import domain_mcp

# Import tool modules to register MCP decorators
import tools.transport_intent
import tools.transport_topology
import tools.core_intent
import tools.core_topology
import tools.radio_intent
import tools.radio_topology

# Configure logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# Create SSE transport app
sse_app = domain_mcp.http_app(transport="sse", stateless_http=True)

async def main():
    """Start the Multi-Domain MCP server"""
    logger.info("=" * 60)
    logger.info("Starting Multi-Domain MCP Server")
    logger.info("=" * 60)
    logger.info("")

    logger.info("Server starting on 0.0.0.0:8080...")

    config = uvicorn.Config(
        app=sse_app,
        host="0.0.0.0",
        port=8080,
        log_level="info",
        workers=1
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
