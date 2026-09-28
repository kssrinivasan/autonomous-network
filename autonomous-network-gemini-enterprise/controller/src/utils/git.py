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

"""
Git / Gitea utilities for the network operator.

This module provides utilities for reading and writing Kubernetes CRD YAML files
to the Gitea 'network' repository, which is synced to the cluster by ConfigSync.

Key functions:
  - crd_to_git_filename(kind, name) — the naming convention for CRs this code
    *creates*. It is not a way to locate a CR that is already in the repo; see
    find_crd_git_path() for that.
  - find_crd_git_path(repo, kind, name) — locate an existing CR's file wherever
    it actually lives, by reading what is in the repo
  - get_gitea_url() — read GITEA_URL, else discover Gitea's reserved address
  - get_git_file(repo, filename) — read a file from a Gitea repo, nested paths
    included
  - commit_git_file(file_path, message, content, repo_name) — create/update file

Git filename naming convention (what crd_to_git_filename() produces):
  - Lowercase the kind
  - Append hyphen and the resource name
  - Add .yaml extension

Examples:
  - VyOSInfrastructure/infra-blue → vyosinfrastructure-infra-blue.yaml
  - VyOSUnderlay/underlay-blue → vyosunderlay-underlay-blue.yaml
  - VyOSL3VPN/l3vpn-blue → vyosl3vpn-l3vpn-blue.yaml
  - NetworkFailure/link-down-pe1 → networkfailure-link-down-pe1.yaml

**The convention describes new files, not the whole repo.** A repo seeded from
examples/ holds the fixtures under their own names and directories —
`transport/underlay.yaml` carries VyOSUnderlay/l3vpn-underlay, which this
convention would call `vyosunderlay-l3vpn-underlay.yaml`. Anything that needs to
*modify* an existing CR must therefore find it by content rather than by
computing a filename and hoping. That assumption is what made every git-mode
fault injection fail on a seeded network.
"""

import os
import base64
import logging
import json
import google.auth
import kubernetes
import requests
import yaml as yaml_lib
from types import SimpleNamespace
from google.auth.transport.requests import Request
from gitea import Gitea, Repository

logger = logging.getLogger(__name__)

# Gitea credentials
USER = os.environ['WEBAPPS_LOGIN']
PWD = os.environ['WEBAPPS_PWD']

# The branch every write lands on. services/gitea/seed.sh creates each repo with
# default_branch 'main' and pushes HEAD:main -- a seeded Gitea has no 'master'
# ref at all, so the previous hardcoded 'master' 404'd on every commit.
MASTER_BRANCH = os.environ.get('GITEA_BASE_BRANCH', 'main')

def crd_to_git_filename(kind: str, name: str) -> str:
    """
    Convert a k8s CRD kind and name to a git filename.

    **THIS IS THE SINGLE SOURCE OF TRUTH FOR GIT FILENAME MAPPING.**
    All code that writes CRDs to git MUST use this function to ensure
    consistent naming across agents, operators, and network load/save functionality.

    Naming convention:
    - Lowercase the kind
    - Append hyphen and the resource name
    - Add .yaml extension

    Args:
        kind: k8s CRD kind (e.g., "VyOSInfrastructure", "VyOSUnderlay", "VyOSL3VPN")
        name: k8s resource name (e.g., "infra-blue", "underlay-blue", "l3vpn-blue")

    Returns:
        Git filename (e.g., "vyosinfrastructure-infra-blue.yaml")

    Examples:
        >>> crd_to_git_filename("VyOSInfrastructure", "infra-blue")
        'vyosinfrastructure-infra-blue.yaml'
        >>> crd_to_git_filename("VyOSUnderlay", "underlay-blue")
        'vyosunderlay-underlay-blue.yaml'
        >>> crd_to_git_filename("VyOSL3VPN", "l3vpn-blue")
        'vyosl3vpn-l3vpn-blue.yaml'
        >>> crd_to_git_filename("NetworkFailure", "link-down-pe1")
        'networkfailure-link-down-pe1.yaml'
    """
    return f"{kind.lower()}-{name}.yaml"


def get_gitea_url():
    """
    Get the base URL of the Gitea server.

    Reads GITEA_URL if set, otherwise asks the Compute API for the reserved
    regional address `gitea-ip` that Gitea's load balancer is pinned to
    (services/gitea/deployment.tpl.yaml). There is no hostname -- the address is
    the identity. Gitea moved off Cloud Run in migration plan 7.2 so its SQLite
    database and repositories could live on a persistent disk; before that this
    asked the Cloud Run Admin API.

    This must not shell out to gcloud: the deployed controller image is
    python:3.13.2 with no gcloud in it, so a subprocess raises FileNotFoundError
    and every GitOps write from networkfailure/operator_injection.py fails.

    The controller runs in-cluster, so it could reach Gitea over the ClusterIP
    service instead. It deliberately does not: the self-signed certificate
    covers both names, but using one URL everywhere keeps the GitOps commit
    URLs identical to what the agents and RootSync see.

    Returns:
        str: Gitea URL (e.g., "https://34.156.121.236")

    Raises:
        Exception if the URL cannot be determined.
    """
    url = os.environ.get('GITEA_URL', '')
    if url:
        return url.rstrip('/')

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
    if not ip:
        raise Exception(f"Reserved address '{address}' has no IP yet")
    discovered = f"https://{ip}"
    logger.debug(f"Gitea URL: {discovered}")
    return discovered


def get_git_file(repo: str, filename: str) -> str:
    """
    Retrieve a file from a Gitea repository by path.

    Args:
        repo: Repository name (e.g., "network", "networkdesign")
        filename: Path in the repo. Nested paths are supported
            (e.g., "transport/underlay.yaml").

    Returns:
        str: File contents as a UTF-8 string

    Raises:
        FileNotFoundError: If the path does not exist in the repo
        Exception: On any other Gitea API error
    """
    url = get_gitea_url()
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    try:
        # Ask the contents API for the path directly. Listing the repo root and
        # matching on ``c.name`` -- what this used to do -- only ever saw
        # top-level files, so every nested path came back "not found". Same bug
        # the shared agent_library copy already fixed.
        api = gitea.local_get_url(f"/repos/{USER}/{repo}/contents/{filename}")
        resp = requests.get(api, auth=(USER, PWD), verify=False, timeout=30)
        if resp.status_code == 404:
            raise FileNotFoundError(f"{filename} not found in repo {repo}")
        resp.raise_for_status()
        return base64.b64decode(resp.json()["content"]).decode("utf-8")
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Failed to read {filename} from {repo}: {e}")
        raise


def list_git_yaml_paths(repo: str, branch: str = None) -> list:
    """
    List every YAML path in a repo, at any depth.

    Args:
        repo: Repository name
        branch: Branch to read (defaults to MASTER_BRANCH)

    Returns:
        list[str]: Repo-relative paths of every .yaml/.yml blob, or [] on error
    """
    url = get_gitea_url()
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    ref = branch or MASTER_BRANCH
    try:
        api = gitea.local_get_url(f"/repos/{USER}/{repo}/git/trees/{ref}?recursive=true")
        resp = requests.get(api, auth=(USER, PWD), verify=False, timeout=30)
        resp.raise_for_status()
        return [
            e["path"] for e in resp.json().get("tree", [])
            if e.get("type") == "blob" and e.get("path", "").endswith((".yaml", ".yml"))
        ]
    except Exception as e:
        logger.error(f"Failed to list YAML paths in {repo}: {e}")
        return []


def find_crd_git_path(repo: str, kind: str, name: str, branch: str = None) -> str:
    """
    Find the file in a repo that defines a given CR, by reading the repo.

    Tries the crd_to_git_filename() convention first, since that is what this
    code creates, then falls back to scanning every YAML in the repo for a
    document whose kind and metadata.name match. Multi-document files are
    supported — the path is what is returned, so the caller edits the file that
    genuinely holds the resource.

    Args:
        repo: Repository name
        kind: CR kind (e.g., "VyOSUnderlay")
        name: Resource name (e.g., "l3vpn-underlay")
        branch: Branch to read (defaults to MASTER_BRANCH)

    Returns:
        str: Repo-relative path, or None if no file defines this CR
    """
    conventional = crd_to_git_filename(kind, name)
    paths = list_git_yaml_paths(repo, branch)
    if conventional in paths:
        return conventional

    for path in paths:
        try:
            docs = yaml_lib.safe_load_all(get_git_file(repo, path))
            for doc in docs:
                if (isinstance(doc, dict)
                        and doc.get("kind") == kind
                        and (doc.get("metadata") or {}).get("name") == name):
                    logger.info(
                        "find_crd_git_path: %s/%s lives at %s, not the conventional %s",
                        kind, name, path, conventional,
                    )
                    return path
        except Exception as e:
            logger.debug("find_crd_git_path: skipping %s (%s)", path, e)
            continue

    logger.error(
        "find_crd_git_path: no file in %s defines %s/%s (scanned %d YAML paths)",
        repo, kind, name, len(paths),
    )
    return None


# Extend the Gitea SDK with missing methods (same pattern as supervisor git_utils.py)

def _local_get_url(self, endpoint: str) -> str:
    """Build a full Gitea API URL."""
    url = self.url + "/api/v1" + endpoint
    self.logger.debug("Url: %s", url)
    return url


def _requests_delete_with_data(self, endpoint: str, data: dict = None):
    """Issue an HTTP DELETE request with a JSON body."""
    if not data:
        data = {}
    request = self.requests.delete(
        self.local_get_url(endpoint), headers=self.headers, data=json.dumps(data)
    )
    if request.status_code not in [200, 204]:
        message = f"Received status code: {request.status_code} ({request.url})"
        self.logger.error(message)
        raise Exception(message)


def _delete_file(self, file_path: str, file_sha: str, data: dict = None):
    """Delete a file from the repository via the Gitea contents API."""
    if not data:
        data = {}
    url = f"/repos/{self.owner.username}/{self.name}/contents/{file_path}"
    data.update({"sha": file_sha})
    return self.gitea.requests_delete_with_data(url, data)


# Patch the upstream classes once at import time
Gitea.local_get_url = _local_get_url
Gitea.requests_delete_with_data = _requests_delete_with_data
Repository.delete_file = _delete_file


def git_file_exists(repo_obj: Repository, file_path: str):
    """
    Check if a file exists in the repository, at any depth.

    Args:
        repo_obj: Gitea Repository object
        file_path: File path in the repo, nested paths included

    Returns:
        File object if exists, None otherwise
    """
    # This used to list get_git_content() and match on ``c.name``. That endpoint
    # returns the repo *root* only, so a nested path always looked absent and
    # commit_git_file() took the "create" branch on a file that already exists --
    # a 422, not an update. py-gitea 0.3.2's get_git_content() takes a commit,
    # not a directory, so ask the contents API for the path itself.
    try:
        data = repo_obj.gitea.requests_get(
            f"/repos/{USER}/{repo_obj.name}/contents/{file_path}"
        )
    except Exception as e:
        logger.debug(f"git_file_exists: '{file_path}' not readable: {e}")
        return None
    if not isinstance(data, dict) or data.get("type") != "file":
        return None
    return SimpleNamespace(
        path=data.get("path", file_path),
        name=data.get("name", os.path.basename(file_path)),
        sha=data.get("sha"),
    )


def commit_git_file(file_path: str, message: str, content: str, repo_name: str = 'network', branch: str = MASTER_BRANCH) -> bool:
    """
    Commit a file to a Gitea repository (create if new, update if exists).

    Args:
        file_path: File path in the repo (e.g., "vyosinfrastructure-infra-blue.yaml")
        message: Commit message
        content: File content (plain text, will be base64-encoded internally)
        repo_name: Repository name (default: "network")
        branch: Branch name (default: 'main')

    Returns:
        bool: True if successful, False otherwise
    """
    url = get_gitea_url()
    logger.info(f"Committing {file_path} to {repo_name} repo at {url}")
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    repo = Repository.request(gitea, USER, repo_name)

    # Base64 encode file content
    b64_content = base64.b64encode(bytes(content, "utf-8")).decode("ascii")

    try:
        if (file := git_file_exists(repo, file_path)) is not None:
            logger.debug(f"Updating git file: {file.path}, sha: {file.sha}")
            repo.change_file(
                file.path, file.sha,
                content=b64_content,
                data={'branch': branch, 'message': f"{message} - Updated"}
            )
        else:
            logger.debug(f"Creating git file: {file_path}")
            repo.create_file(
                file_path,
                content=b64_content,
                data={'branch': branch, 'message': message}
            )
        logger.info(f"Successfully committed {file_path} to {repo_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to commit {file_path} to {repo_name}: {e}")
        return False
