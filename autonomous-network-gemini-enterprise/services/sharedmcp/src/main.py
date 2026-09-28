# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may in a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import asyncio
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file (if it exists)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    logging.info(f"Loaded environment variables from {env_path}")
else:
    logging.info(f"No .env file found at {env_path}, using system environment variables")

from utils.globals import networkagent_mcp

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# import all tools
import tools.devices
import tools.core
import tools.transport
import tools.radio
import tools.gitea

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    networkagent_mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
