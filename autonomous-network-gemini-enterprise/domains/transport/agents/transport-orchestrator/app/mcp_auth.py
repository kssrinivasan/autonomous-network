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

"""MCP Authentication Helper

Provides Google Cloud OIDC ID token authentication for Cloud Run MCP server connections.
"""

import json
import logging
import os
import urllib.request
import httpx
from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
import google.oauth2.id_token

logger = logging.getLogger(__name__)


def fetch_cloud_run_id_token(auth_request: GoogleAuthRequest, audience: str, credentials=None) -> str:
    """Fetch an OIDC ID token for a Cloud Run audience using multiple fallback strategies."""

    # Strategy 1: Compute Metadata Server (Fastest & native in GCP containers / Reasoning Engine)
    try:
        url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}"
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            token = resp.read().decode("utf-8").strip()
            if token:
                logger.info(f"🔒 [MCP AUTH] Successfully fetched OIDC ID token via Metadata Server for audience: {audience}")
                return token
    except Exception as e:
        logger.debug(f"Metadata server ID token fetch failed for {audience}: {e}")

    # Strategy 2: google.oauth2.id_token.fetch_id_token
    try:
        token = google.oauth2.id_token.fetch_id_token(auth_request, audience)
        if token:
            logger.info(f"🔒 [MCP AUTH] Successfully fetched OIDC ID token via google.oauth2.id_token for audience: {audience}")
            return token
    except Exception as e:
        logger.debug(f"google.oauth2.id_token.fetch_id_token failed for {audience}: {e}")

    # Strategy 3: IAM Credentials API (generateIdToken)
    if credentials:
        try:
            if not credentials.valid:
                credentials.refresh(auth_request)

            sa_email = getattr(credentials, 'service_account_email', None)
            if not sa_email or sa_email == 'default':
                sa_email = os.environ.get('AGENT_SERVICE_ACCOUNT') or os.environ.get('GOOGLE_SERVICE_ACCOUNT')

            if sa_email and credentials.token:
                iam_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
                payload = json.dumps({"audience": audience, "includeEmail": True}).encode("utf-8")
                iam_req = urllib.request.Request(iam_url, data=payload, headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json"
                })
                with urllib.request.urlopen(iam_req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    token = data.get("token")
                    if token:
                        logger.info(f"🔒 [MCP AUTH] Successfully fetched OIDC ID token via IAM Credentials API for {sa_email}")
                        return token
        except Exception as e:
            logger.debug(f"IAM Credentials API generateIdToken failed for {audience}: {e}")

    raise RuntimeError(f"All OIDC ID token acquisition strategies failed for audience: {audience}")


class GoogleCloudAuth(httpx.Auth):
    """Custom httpx auth handler for Cloud Run MCP server requests.

    Generates target-specific OIDC ID tokens required for Cloud Run IAM ingress,
    falling back to OAuth2 access tokens if ID token generation is unavailable.
    """

    requires_request_body = True

    def __init__(self):
        self._auth_request = GoogleAuthRequest()
        try:
            self.credentials, self.project = default()
            logger.debug(f"Initialized GoogleCloudAuth with project: {self.project}")
        except Exception as e:
            logger.debug(f"Could not load default credentials: {e}")
            self.credentials = None

    def auth_flow(self, request: httpx.Request):
        """Add Google Cloud authentication token to request headers.

        Extracts audience from request.url and fetches a target-bound OIDC ID token.
        """
        # Ensure scheme and host are strings (httpx request.url.netloc returns bytes b'...')
        scheme = request.url.scheme.decode('utf-8') if isinstance(request.url.scheme, bytes) else str(request.url.scheme)
        host = request.url.host.decode('utf-8') if isinstance(request.url.host, bytes) else str(request.url.host)
        audience = f"{scheme}://{host}"
        if request.url.port and request.url.port not in (80, 443):
            audience += f":{request.url.port}"
        token_added = False

        # 1. Try to fetch OIDC ID token for Cloud Run IAM authentication
        try:
            id_token = fetch_cloud_run_id_token(self._auth_request, audience, self.credentials)
            request.headers['Authorization'] = f'Bearer {id_token}'
            logger.info(f"🔒 [MCP AUTH] Attached OIDC ID Token to {request.method} {request.url}")
            token_added = True
        except Exception as e:
            logger.warning(f"⚠️ [MCP AUTH] Could not fetch OIDC ID token for audience {audience}: {e}")

        # 2. Fall back to OAuth2 access token if ID token fetch failed
        if not token_added and self.credentials:
            try:
                if not self.credentials.valid:
                    self.credentials.refresh(self._auth_request)
                if self.credentials.token:
                    request.headers['Authorization'] = f'Bearer {self.credentials.token}'
                    logger.warning(
                        f"⚠️ [MCP AUTH Fallback] Attached OAuth2 access token for {audience}. "
                        "Note: Cloud Run ingress requires OIDC ID tokens and will return 401 for OAuth2 access tokens."
                    )
            except Exception as e:
                logger.error(f"❌ [MCP AUTH] Could not fetch OAuth2 access token fallback: {e}")

        yield request


def create_authenticated_mcp_client(**kwargs) -> httpx.AsyncClient:
    """Create an httpx AsyncClient with Google Cloud authentication.

    This factory function is used by MCP's httpx_client_factory parameter.

    Args:
        **kwargs: Additional arguments passed to httpx.AsyncClient

    Returns:
        Configured httpx.AsyncClient with Google Cloud auth
    """
    # Set default timeout if not provided
    if 'timeout' not in kwargs:
        kwargs['timeout'] = httpx.Timeout(
            timeout=300.0,   # 5 minutes total
            connect=10.0,    # 10s to establish connection
            read=120.0,      # 120s between read chunks
            write=30.0       # 30s for writes
        )

    # Add Google Cloud authentication
    kwargs['auth'] = GoogleCloudAuth()

    logger.debug(f"Creating authenticated MCP httpx client with timeout={kwargs.get('timeout')}")
    return httpx.AsyncClient(**kwargs)


