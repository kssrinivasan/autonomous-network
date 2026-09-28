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

"""Where the designer writes, and how it names what it writes.

The Gitea plumbing -- URL discovery, the ``py-gitea`` monkeypatches, the
commit/delete retry loops, branch creation -- lives in
``agent_library.gitea_utils`` and is shared with the MCP tool servers and
dashboard-v2. It used to be duplicated here, which is why the same Gitea fix
had to be applied in four or five places.

What is genuinely local to the designer, and stays: it does not commit to
``main``. Every write goes to ``TARGET_BRANCH`` so the proposal can be reviewed
before it reaches the branch Config Sync reconciles from. ``agent_library``
creates that branch off ``main`` on first write.
"""

import logging
import os

# Relative: deploy_agents.py vendors lib/src/agent_library into app/agent_library,
# and under agents-cli 1.3.1 the container root is /code with `app` as a package,
# so the library is reachable as app.agent_library -- not as a top-level module
# the way it was when 0.3.0 packaged app/ as the root.
from ..agent_library import (  # noqa: F401  -- re-exported for app_utils.design
    MASTER_BRANCH,
    PWD,
    USER,
    commit_git_file,
    delete_git_file,
    find_crd_git_path,
    get_git_file,
    get_gitea_url,
    git_file_exists,
    list_git_files,
)

logger = logging.getLogger(__name__)

if not USER or not PWD:
    raise ValueError(
        "Missing Gitea credentials! "
        "Please ensure WEBAPPS_LOGIN and WEBAPPS_PWD environment variables are set."
    )

#: Domain design documents, read-only.
DESIGN_REPO = 'networkdesign'

#: The GitOps repository Config Sync reconciles the cluster from.
SERVICE_REPO = 'network'

#: The review branch. Cut from MASTER_BRANCH on first write, never committed
#: to directly by anything else.
TARGET_BRANCH = os.environ.get('GITEA_TARGET_BRANCH', 'update')


def resolve_cr_filename(kind: str, name: str) -> str:
    """Return the filename to write the ``kind``/``name`` descriptor to.

    Order matters, and it is about not creating a second file for a resource
    that already has one -- Config Sync treats that as two sources managing one
    object:

    1. Whatever file already defines the resource, at any depth. A repo seeded
       from ``examples/`` keeps ``VyOSUnderlay/l3vpn-underlay`` in
       ``transport/underlay.yaml``, which no naming convention would guess.
    2. The legacy flat ``<name>.yaml``, for descriptors written before the
       ``<kind>-<name>.yaml`` convention.
    3. ``<kind>-<name>.yaml``, for a resource that is genuinely new.
    """
    preferred = f"{kind.lower()}-{name}.yaml"
    legacy = f"{name}.yaml"

    existing = find_crd_git_path(SERVICE_REPO, kind, name, branch=TARGET_BRANCH)
    if existing:
        return existing

    if legacy in set(list_git_files(SERVICE_REPO, branch=TARGET_BRANCH)):
        return legacy
    return preferred


def resolve_existing_cr_filename(name: str, kind: str | None = None) -> str | None:
    """Return the filename the descriptor for *name* is stored under, or None.

    Used for deletion, where guessing wrong means leaving the resource
    deployed. With *kind* known, find the file by reading the repo -- that also
    covers descriptors kept in directories. Without it, fall back to matching
    the legacy ``<name>.yaml`` and then any ``<kind>-<name>.yaml`` at the root.
    """
    if kind:
        found = find_crd_git_path(SERVICE_REPO, kind, name, branch=TARGET_BRANCH)
        if found:
            return found

    legacy = f"{name}.yaml"
    suffix = f"-{name}.yaml"

    names = list_git_files(SERVICE_REPO, branch=TARGET_BRANCH)
    if legacy in names:
        return legacy
    matches = [n for n in names if n.endswith(suffix)]
    if matches:
        return matches[0]

    logger.warning("resolve_existing_cr_filename: no descriptor found for '%s'", name)
    return None
