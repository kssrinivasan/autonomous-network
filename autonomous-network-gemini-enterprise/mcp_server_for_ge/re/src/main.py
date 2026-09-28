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

import asyncio
import logging
import os
from utils.globals import networkagent_mcp

log_format = "%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# Instantiating all decoupled modular FastMCP tools
import tools.topology
import tools.faults
import tools.capacity
import tools.simulations
import tools.devices
import tools.logs
import tools.tests
import tools.configdrift
import tools.crds
import tools.descriptors

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Starting refactored FastMCP Reasoning Engine server (RE) on port {port} with pure SSE...")
    asyncio.run(
        networkagent_mcp.run_async(
            transport="sse",
            host="0.0.0.0",
            port=port,
        )
    )
