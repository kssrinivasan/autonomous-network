import logging

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
