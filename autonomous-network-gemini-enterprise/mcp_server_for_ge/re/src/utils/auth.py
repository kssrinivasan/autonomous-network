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

"""Authentication for callers of the MCP tool plane.

These tools are not read-only -- they deploy configs, inject faults and drive
Gitea -- so the server needs to know who is calling. Two verifiers are offered
because the two deployments of this code have different callers:

``GoogleOIDCTokenVerifier`` (``MCP_AUDIENCE``) is for the Reasoning Engine
server, ``re/``. Cloud Run's ``--no-allow-unauthenticated`` already rejects
anyone without a valid Google identity, but it does not say *which* identity,
so this adds the second half: the token's audience must be this service's own
URL, and its email must be on the allowlist.

``SharedSecretTokenVerifier`` (``MCP_SHARED_SECRET``) is for the Gemini
Enterprise server, ``ge/``. Gemini Enterprise connects as a Tool Data Store and
does not present a Google ID token, so the OIDC path cannot work and Cloud Run
has to stay ``--allow-unauthenticated`` for GE to reach the service at all.
That left the endpoint completely open. A bearer secret, configured on the GE
tool data store and mounted here from Secret Manager, is the credential that
closes it.

Set neither and the server starts with no verifier -- which is the state ``ge/``
was in, and is now only reachable by explicitly clearing the secret.
"""

import hmac
import logging
import time

from fastmcp.server.auth import TokenVerifier, AccessToken
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)


class GoogleOIDCTokenVerifier(TokenVerifier):
    """Verifies Google OIDC ID tokens and enforces an email allowlist."""

    def __init__(self, audience: str, allowed_emails: list[str]):
        super().__init__()
        self.audience = audience
        self.allowed_emails = set(allowed_emails)
        self._request = google_requests.Request()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            info = id_token.verify_oauth2_token(token, self._request, audience=self.audience)
            email = info.get("email")
            if email not in self.allowed_emails:
                logger.warning("OIDC token from disallowed email: %s", email)
                return None
            return AccessToken(
                token=token,
                client_id=email,
                scopes=[],
                expires_at=info["exp"],
                claims=info,
            )
        except Exception as e:
            logger.warning("OIDC token verification failed: %s", e)
            return None


class SharedSecretTokenVerifier(TokenVerifier):
    """Accepts a single pre-shared bearer secret.

    For callers that cannot present a Google ID token -- in practice Gemini
    Enterprise, which reaches this service as a Tool Data Store. Weaker than
    OIDC: it identifies the *deployment*, not a principal, and it does not
    expire. It is used only where the alternative is no authentication at all,
    and it is paired with ``MCP_ENABLE_MUTATING_TOOLS`` being off, so a leaked
    secret exposes reads rather than writes.
    """

    #: Long enough that guessing is not a concern; deploy.sh generates 64 hex
    #: characters. Rejecting anything shorter stops a deployment from being
    #: "authenticated" by a placeholder like "changeme".
    MIN_SECRET_LENGTH = 32

    def __init__(self, secret: str):
        super().__init__()
        # Strip before comparing. Secret Manager stores whatever bytes it was
        # given, and a generator as ordinary as `openssl rand -hex 32` ends its
        # output with a newline -- so the mounted env var was 65 characters
        # while every client that pastes the value from
        # `gcloud secrets versions access latest` sends 64. That mismatch made
        # the correct secret fail with "invalid_token". A bearer token cannot
        # contain surrounding whitespace anyway, so nothing is lost here.
        secret = secret.strip()
        if len(secret) < self.MIN_SECRET_LENGTH:
            raise ValueError(
                f"MCP_SHARED_SECRET must be at least {self.MIN_SECRET_LENGTH} "
                f"characters; got {len(secret)}"
            )
        self._secret = secret

    async def verify_token(self, token: str) -> AccessToken | None:
        # compare_digest, not ==, so a wrong secret cannot be recovered one
        # character at a time from response timing.
        if not hmac.compare_digest(token, self._secret):
            logger.warning("Shared-secret verification failed")
            return None
        return AccessToken(
            token=token,
            client_id="gemini-enterprise",
            scopes=[],
            # The secret does not expire. FastMCP wants a timestamp, so this is
            # a rolling one-hour window rather than a meaningful lifetime;
            # rotation is a redeploy, not a token refresh.
            expires_at=int(time.time()) + 3600,
            claims={"sub": "gemini-enterprise", "auth_method": "shared_secret"},
        )
