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

"""Configuration for Transport Orchestrator Agent"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

# Load .env FIRST before reading any environment variables
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)  # Don't override existing env vars

# Configure logging level so info logs appear in Cloud Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
MCP_AUTH_ENABLED = os.getenv("MCP_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
_raw_mcp_url = os.environ.get("SHARED_MCP_URL", "")
if _raw_mcp_url and not _raw_mcp_url.rstrip("/").endswith("/mcp"):
    SHARED_MCP_URL = f"{_raw_mcp_url.rstrip('/')}/mcp"
else:
    SHARED_MCP_URL = _raw_mcp_url

# GCP Project Configuration
GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
GOOGLE_REGION = os.getenv("GOOGLE_REGION", "europe-west1")

# Spanner Configuration — transport topology databases
SPANNER_INSTANCE = os.getenv("GOOGLE_SPANNER_INSTANCE", "network-topology")
SPANNER_TRANSPORT_DB = os.getenv("GOOGLE_SPANNER_TRANSPORT_DATABASE", "transporttopology-db")
SPANNER_INFRA_DB = os.getenv("GOOGLE_SPANNER_INFRA_DATABASE", "infratopology-db")

# Spanner Configuration — intent ops database (agentops-db, shared with intent server)
SPANNER_OPS_INSTANCE = os.getenv("GOOGLE_SPANNER_OPS_INSTANCE", "networktopology-instance")
SPANNER_OPS_DB = os.getenv("GOOGLE_SPANNER_OPS_DATABASE", "agentops-db")

# Network Configuration
PROVIDER_ASN = int(os.getenv("PROVIDER_ASN", "65001"))
DEFAULT_UNDERLAY_REF = os.getenv("DEFAULT_UNDERLAY_REF", "mpls-underlay")

# VRF Configuration
VRF_TABLE_ID = int(os.getenv("VRF_TABLE_ID", "300"))

# Resource Pool Ranges
VRF_SEQ_MIN = 200
VRF_SEQ_MAX = 65535
RT_SEQ_MIN = 2000
RT_SEQ_MAX = 65535
CUSTOMER_AS_MIN = 65100
CUSTOMER_AS_MAX = 65199
