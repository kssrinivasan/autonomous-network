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

import os
from fastmcp import FastMCP
from utils.auth import GoogleOIDCTokenVerifier

GOOGLE_PROJECT = os.environ.get("GOOGLE_PROJECT", "")
GOOGLE_PROJECT_NUMBER = os.environ.get("GOOGLE_PROJECT_NUMBER", "")
MCP_AUDIENCE = os.environ.get("MCP_AUDIENCE", "")

_auth = None
if MCP_AUDIENCE:
    _auth = GoogleOIDCTokenVerifier(
        audience=MCP_AUDIENCE,
        allowed_emails=[
            f"networkagent@{GOOGLE_PROJECT}.iam.gserviceaccount.com",
            f"{GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
            f"service-{GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
        ],
    )

# Create an MCP server
networkagent_mcp = FastMCP(
    name="Network Agent MCP",
    instructions="Provides network agent tools",
    auth=_auth,
)
