# Copyright 2024-2025 Google LLC
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

"""
Shared Git / Gitea utilities for network agents and tools.

This module consolidates all Gitea interaction into one place:

  1. SDK patches  – extends the upstream ``gitea`` library with the missing
                    ``delete_file`` method and a helper to build API URLs.
  2. URL discovery – ``get_gitea_url()`` reads the Gitea IP from the K8s
                    ``Gitea`` custom resource.
  3. Core helpers  – ``get_repo()``, ``git_file_exists()``, ``get_git_file()``,
                    ``commit_git_file()``, ``delete_git_file()``, and
                    ``delete_all_git_files()`` provide the low-level CRUD
                    operations used by agents and tools.

"""

import base64
import json
import logging
import os

import kubernetes
from gitea import Gitea, Repository

from utils.k8s import get_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# FIXME: change these user credentials into an authentication token
# generated at Gitea server creation time
USER = os.environ.get('WEBAPPS_LOGIN', '')
PWD = os.environ.get('WEBAPPS_PWD', '')
MASTER_BRANCH = 'main'
GITEA_PORT = 3000

# ---------------------------------------------------------------------------
# Error logging helpers
# ---------------------------------------------------------------------------

def _log_http_error(logger_instance, operation: str, exception: Exception) -> None:
    """Extract and log detailed HTTP error information from exceptions.

    Many exceptions from the gitea library wrap HTTP responses but the
    underlying status code and response body are buried in the exception
    string. This helper extracts what it can to make debugging easier.
    """
    exc_str = str(exception)
    exc_type = type(exception).__name__

    # Try to extract HTTP status from common patterns in exception messages
    http_status = None
    if 'status code:' in exc_str.lower():
        import re
        match = re.search(r'status code:\s*(\d+)', exc_str, re.IGNORECASE)
        if match:
            http_status = match.group(1)

    # Check if the exception has a response attribute (requests library pattern)
    response_text = None
    if hasattr(exception, 'response') and exception.response is not None:
        try:
            http_status = exception.response.status_code
            response_text = exception.response.text[:500]
        except:
            pass

    error_parts = [f"{operation} failed: {exc_type}"]
    if http_status:
        error_parts.append(f"HTTP {http_status}")
    if response_text:
        error_parts.append(f"Response: {response_text}")
    error_parts.append(f"Exception: {exc_str}")

    logger_instance.error(" | ".join(error_parts))

# ---------------------------------------------------------------------------
# SDK patches
# ---------------------------------------------------------------------------

def _local_get_url(self, endpoint: str) -> str:
    """Build a full Gitea API URL. Reimplemented here because the upstream
    ``Gitea.__get_url`` is private and therefore inaccessible externally."""
    url = self.url + "/api/v1" + endpoint
    self.logger.debug("Url: %s", url)
    return url


def _requests_delete_with_data(self, endpoint: str, data: dict = None):
    """Issue an HTTP DELETE request with a JSON body (not natively supported
    by the upstream Gitea client)."""
    if not data:
        data = {}

    url = self.local_get_url(endpoint)
    self.logger.debug("DELETE %s with data: %s", url, json.dumps(data, indent=2))

    request = self.requests.delete(url, headers=self.headers, data=json.dumps(data))

    if request.status_code not in [200, 204]:
        message = (
            f"DELETE {endpoint} failed: HTTP {request.status_code} | "
            f"URL: {request.url} | "
            f"Request data: {json.dumps(data)} | "
            f"Response: {request.text[:500]}"
        )
        self.logger.error(message)
        raise Exception(message)

    self.logger.debug("DELETE %s succeeded: HTTP %s", endpoint, request.status_code)


def _requests_put_with_logging(self, endpoint: str, data: dict = None):
    """Enhanced PUT request with detailed logging for large file debugging."""
    if not data:
        data = {}

    url = self.local_get_url(endpoint)
    payload = json.dumps(data)
    payload_size = len(payload)

    # Log request details, but truncate content field if present
    log_data = data.copy()
    if 'content' in log_data and len(log_data['content']) > 100:
        log_data['content'] = f"<base64 content: {len(log_data['content'])} chars>"

    self.logger.debug(
        "PUT %s | payload_size=%d bytes | data_keys=%s",
        url, payload_size, list(log_data.keys())
    )

    request = self.requests.put(url, headers=self.headers, data=payload)

    if request.status_code in [200, 204]:
        self.logger.debug("PUT %s succeeded: HTTP %s", endpoint, request.status_code)
        return

    # Enhanced error message with size information
    message = (
        f"PUT {endpoint} failed: HTTP {request.status_code} | "
        f"URL: {request.url} | "
        f"Payload size: {payload_size} bytes | "
        f"Data keys: {list(data.keys())} | "
        f"Response: {request.text[:500]}"
    )
    self.logger.error(message)
    # Call the private method via name mangling
    self._Gitea__http_to_exception(request.status_code, message)


def _requests_post_with_logging(self, endpoint: str, data: dict):
    """Enhanced POST request with detailed logging for large file debugging."""
    url = self.local_get_url(endpoint)
    payload = json.dumps(data)
    payload_size = len(payload)

    # Log request details, but truncate content field if present
    log_data = data.copy()
    if 'content' in log_data and len(log_data['content']) > 100:
        log_data['content'] = f"<base64 content: {len(log_data['content'])} chars>"

    self.logger.debug(
        "POST %s | payload_size=%d bytes | data_keys=%s",
        url, payload_size, list(log_data.keys())
    )

    request = self.requests.post(url, headers=self.headers, data=payload)

    if request.status_code in [200, 201, 202]:
        self.logger.debug("POST %s succeeded: HTTP %s", endpoint, request.status_code)
        return self.parse_result(request)

    # Enhanced error message with size information
    message = (
        f"POST {endpoint} failed: HTTP {request.status_code} | "
        f"URL: {request.url} | "
        f"Payload size: {payload_size} bytes | "
        f"Data keys: {list(data.keys())} | "
        f"Response: {request.text[:500]}"
    )

    if "already exists" in request.text or "e-mail already in use" in request.text:
        self.logger.warning(request.text)

    self.logger.error(message)
    # Call the private method via name mangling
    self._Gitea__http_to_exception(request.status_code, message)


def _delete_file(self, file_path: str, file_sha: str, data: dict = None):
    """Delete a file from the repository via the Gitea contents API."""
    if not data:
        data = {}
    url = f"/repos/{self.owner.username}/{self.name}/contents/{file_path}"
    data.update({"sha": file_sha})
    return self.gitea.requests_delete_with_data(url, data)


# Patch the upstream classes once at import time.
Gitea.local_get_url = _local_get_url
Gitea.requests_delete_with_data = _requests_delete_with_data
Gitea.requests_put = _requests_put_with_logging
Gitea.requests_post = _requests_post_with_logging
Repository.delete_file = _delete_file

# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------

def get_gitea_url() -> str | None:
    """Return the Gitea base URL from the GITEA_URL environment variable.

    Returns:
        The Gitea URL from environment, or None if not configured.
    """
    env_url = os.environ.get('GITEA_URL')
    if env_url:
        logger.debug("Using Gitea URL from GITEA_URL environment variable: %s", env_url)
        return env_url

    logger.error(
        "GITEA_URL environment variable not set. "
        "Please configure GITEA_URL in your .env file or environment."
    )
    return None

# ---------------------------------------------------------------------------
# Core git helpers
# ---------------------------------------------------------------------------

def get_repo(repo_name: str) -> Repository:
    """Open and return the named Gitea repository.

    Raises:
        Exception if Gitea URL is unavailable or repository cannot be accessed.
    """
    url = get_gitea_url()
    if not url:
        logger.error("get_repo: cannot access repo '%s' - Gitea URL unavailable", repo_name)
        raise Exception(f"Gitea URL unavailable - cannot access repo '{repo_name}'")

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo = Repository.request(gitea, USER, repo_name)
        logger.debug("get_repo: successfully connected to '%s/%s'", USER, repo_name)
        return repo
    except Exception as e:
        logger.error(
            "get_repo: failed to access repository '%s/%s' at %s: %s",
            USER, repo_name, url, e, exc_info=True
        )
        raise


def git_file_exists(repo: Repository, file_path: str):
    """Return the content object for *file_path* in *repo*, or ``None``."""
    try:
        contents = repo.get_git_content()
    except Exception as e:
        # Empty repo causes the Gitea API to return HTTP 409, which the
        # gitea client raises as an exception rather than returning None.
        # Treat any error here as "file not found".
        logger.debug(
            "git_file_exists('%s'): get_git_content raised %s (likely empty repo)",
            file_path, type(e).__name__
        )
        return None
    if not contents:
        logger.debug("git_file_exists('%s'): repo has no contents", file_path)
        return None
    files = [c for c in contents if c.name == file_path]
    if files:
        logger.debug("git_file_exists('%s'): found (sha=%s)", file_path, files[0].sha)
    else:
        logger.debug("git_file_exists('%s'): not found in repo", file_path)
    return files[0] if files else None


def get_git_file(repo_name: str, filename: str) -> str | None:
    """
    Retrieve a file from a Gitea repository by name.

    Args:
        repo_name: Repository name (e.g., "network", "networkdesign")
        filename: File name or path in the repo (e.g., "config.yaml", "knowledge/designdoc.md")

    Returns:
        str: File contents as a UTF-8 string, or None if not found
    """
    url = get_gitea_url()
    if not url:
        logger.error(
            "get_git_file('%s', '%s'): cannot retrieve Gitea URL",
            repo_name, filename
        )
        return None

    logger.debug("get_git_file: Gitea server at %s, repo=%s, file=%s", url, repo_name, filename)
    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo = Repository.request(gitea, USER, repo_name)
    except Exception as e:
        _log_http_error(logger, f"get_git_file: connect to repo '{USER}/{repo_name}'", e)
        logger.debug("Full exception:", exc_info=True)
        return None

    try:
        # Use the Gitea API directly to get file content at a specific path
        # This works for both root-level files and nested paths like "knowledge/designdoc.md"
        endpoint = f"/repos/{USER}/{repo_name}/contents/{filename}"
        url_full = gitea.local_get_url(endpoint)

        logger.debug("get_git_file: fetching %s", url_full)
        response = gitea.requests.get(url_full, headers=gitea.headers)

        if response.status_code == 404:
            logger.debug("get_git_file: file '%s' not found in repo '%s'", filename, repo_name)
            return None

        if response.status_code != 200:
            logger.error(
                "get_git_file: HTTP %d fetching '%s' from '%s': %s",
                response.status_code, filename, repo_name, response.text[:200]
            )
            return None

        data = response.json()

        # Check if it's a directory (will be a list)
        if isinstance(data, list):
            logger.error(
                "get_git_file: '%s' is a directory in repo '%s', not a file",
                filename, repo_name
            )
            return None

        # Get the base64-encoded content
        if 'content' not in data:
            logger.error(
                "get_git_file: response for '%s' has no 'content' field: %s",
                filename, list(data.keys())
            )
            return None

        # Decode the base64 content (Gitea returns it with newlines, so remove them first)
        content_b64 = data['content'].replace('\n', '')
        decoded = base64.b64decode(content_b64).decode("utf-8")
        logger.debug("get_git_file: successfully retrieved '%s' (%d bytes)", filename, len(decoded))
        return decoded

    except Exception as e:
        _log_http_error(logger, f"get_git_file: read '{filename}' from '{repo_name}'", e)
        logger.debug("Full exception:", exc_info=True)
        return None


def commit_git_file(
    file_path: str,
    message: str,
    content: str,
    repo_name: str,
    branch: str = MASTER_BRANCH,
) -> bool | None:
    """Create or update *file_path* in *repo_name* with *content*.

    Args:
        file_path: Path to the file in the repository
        message: Git commit message
        content: File content (plain text, will be base64-encoded internally)
        repo_name: Target repository name
        branch: Branch name (default: 'master')

    Returns:
        ``True`` on success, ``None`` on failure.
    """
    url = get_gitea_url()
    if not url:
        logger.error(
            "commit_git_file: cannot commit '%s' to '%s' - Gitea URL unavailable",
            file_path, repo_name
        )
        return None

    logger.debug(
        "commit_git_file: repo=%s, branch=%s, file=%s, content_size=%d bytes",
        repo_name, branch, file_path, len(content)
    )

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo = Repository.request(gitea, USER, repo_name)
    except Exception as e:
        logger.error(
            "commit_git_file: failed to connect to repo '%s/%s': %s",
            USER, repo_name, e, exc_info=True
        )
        return None

    b64_content = base64.b64encode(bytes(content, "utf-8"))
    logger.debug(
        "commit_git_file: encoded content size: %d bytes raw -> %d bytes base64 (%.1f%% overhead)",
        len(content), len(b64_content), (len(b64_content) / len(content) - 1) * 100
    )

    try:
        # Ensure the target branch exists; create it from master if it doesn't.
        if branch != MASTER_BRANCH:
            branch_endpoint = f"/repos/{USER}/{repo_name}/branches/{branch}"
            branch_url = gitea.local_get_url(branch_endpoint)
            branch_resp = gitea.requests.get(branch_url, headers=gitea.headers)
            if branch_resp.status_code == 404:
                logger.info(
                    "commit_git_file: branch '%s' does not exist in '%s', creating from '%s'",
                    branch, repo_name, MASTER_BRANCH
                )
                create_branch_endpoint = f"/repos/{USER}/{repo_name}/branches"
                create_url = gitea.local_get_url(create_branch_endpoint)
                create_resp = gitea.requests.post(
                    create_url,
                    headers=gitea.headers,
                    data=json.dumps({"new_branch_name": branch, "old_branch_name": MASTER_BRANCH})
                )
                if create_resp.status_code not in [200, 201]:
                    logger.error(
                        "commit_git_file: failed to create branch '%s' in '%s': HTTP %d %s",
                        branch, repo_name, create_resp.status_code, create_resp.text[:200]
                    )
                    return None
                logger.info("commit_git_file: branch '%s' created in '%s'", branch, repo_name)

        # When writing to non-master branches (e.g., 'update'), ALWAYS create new files.
        # These branches are ephemeral and files never exist there yet.
        # Only check for existing files when updating the master branch.
        if branch != MASTER_BRANCH:
            logger.debug(
                "commit_git_file: creating new file '%s' in repo '%s' on branch '%s' (non-master branch)",
                file_path, repo_name, branch
            )
            repo.create_file(
                file_path,
                content=b64_content.decode("ascii"),
                data={'branch': branch, 'message': message},
            )
            logger.info(
                "commit_git_file: successfully created '%s' in repo '%s' on branch '%s'",
                file_path, repo_name, branch
            )
        elif (file := git_file_exists(repo, file_path)) is not None:
            logger.debug(
                "commit_git_file: updating existing file '%s' (sha=%s) in repo '%s' on branch '%s'",
                file.path, file.sha, repo_name, branch
            )
            repo.change_file(
                file.path, file.sha,
                content=b64_content.decode("ascii"),
                data={'branch': branch, 'message': message + ' - Updated'},
            )
            logger.info(
                "commit_git_file: successfully updated '%s' in repo '%s' on branch '%s'",
                file_path, repo_name, branch
            )
        else:
            logger.debug(
                "commit_git_file: creating new file '%s' in repo '%s' on branch '%s'",
                file_path, repo_name, branch
            )
            repo.create_file(
                file_path,
                content=b64_content.decode("ascii"),
                data={'branch': branch, 'message': message},
            )
            logger.info(
                "commit_git_file: successfully created '%s' in repo '%s' on branch '%s'",
                file_path, repo_name, branch
            )
        return True
    except Exception as e:
        _log_http_error(
            logger,
            f"commit_git_file('{file_path}' to '{repo_name}' on '{branch}')",
            e
        )
        logger.error(
            "commit_git_file: context - Message: '%s' | Content preview: %s",
            message, content[:200] + '...' if len(content) > 200 else content
        )
        logger.debug("Full exception:", exc_info=True)
        return None


def delete_git_file(
    file_path: str,
    message: str,
    repo_name: str,
    branch: str = MASTER_BRANCH,
) -> bool | None:
    """Delete *file_path* from *repo_name*.

    Args:
        file_path: Path to the file in the repository
        message: Git commit message
        repo_name: Target repository name
        branch: Branch name (default: 'master')

    Returns:
        ``True`` on success, ``None`` on failure.
    """
    url = get_gitea_url()
    if not url:
        logger.error(
            "delete_git_file: cannot delete '%s' from '%s' - Gitea URL unavailable",
            file_path, repo_name
        )
        return None

    logger.debug(
        "delete_git_file: repo=%s, branch=%s, file=%s",
        repo_name, branch, file_path
    )

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo = Repository.request(gitea, USER, repo_name)
    except Exception as e:
        logger.error(
            "delete_git_file: failed to connect to repo '%s/%s': %s",
            USER, repo_name, e, exc_info=True
        )
        return None

    try:
        if file := git_file_exists(repo, file_path):
            logger.debug(
                "delete_git_file: deleting '%s' (sha=%s) from repo '%s'",
                file_path, file.sha, repo_name
            )
            repo.delete_file(
                file_path, file.sha,
                data={'branch': branch, 'message': message},
            )
            logger.info(
                "delete_git_file: successfully deleted '%s' from repo '%s' on branch '%s'",
                file_path, repo_name, branch
            )
            return True
        else:
            logger.warning(
                "delete_git_file: file '%s' not found in repo '%s' (already deleted or never existed)",
                file_path, repo_name
            )
            return None
    except Exception as e:
        _log_http_error(
            logger,
            f"delete_git_file('{file_path}' from '{repo_name}' on '{branch}')",
            e
        )
        logger.debug("Full exception:", exc_info=True)
        return None


def delete_all_git_files(
    message: str,
    repo_name: str,
) -> tuple[list[str], list[str]]:
    """Delete every file currently in *repo_name*.

    Useful for a full clean-slate teardown where the set of files in the repo
    may differ from what is described in a stored network descriptor (e.g.
    files left over from a previous deployment).

    Args:
        message:   Git commit message applied to each deletion.
        repo_name: Target Gitea repository.

    Returns:
        Tuple of ``(deleted_filenames, failed_filenames)``.
    """
    deleted: list[str] = []
    failed: list[str] = []

    url = get_gitea_url()
    if not url:
        logger.error(
            "delete_all_git_files: cannot delete files from '%s' - Gitea URL unavailable",
            repo_name
        )
        failed.append("Gitea URL unavailable")
        return deleted, failed

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo = Repository.request(gitea, USER, repo_name)
    except Exception as e:
        logger.error(
            "delete_all_git_files: failed to connect to repo '%s/%s': %s",
            USER, repo_name, e, exc_info=True
        )
        failed.append(f"Failed to connect: {e}")
        return deleted, failed

    try:
        contents = repo.get_git_content()
    except Exception as e:
        # Empty repo causes the Gitea API to throw instead of returning None.
        logger.debug(
            "delete_all_git_files: repo '%s' is already empty (get_git_content raised %s)",
            repo_name, type(e).__name__
        )
        return deleted, failed

    if not contents:
        logger.debug("delete_all_git_files: repo '%s' is already empty", repo_name)
        return deleted, failed

    logger.info(
        "delete_all_git_files: deleting %d files from repo '%s'",
        len(contents), repo_name
    )

    for file in contents:
        try:
            repo.delete_file(
                file.name, file.sha,
                data={'branch': MASTER_BRANCH, 'message': message},
            )
            deleted.append(file.name)
            logger.debug("Deleted '%s' from repo '%s'", file.name, repo_name)
        except Exception as e:
            failed.append(file.name)
            _log_http_error(
                logger,
                f"delete_all_git_files: delete '{file.name}' from '{repo_name}'",
                e
            )
            logger.debug("Full exception:", exc_info=True)

    logger.info(
        "delete_all_git_files: repo '%s' cleanup complete - deleted: %d, failed: %d",
        repo_name, len(deleted), len(failed)
    )
    return deleted, failed
