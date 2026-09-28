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

"""OIDC authentication for this agent's MCP tool connection.

The MCP tool server (``mcp-networktools``) is deployed with
``--no-allow-unauthenticated`` and additionally verifies the caller's identity
in-process, so every request needs a Google OIDC ID token whose audience is
the tool server's own URL.

Why a header provider and not an httpx client factory: ADK's
``StreamableHTTPConnectionParams`` accepts ``httpx_client_factory``, but
``SseConnectionParams`` -- which these agents use, because the tool server runs
the SSE transport -- does not. ``MCPToolset(header_provider=...)`` is the hook
that works for both, and ADK calls it per invocation, so the token stays fresh.

Tokens are cached per audience until shortly before they expire. That matters
for more than cost: ADK pools MCP sessions keyed by a hash of the request
headers, so minting a new token per call would create a new pooled session
every time.

Set ``MCP_AUTH_ENABLED=false`` to disable, e.g. when pointing at a local
unauthenticated tool server.
"""

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request

from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
import google.oauth2.id_token

logger = logging.getLogger(__name__)

MCP_AUTH_ENABLED = os.getenv("MCP_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")

# Refresh this many seconds before the token actually expires, so a request
# never leaves with a token that dies in flight.
_EXPIRY_MARGIN_SEC = 300

# audience -> (token, expires_at_epoch)
_token_cache = {}
_auth_request = None
_credentials = None


def _get_credentials():
    """Load ADC once, tolerating environments that have none."""
    global _credentials, _auth_request
    if _auth_request is None:
        _auth_request = GoogleAuthRequest()
    if _credentials is None:
        try:
            _credentials, _ = default()
        except Exception as e:
            logger.debug("No default credentials available: %s", e)
            _credentials = False
    return _credentials or None


def audience_for(url: str) -> str:
    """The Cloud Run audience for a tool URL: scheme://host[:port], no path."""
    parts = urllib.parse.urlsplit(url)
    audience = f"{parts.scheme}://{parts.hostname}"
    if parts.port and parts.port not in (80, 443):
        audience += f":{parts.port}"
    return audience


def _expiry_of(token: str) -> float:
    """Read 'exp' out of the JWT payload without verifying it.

    Verification is the server's job; this is only used to decide when to
    refresh. A token we cannot parse is treated as short-lived.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + 600


def fetch_cloud_run_id_token(audience: str) -> str:
    """Fetch an OIDC ID token for ``audience``, trying each available source."""
    auth_request = GoogleAuthRequest() if _auth_request is None else _auth_request

    # 1. Metadata server -- present and fastest inside Agent Runtime and GCE.
    try:
        url = ("http://metadata.google.internal/computeMetadata/v1/instance/"
               f"service-accounts/default/identity?audience={audience}")
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            token = resp.read().decode("utf-8").strip()
            if token:
                return token
    except Exception as e:
        logger.debug("Metadata server ID token fetch failed for %s: %s", audience, e)

    # 2. Whatever ADC can mint directly (works for user creds and SA key files).
    try:
        token = google.oauth2.id_token.fetch_id_token(auth_request, audience)
        if token:
            return token
    except Exception as e:
        logger.debug("fetch_id_token failed for %s: %s", audience, e)

    # 3. IAM Credentials generateIdToken, for impersonated service accounts.
    credentials = _get_credentials()
    if credentials:
        try:
            if not credentials.valid:
                credentials.refresh(auth_request)
            sa_email = getattr(credentials, "service_account_email", None)
            if not sa_email or sa_email == "default":
                sa_email = os.environ.get("AGENT_SERVICE_ACCOUNT") or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
            if sa_email and credentials.token:
                iam_url = ("https://iamcredentials.googleapis.com/v1/projects/-/"
                           f"serviceAccounts/{sa_email}:generateIdToken")
                payload = json.dumps({"audience": audience, "includeEmail": True}).encode("utf-8")
                iam_req = urllib.request.Request(iam_url, data=payload, headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json",
                })
                with urllib.request.urlopen(iam_req, timeout=5) as resp:
                    token = json.loads(resp.read().decode("utf-8")).get("token")
                    if token:
                        return token
        except Exception as e:
            logger.debug("generateIdToken failed for %s: %s", audience, e)

    raise RuntimeError(f"No OIDC ID token could be obtained for audience: {audience}")


def id_token_for(audience: str) -> str:
    """Return a cached, non-expiring-soon ID token for ``audience``."""
    cached = _token_cache.get(audience)
    if cached and cached[1] - _EXPIRY_MARGIN_SEC > time.time():
        return cached[0]
    token = fetch_cloud_run_id_token(audience)
    _token_cache[audience] = (token, _expiry_of(token))
    logger.info("Obtained OIDC ID token for MCP audience %s", audience)
    return token


def mcp_auth_headers(url: str) -> dict:
    """Authorization header for calls to the MCP server at ``url``.

    Returns an empty dict rather than raising when auth is disabled or no
    token can be obtained: a 401 from the server is a clearer failure than an
    exception thrown while the agent is assembling its toolset.
    """
    if not MCP_AUTH_ENABLED:
        return {}
    try:
        return {"Authorization": f"Bearer {id_token_for(audience_for(url))}"}
    except Exception as e:
        logger.warning("Proceeding without an MCP auth header for %s: %s", url, e)
        return {}


def mcp_header_provider(url: str):
    """Build the ``header_provider`` callable for ``MCPToolset``."""
    def provider(_readonly_context=None):
        return mcp_auth_headers(url)
    return provider
