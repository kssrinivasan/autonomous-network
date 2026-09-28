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

"""
Shared Git / Gitea utilities for network agents and tools.

This module consolidates all Gitea interaction into one place:

  1. SDK patches  – extends the upstream ``gitea`` library with the missing
                    ``delete_file`` method and a helper to build API URLs.
  2. URL discovery – ``get_gitea_url()`` reads ``GITEA_URL``, falling back to
                    the Cloud Run service Gitea is deployed as.
  3. Core helpers  – ``get_repo()``, ``git_file_exists()``, ``get_git_file()``,
                    ``commit_git_file()``, ``delete_git_file()``, and
                    ``delete_all_git_files()`` provide the low-level CRUD
                    operations used by agents and tools.

All components should import these utilities from agent_library instead of
maintaining their own copies.
"""

import base64
import json
import logging
import os
import time

import yaml
from gitea import Content, Gitea, Repository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# FIXME: change these user credentials into an authentication token
# generated at Gitea server creation time
USER = os.environ.get('WEBAPPS_LOGIN', '')
PWD = os.environ.get('WEBAPPS_PWD', '')
# The branch every write lands on, and the branch new branches are cut from.
# services/gitea/seed.sh creates each repo with default_branch 'main' and pushes
# HEAD:main -- there is no 'master' ref anywhere in a seeded Gitea, so a
# hardcoded 'master' 404s on every commit, delete and create-branch. The
# constant keeps its name for the callers that import it.
MASTER_BRANCH = os.environ.get('GITEA_BASE_BRANCH', 'main')

# Gitea returns transient 404s on rapid sequential commits, because a git
# ref-lock window briefly hides the repo. Retry rather than surface it.
_MAX_COMMIT_ATTEMPTS = 4
_COMMIT_RETRY_DELAYS = (2.0, 4.0, 8.0)
_DELETE_RETRY_DELAY = 3.0

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
    """Return the Gitea base URL, or None if it cannot be determined.

    Gitea runs on GKE behind an L4 load balancer pinned to the reserved regional
    address ``gitea-ip`` (``services/gitea/deployment.tpl.yaml``). There is no
    hostname: the address *is* the identity, which is why it is reserved rather
    than ephemeral, and why the fallback below looks it up.

    This previously asked the Cloud Run Admin API for the ``gitea`` service's
    URI, and before that read a ``Gitea`` custom resource that no longer exists.
    Both are gone; Gitea left Cloud Run in migration plan 7.2 so that its SQLite
    database and repositories could sit on a persistent disk.

    ``GITEA_URL`` short-circuits the discovery; set it in the deployment when
    the URL is already known, which is the normal case.
    """
    url = os.environ.get('GITEA_URL', '')
    if url:
        return url.rstrip('/')

    try:
        import google.auth
        import requests
        from google.auth.transport.requests import Request

        credentials, project_id = google.auth.default()
        credentials.refresh(Request())
        project = os.environ.get('GOOGLE_PROJECT', project_id)
        region = os.environ.get('GOOGLE_REGION', 'us-central1')
        address = os.environ.get('GITEA_ADDRESS_NAME', 'gitea-ip')

        response = requests.get(
            f"https://compute.googleapis.com/compute/v1/projects/{project}"
            f"/regions/{region}/addresses/{address}",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=30,
        )
        response.raise_for_status()
        ip = (response.json().get('address') or '').strip()
        if ip:
            discovered = f"https://{ip}"
            logger.debug("Gitea URL resolved from reserved address: %s", discovered)
            return discovered
        logger.error(
            "Reserved address '%s' in %s/%s has no IP yet.",
            address, project, region
        )
    except Exception as e:
        logger.error("Could not discover the Gitea URL: %s", e, exc_info=True)
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


def git_file_exists(repo: Repository, file_path: str, branch: str | None = None):
    """Return the content object for *file_path* in *repo*, or ``None``.

    Asks the contents API for the path directly rather than listing the repo
    root and matching on ``name``: ``get_git_content`` is not recursive, so
    the old form reported every nested path as missing and ``commit_git_file``
    would then try to create a file that already exists.

    Args:
        repo: An open ``Repository``.
        file_path: Path within the repository.
        branch: Which branch to look on. ``None`` means the default branch.
            Pass it when working on a review branch such as ``update``, where
            a file may exist that is not on ``main`` or the other way round.
    """
    gitea = repo.gitea
    url = gitea.local_get_url(
        f"/repos/{repo.owner.username}/{repo.name}/contents/{file_path}"
    )
    if branch:
        url += f"?ref={branch}"
    try:
        response = gitea.requests.get(url, headers=gitea.headers)
    except Exception as e:
        logger.debug(
            "git_file_exists('%s'): request raised %s", file_path, type(e).__name__
        )
        return None

    # 404 is "no such file"; an empty repo answers 409. Neither is an error
    # here -- both mean the caller should create the file.
    if response.status_code in (404, 409):
        logger.debug("git_file_exists('%s'): not found in repo", file_path)
        return None
    if response.status_code != 200:
        logger.debug(
            "git_file_exists('%s'): HTTP %d, treating as not found: %s",
            file_path, response.status_code, response.text[:200]
        )
        return None

    data = response.json()
    if isinstance(data, list) or data.get("type") != Content.FILE:
        logger.debug("git_file_exists('%s'): path is not a file", file_path)
        return None

    logger.debug("git_file_exists('%s'): found (sha=%s)", file_path, data.get("sha"))
    return Content.parse_response(gitea, data)


def _ensure_branch(gitea: Gitea, repo_name: str, branch: str) -> None:
    """Create *branch* off ``MASTER_BRANCH`` if it does not exist yet.

    Callers write to short-lived branches (``update`` for the review workflow)
    that nothing creates up front, so the first commit to one would otherwise
    fail with a 404 on the branch.

    Raises on failure so ``commit_git_file`` retries rather than going on to
    commit against a branch that is not there.
    """
    if branch == MASTER_BRANCH:
        return

    url = gitea.local_get_url(f"/repos/{USER}/{repo_name}/branches/{branch}")
    if gitea.requests.get(url, headers=gitea.headers).status_code != 404:
        return

    logger.info(
        "_ensure_branch: branch '%s' does not exist in '%s', creating from '%s'",
        branch, repo_name, MASTER_BRANCH
    )
    create_url = gitea.local_get_url(f"/repos/{USER}/{repo_name}/branches")
    response = gitea.requests.post(
        create_url,
        headers=gitea.headers,
        data=json.dumps({"new_branch_name": branch, "old_branch_name": MASTER_BRANCH}),
    )
    if response.status_code not in (200, 201):
        raise Exception(
            f"failed to create branch '{branch}' in '{repo_name}': "
            f"HTTP {response.status_code} {response.text[:200]}"
        )
    logger.info("_ensure_branch: branch '%s' created in '%s'", branch, repo_name)


def list_git_files(repo_name: str, branch: str = MASTER_BRANCH) -> list[str]:
    """Return the names of the files at the root of *repo_name* on *branch*.

    Root only -- the contents API is not recursive. That is enough for the
    callers that have one, which resolve a descriptor's filename by looking for
    a known suffix among the flat set of CR files.

    Returns an empty list on any failure, including a missing branch, so a
    caller can fall back to a computed name rather than having to catch.
    """
    url = get_gitea_url()
    if not url:
        logger.error("list_git_files: cannot list '%s' - Gitea URL unavailable", repo_name)
        return []

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        api_url = gitea.local_get_url(f"/repos/{USER}/{repo_name}/contents")
        if branch:
            api_url += f"?ref={branch}"
        response = gitea.requests.get(api_url, headers=gitea.headers)
    except Exception as e:
        _log_http_error(logger, f"list_git_files: list '{repo_name}' on '{branch}'", e)
        logger.debug("Full exception:", exc_info=True)
        return []

    # 404 is a missing repo or branch; an empty repo answers 409. Both mean
    # "nothing there", which is what an empty list says.
    if response.status_code in (404, 409):
        logger.debug(
            "list_git_files: '%s' on '%s' returned %d, treating as empty",
            repo_name, branch, response.status_code
        )
        return []
    if response.status_code != 200:
        logger.error(
            "list_git_files: HTTP %d listing '%s' on '%s': %s",
            response.status_code, repo_name, branch, response.text[:200]
        )
        return []

    data = response.json()
    if not isinstance(data, list):
        logger.error("list_git_files: '%s' root is not a directory listing", repo_name)
        return []
    return [entry["name"] for entry in data if entry.get("type") == Content.FILE]


def list_git_yaml_paths(repo_name: str, branch: str = MASTER_BRANCH) -> list[str]:
    """Return every YAML path in *repo_name* on *branch*, at any depth.

    ``list_git_files`` is root-only because the contents API is not recursive.
    The git trees API is, so use it when the answer has to cover a repo that
    keeps its descriptors in directories -- which a repo seeded from
    ``examples/`` does.

    Returns an empty list on any failure, so a caller can fall back to a
    computed name rather than having to catch.
    """
    url = get_gitea_url()
    if not url:
        logger.error("list_git_yaml_paths: cannot list '%s' - Gitea URL unavailable", repo_name)
        return []

    try:
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        api_url = gitea.local_get_url(
            f"/repos/{USER}/{repo_name}/git/trees/{branch or MASTER_BRANCH}?recursive=true"
        )
        response = gitea.requests.get(api_url, headers=gitea.headers)
    except Exception as e:
        _log_http_error(logger, f"list_git_yaml_paths: list '{repo_name}' on '{branch}'", e)
        logger.debug("Full exception:", exc_info=True)
        return []

    if response.status_code != 200:
        logger.debug(
            "list_git_yaml_paths: HTTP %d listing '%s' on '%s', treating as empty",
            response.status_code, repo_name, branch
        )
        return []

    return [
        entry["path"] for entry in response.json().get("tree", [])
        if entry.get("type") == "blob" and entry.get("path", "").endswith((".yaml", ".yml"))
    ]


def find_crd_git_path(
    repo_name: str,
    kind: str,
    name: str,
    branch: str = MASTER_BRANCH,
) -> str | None:
    """Return the path of the file that defines ``kind``/``name``, or None.

    Naming conventions describe the files this code *writes*. They say nothing
    about a repo seeded from somewhere else: ``VyOSUnderlay/l3vpn-underlay``
    lives in ``transport/underlay.yaml``, which no convention would guess.
    Anything that needs to *modify or delete* an existing descriptor has to find
    it by reading the repo, so it edits the file that genuinely holds the
    resource instead of creating a second one Config Sync will fight over.

    Tries the ``<kind>-<name>.yaml`` convention first, since that is the common
    case and costs one comparison, then scans every YAML for a document whose
    kind and metadata.name match. Multi-document files are searched too.
    """
    conventional = f"{kind.lower()}-{name}.yaml"
    paths = list_git_yaml_paths(repo_name, branch)
    if conventional in paths:
        return conventional

    for path in paths:
        content = get_git_file(repo_name, path, branch=branch)
        if not content:
            continue
        try:
            documents = yaml.safe_load_all(content)
            for document in documents:
                if (isinstance(document, dict)
                        and document.get("kind") == kind
                        and (document.get("metadata") or {}).get("name") == name):
                    logger.info(
                        "find_crd_git_path: %s/%s lives at '%s', not the conventional '%s'",
                        kind, name, path, conventional
                    )
                    return path
        except Exception as e:
            logger.debug("find_crd_git_path: skipping '%s' (%s)", path, e)
            continue

    logger.warning(
        "find_crd_git_path: nothing in '%s' on '%s' defines %s/%s (scanned %d YAML paths)",
        repo_name, branch, kind, name, len(paths)
    )
    return None


def get_git_file(repo_name: str, filename: str, branch: str | None = None) -> str | None:
    """
    Retrieve a file from a Gitea repository by name.

    Args:
        repo_name: Repository name (e.g., "network", "networkdesign")
        filename: Path to the file in the repo. Nested paths are supported
            (e.g., "config.yaml", "knowledge/designdoc.md").
        branch: Branch to read from. ``None`` means the repo's default branch.
            Pass it when reading a review branch such as ``update``.

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
        # No Repository.request() here: it only existed to fail early on a
        # missing repo, which the contents call below already reports as a 404.
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
    except Exception as e:
        _log_http_error(logger, f"get_git_file: connect to Gitea at '{url}'", e)
        logger.debug("Full exception:", exc_info=True)
        return None

    try:
        # Hit the contents API by path rather than listing the repo root and
        # matching on ``c.name``: that only ever saw top-level files, so any
        # nested path was reported as missing.
        url_full = gitea.local_get_url(f"/repos/{USER}/{repo_name}/contents/{filename}")
        if branch:
            url_full += f"?ref={branch}"
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
        # A directory comes back as a list of entries, not a file object.
        if isinstance(data, list):
            logger.error(
                "get_git_file: '%s' is a directory in repo '%s', not a file",
                filename, repo_name
            )
            return None
        if 'content' not in data:
            logger.error(
                "get_git_file: response for '%s' has no 'content' field: %s",
                filename, list(data.keys())
            )
            return None

        # Gitea wraps the base64 payload at a fixed width.
        decoded = base64.b64decode(data['content'].replace('\n', '')).decode("utf-8")
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
        branch: Branch name (default: 'main')

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

    b64_content = base64.b64encode(bytes(content, "utf-8"))
    logger.debug(
        "commit_git_file: encoded content size: %d bytes raw -> %d bytes base64 (%.1f%% overhead)",
        len(content), len(b64_content), (len(b64_content) / len(content) - 1) * 100
    )

    for attempt in range(_MAX_COMMIT_ATTEMPTS):
        try:
            # Re-open the repo on every attempt so a cold start or a transient
            # 404 on the repo itself is recovered on the next try.
            gitea = Gitea(url, auth=(USER, PWD), verify=False)
            repo = Repository.request(gitea, USER, repo_name)

            _ensure_branch(gitea, repo_name, branch)

            # Look for the file on the branch being written to, not on the
            # default one. A review branch such as 'update' is cut from main
            # and then written to repeatedly -- a second run against a branch
            # that has not been merged yet finds its own earlier commit there,
            # and must change_file rather than create_file. (This used to
            # unconditionally create on non-master branches, on the assumption
            # that they are always empty, and 422'd on every repeat run.)
            if (file := git_file_exists(repo, file_path, branch=branch)) is not None:
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
            if attempt < _MAX_COMMIT_ATTEMPTS - 1:
                delay = _COMMIT_RETRY_DELAYS[attempt]
                logger.warning(
                    "commit_git_file: attempt %d/%d failed for '%s' in '%s', retrying in %.1fs: %s",
                    attempt + 1, _MAX_COMMIT_ATTEMPTS, file_path, repo_name, delay, e,
                )
                time.sleep(delay)
            else:
                _log_http_error(
                    logger,
                    f"commit_git_file('{file_path}' to '{repo_name}' on '{branch}')",
                    e
                )
                logger.error(
                    "commit_git_file: all %d attempts exhausted for '%s' | Message: '%s' | Content preview: %s",
                    _MAX_COMMIT_ATTEMPTS, file_path, message,
                    content[:200] + '...' if len(content) > 200 else content,
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
        branch: Branch name (default: 'main')

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
        # On the branch being deleted from, for the same reason commit_git_file
        # looks there: the file may exist on 'update' and not on 'main'.
        if file := git_file_exists(repo, file_path, branch=branch):
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

    # Re-fetch the repo so we have fresh blob SHAs, then make one more pass at
    # whatever failed -- the usual cause is a transient 404 from the ref-lock
    # window that opens on rapid sequential commits.
    if failed:
        logger.warning(
            "delete_all_git_files: %d deletion(s) failed on first pass - retrying after delay: %s",
            len(failed), failed,
        )
        time.sleep(_DELETE_RETRY_DELAY)
        try:
            retry_map = {f.name: f for f in (repo.get_git_content() or [])}
        except Exception as e:
            logger.error(
                "delete_all_git_files: failed to re-fetch repo contents for retry: %s", e
            )
            retry_map = None

        if retry_map is not None:
            for name in list(failed):
                file = retry_map.get(name)
                if file is None:
                    # Gone from the repo, so the first pass did land after all.
                    failed.remove(name)
                    deleted.append(name)
                    logger.debug("delete_all_git_files: '%s' already gone on retry", name)
                    continue
                try:
                    repo.delete_file(
                        file.name, file.sha,
                        data={'branch': MASTER_BRANCH, 'message': message},
                    )
                    failed.remove(file.name)
                    deleted.append(file.name)
                    logger.info("delete_all_git_files: deleted '%s' on retry", file.name)
                except Exception as e:
                    _log_http_error(
                        logger,
                        f"delete_all_git_files: retry delete '{file.name}' from '{repo_name}'",
                        e,
                    )

    logger.info(
        "delete_all_git_files: repo '%s' cleanup complete - deleted: %d, failed: %d",
        repo_name, len(deleted), len(failed)
    )
    return deleted, failed
