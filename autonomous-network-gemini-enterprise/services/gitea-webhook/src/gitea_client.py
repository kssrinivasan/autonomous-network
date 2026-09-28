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

"""Gitea API client for fetching diffs and posting comments."""

import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)


class GiteaClient:
    """Client for interacting with Gitea API."""

    def __init__(self, gitea_url: str, api_token: str):
        """Initialize Gitea client.

        Args:
            gitea_url: Base URL of Gitea server (e.g., https://gitea.example.com:3000)
            api_token: Gitea API token for authentication
        """
        self.gitea_url = gitea_url.rstrip('/')
        self.api_token = api_token
        self.headers = {
            'Authorization': f'token {api_token}',
            'Content-Type': 'application/json'
        }

    async def get_commit_diff(self, owner: str, repo: str, commit_sha: str) -> str:
        """Fetch the diff for a specific commit.

        Args:
            owner: Repository owner username
            repo: Repository name
            commit_sha: Commit SHA

        Returns:
            Diff content as text

        Raises:
            Exception: If API request fails
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/git/commits/{commit_sha}.diff'

        logger.info(f"Fetching diff for commit {commit_sha[:8]} from {url}")

        async with httpx.AsyncClient(verify=False) as client:  # SSL verify=False for self-signed certs
            try:
                response = await client.get(url, headers=self.headers, timeout=30.0)
                response.raise_for_status()

                diff_content = response.text
                logger.info(f"Successfully fetched diff ({len(diff_content)} bytes)")
                return diff_content

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error fetching diff: {e.response.status_code}")
                raise Exception(f"Failed to fetch commit diff: {e}")
            except httpx.RequestError as e:
                logger.error(f"Request error fetching diff: {e}")
                raise Exception(f"Failed to connect to Gitea: {e}")

    async def get_commit_files(self, owner: str, repo: str, commit_sha: str) -> list[str]:
        """Get list of files changed in a commit.

        Args:
            owner: Repository owner username
            repo: Repository name
            commit_sha: Commit SHA

        Returns:
            List of file paths

        Raises:
            Exception: If API request fails
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/git/commits/{commit_sha}'

        logger.info(f"Fetching commit info for {commit_sha[:8]}")

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.get(url, headers=self.headers, timeout=30.0)
                response.raise_for_status()

                commit_data = response.json()

                # Extract files from commit data
                files = []
                if 'files' in commit_data:
                    files = [f['filename'] for f in commit_data['files']]

                logger.info(f"Found {len(files)} changed files")
                return files

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error fetching commit info: {e.response.status_code}")
                raise Exception(f"Failed to fetch commit info: {e}")
            except httpx.RequestError as e:
                logger.error(f"Request error fetching commit info: {e}")
                raise Exception(f"Failed to connect to Gitea: {e}")

    async def post_commit_comment(self, owner: str, repo: str, commit_sha: str,
                                  comment: str) -> bool:
        """Post a comment on a commit.

        Args:
            owner: Repository owner username
            repo: Repository name
            commit_sha: Commit SHA
            comment: Comment text (markdown supported)

        Returns:
            True if successful

        Raises:
            Exception: If API request fails
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/commits/{commit_sha}/comments'

        logger.info(f"Posting comment to commit {commit_sha[:8]}")

        payload = {
            'body': comment
        }

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()

                logger.info(f"Successfully posted comment to commit {commit_sha[:8]}")
                return True

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error posting comment: {e.response.status_code}")
                # Don't raise - just log and return False so webhook can still succeed
                return False
            except httpx.RequestError as e:
                logger.error(f"Request error posting comment: {e}")
                return False

    async def get_pull_requests(self, owner: str, repo: str, state: str = "open") -> list[dict]:
        """Fetch pull requests for a repository.

        Args:
            owner: Repository owner username
            repo: Repository name
            state: PR state ('open', 'closed', 'all')

        Returns:
            List of pull request dicts
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/pulls'
        params = {'state': state}

        logger.info(f"Fetching {state} pull requests from {url}")

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.get(url, headers=self.headers, params=params, timeout=30.0)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Failed to fetch pull requests: {e}")
                return []

    async def create_pull_request(self, owner: str, repo: str, title: str, body: str,
                                  head: str, base: str = "main") -> Optional[dict]:
        """Create a new pull request.

        Args:
            owner: Repository owner username
            repo: Repository name
            title: PR title
            body: PR body description
            head: Source branch name (e.g. 'update')
            base: Target branch name (default: 'main')

        Returns:
            Created PR dict, or None if failed
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/pulls'
        payload = {
            'title': title,
            'body': body,
            'head': head,
            'base': base
        }

        logger.info(f"Creating pull request '{title}' from {head} to {base}")

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.post(url, headers=self.headers, json=payload, timeout=30.0)
                response.raise_for_status()
                pr_data = response.json()
                logger.info(f"Successfully created PR #{pr_data.get('number')}")
                return pr_data
            except Exception as e:
                logger.error(f"Failed to create pull request: {e}")
                return None

    async def post_pr_comment(self, owner: str, repo: str, pr_number: int, comment: str) -> bool:
        """Post a comment to a pull request (timeline comment via issues API).

        Args:
            owner: Repository owner username
            repo: Repository name
            pr_number: Pull request index number
            comment: Comment text (markdown supported)

        Returns:
            True if successful, False otherwise
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/issues/{pr_number}/comments'
        payload = {
            'body': comment
        }

        logger.info(f"Posting PR comment to PR #{pr_number} via {url}")

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.post(url, headers=self.headers, json=payload, timeout=30.0)
                response.raise_for_status()
                logger.info(f"Successfully posted comment to PR #{pr_number}")
                return True
            except Exception as e:
                logger.error(f"Failed to post PR comment: {e}")
                return False

    async def merge_pull_request(self, owner: str, repo: str, pr_number: int) -> bool:
        """Merge an open pull request.

        Args:
            owner: Repository owner username
            repo: Repository name
            pr_number: Pull request index number

        Returns:
            True if successful, False otherwise
        """
        url = f'{self.gitea_url}/api/v1/repos/{owner}/{repo}/pulls/{pr_number}/merge'
        payload = {
            'Do': 'merge'
        }

        logger.info(f"Merging Pull Request #{pr_number} via {url}")

        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.post(url, headers=self.headers, json=payload, timeout=30.0)
                response.raise_for_status()
                logger.info(f"Successfully merged PR #{pr_number}")
                return True
            except Exception as e:
                logger.error(f"Failed to merge PR #{pr_number}: {e}")
                return False
