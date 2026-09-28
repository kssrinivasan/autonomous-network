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

import logging
import os

from fastmcp import FastMCP

from utils.auth import GoogleOIDCTokenVerifier, SharedSecretTokenVerifier

logger = logging.getLogger(__name__)

GOOGLE_PROJECT = os.environ.get("GOOGLE_PROJECT", "")
GOOGLE_PROJECT_NUMBER = os.environ.get("GOOGLE_PROJECT_NUMBER", "")

# Set to this service's own Cloud Run URL to require OIDC on every call; see
# utils/auth.py. deploy.sh sets it for the Reasoning Engine server (re/). The
# Gemini Enterprise server (ge/) cannot use it -- GE connects as a Tool Data
# Store without an ID token -- and uses MCP_SHARED_SECRET instead.
MCP_AUDIENCE = os.environ.get("MCP_AUDIENCE", "")

# Pre-shared bearer secret, mounted from Secret Manager by ge/deploy.sh and
# configured on the GE tool data store. Only consulted when MCP_AUDIENCE is
# unset, so the two are never both active.
MCP_SHARED_SECRET = os.environ.get("MCP_SHARED_SECRET", "")

_auth = None
if MCP_AUDIENCE:
    _auth = GoogleOIDCTokenVerifier(
        audience=MCP_AUDIENCE,
        allowed_emails=[
            # the shared agent/service identity
            f"networkagent@{GOOGLE_PROJECT}.iam.gserviceaccount.com",
            # GKE workloads and anything running on the default compute SA
            f"{GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
            # the Vertex AI Reasoning Engine service agent -- the identity the
            # deployed agents actually present when they call these tools
            f"service-{GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
        ],
    )
elif MCP_SHARED_SECRET:
    _auth = SharedSecretTokenVerifier(secret=MCP_SHARED_SECRET)
else:
    logger.warning(
        "No MCP_AUDIENCE and no MCP_SHARED_SECRET: this server accepts "
        "unauthenticated calls from anyone who can reach it."
    )

# Whether this deployment registers the tools that change state -- deploying
# and deleting GitOps descriptors, creating and deleting devices, starting and
# stopping traffic tests.
#
# Off by default, and deliberately so. The two servers share one tool surface,
# so every tool added for the authenticated Reasoning Engine twin also lands on
# the Gemini Enterprise one, whose only credential is a shared secret. Reads
# behind a shared secret is a reasonable trade; unauthenticated writes to the
# GitOps repo the controller reconciles from is not. re/deploy.sh opts in.
ENABLE_MUTATING_TOOLS = os.environ.get(
    "MCP_ENABLE_MUTATING_TOOLS", ""
).strip().lower() in ("1", "true", "yes", "on")

# Create central FastMCP server instance
networkagent_mcp = FastMCP(
    name="Network Agent MCP",
    instructions="Provides production telecommunications network lifecycle tools",
    auth=_auth,
)


def callable_tool(**kwargs):
    """Register an MCP tool that other tools can still call directly.

    ``@networkagent_mcp.tool()`` rebinds the name to a ``FunctionTool``, which
    is a pydantic model and not callable, so a tool invoked in-process by
    another tool must be registered with this decorator instead. It registers
    the function and then leaves the plain function bound to its own name.

    Takes the same keyword arguments as ``networkagent_mcp.tool``.
    """
    def decorator(fn):
        networkagent_mcp.tool(**kwargs)(fn)
        return fn
    return decorator


def mutating_tool(**kwargs):
    """Register a state-changing MCP tool, unless this deployment forbids them.

    See ``ENABLE_MUTATING_TOOLS``. When off, the function stays importable and
    directly callable but is never exposed over MCP, so a caller who reaches
    the endpoint cannot invoke it however they are authenticated.
    """
    def decorator(fn):
        if ENABLE_MUTATING_TOOLS:
            networkagent_mcp.tool(**kwargs)(fn)
        else:
            logger.info("Not registering mutating tool %s", fn.__name__)
        return fn
    return decorator
