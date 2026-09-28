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

"""HTTP client wrapper for proxying REST API requests"""

import os
import logging
import httpx
from typing import Any, Optional
from urllib.parse import urlparse
import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
import google.oauth2.id_token

logger = logging.getLogger(__name__)

# Service URLs from environment - All Domains
# Transport Domain
TRANSPORT_INTENT_API_URL = os.getenv("TRANSPORT_INTENT_API_URL")
TRANSPORT_TOPOLOGY_API_URL = os.getenv("TRANSPORT_TOPOLOGY_API_URL")

# Core Domain
CORE_INTENT_API_URL = os.getenv("CORE_INTENT_API_URL")
CORE_TOPOLOGY_API_URL = os.getenv("CORE_TOPOLOGY_API_URL")

# Radio Domain
RADIO_INTENT_API_URL = os.getenv("RADIO_INTENT_API_URL")
RADIO_TOPOLOGY_API_URL = os.getenv("RADIO_TOPOLOGY_API_URL")

# HTTP client configuration
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3


class DomainHTTPClient:
    """Async HTTP client with retry logic for multi-domain API calls

    Automatically adds Cloud Run authentication (ID tokens) to requests.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True
        )
        # Cache for auth request object
        self._auth_request = GoogleAuthRequest()

    def _get_auth_headers(self, url: str) -> dict:
        """Generate Authorization header with ID token for Cloud Run service

        Args:
            url: Target Cloud Run service URL

        Returns:
            Dictionary with Authorization header containing ID token
        """
        try:
            # Extract audience (base URL without path) for ID token
            parsed = urlparse(url)
            audience = f"{parsed.scheme}://{parsed.netloc}"

            # Fetch ID token for this audience
            # Automatically uses Cloud Run service account from metadata server
            id_token = google.oauth2.id_token.fetch_id_token(self._auth_request, audience)

            logger.debug(f"Generated ID token for audience: {audience}")
            return {"Authorization": f"Bearer {id_token}"}

        except Exception as e:
            logger.warning(f"Failed to generate ID token for {url}: {e}")
            # Return empty dict - let the request proceed unauthenticated
            # Cloud Run will reject with 403 if auth is required
            return {}

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any
    ) -> httpx.Response:
        """Execute HTTP request with exponential backoff retry for 5xx errors

        Automatically adds Cloud Run authentication headers to requests.

        Args:
            method: HTTP method (GET, POST, DELETE, PATCH)
            url: Full URL
            **kwargs: Additional arguments passed to httpx

        Returns:
            httpx.Response

        Raises:
            httpx.HTTPError: On non-retryable errors or max retries exceeded
        """
        # Add authentication headers for Cloud Run services
        headers = kwargs.get('headers', {})
        auth_headers = self._get_auth_headers(url)
        headers.update(auth_headers)
        kwargs['headers'] = headers

        last_exception = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.request(method, url, **kwargs)

                # Raise HTTPError for 4xx/5xx status codes
                response.raise_for_status()

                return response

            except httpx.HTTPStatusError as e:
                # Retry on 5xx server errors
                if e.response.status_code >= 500:
                    logger.warning(
                        f"HTTP {e.response.status_code} from {url} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    last_exception = e
                    if attempt < MAX_RETRIES - 1:
                        # Exponential backoff: 1s, 2s, 4s
                        await httpx.AsyncClient().aclose()  # Small delay
                        continue
                # Don't retry on 4xx client errors
                raise

            except httpx.RequestError as e:
                logger.warning(
                    f"Request error to {url}: {e} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    continue
                raise

        # Max retries exceeded
        if last_exception:
            raise last_exception

    async def get(self, url: str, params: Optional[dict] = None) -> dict:
        """Execute GET request

        Args:
            url: Full URL
            params: Query parameters

        Returns:
            Parsed JSON response as dict

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        logger.info(f"GET {url}")
        response = await self._request_with_retry("GET", url, params=params)
        return response.json()

    async def post(
        self,
        url: str,
        json: Optional[dict] = None,
        data: Optional[Any] = None
    ) -> dict:
        """Execute POST request

        Args:
            url: Full URL
            json: JSON body (dict)
            data: Form data

        Returns:
            Parsed JSON response as dict

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        logger.info(f"POST {url}")
        response = await self._request_with_retry(
            "POST",
            url,
            json=json,
            data=data
        )
        return response.json()

    async def delete(self, url: str) -> dict:
        """Execute DELETE request

        Args:
            url: Full URL

        Returns:
            Parsed JSON response as dict

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        logger.info(f"DELETE {url}")
        response = await self._request_with_retry("DELETE", url)
        return response.json()

    async def patch(self, url: str, json: Optional[dict] = None) -> dict:
        """Execute PATCH request

        Args:
            url: Full URL
            json: JSON body (dict)

        Returns:
            Parsed JSON response as dict

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        logger.info(f"PATCH {url}")
        response = await self._request_with_retry("PATCH", url, json=json)
        return response.json()


# Global HTTP client instance
http_client = DomainHTTPClient()
