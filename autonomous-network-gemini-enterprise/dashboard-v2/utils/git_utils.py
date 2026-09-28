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

"""Turning a network descriptor into commits in the GitOps repository.

The Gitea plumbing -- SDK patches, URL discovery, and the CRUD helpers
``get_repo()`` / ``git_file_exists()`` / ``commit_git_file()`` /
``delete_git_file()`` / ``delete_all_git_files()`` -- lives in
``agent_library.gitea_utils`` and is imported below. The Dockerfile pip-installs
``lib/`` for that reason. This module used to carry its own copy of all of it.

What is left here is the part that is specific to the dashboard:
``deploy_descriptor_to_git()`` converts a full network descriptor to YAML and
commits each CRD to the Gitea ``network`` repository so Config Sync can
reconcile the cluster state.

Deploy order: infrastructure → underlay → vpns → traffic_tests

Teardown deletes *every* file currently in the ``network`` repo, ensuring a
true clean slate regardless of leftover files from previous deployments.
"""

import logging
import yaml

from agent_library import (
    get_gitea_url,
    get_repo,
    git_file_exists,
    commit_git_file,
    delete_git_file,
    delete_all_git_files,
    USER,
    PWD,
    MASTER_BRANCH,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVICE_REPO = 'core'
NETWORK_REPO = 'network'

# ---------------------------------------------------------------------------
# Deployment helpers  (previously in git_deploy.py)
# ---------------------------------------------------------------------------

def _cr_filename(body: dict) -> str:
    """
    Return git filename for a CRD body dict using the standard naming convention.

    Naming convention: {kind-lowercase}-{name}.yaml

    Examples:
        VyOSInfrastructure/l3vpn-infra → vyosinfrastructure-l3vpn-infra.yaml
        VyOSUnderlay/l3vpn-underlay → vyosunderlay-l3vpn-underlay.yaml
        VyOSL3VPN/l3vpn-blue → vyosl3vpn-l3vpn-blue.yaml

    This convention must match the operator's crd_to_git_filename() function
    in operator/src/utils/git.py.
    """
    kind = body.get("kind", "unknown")
    name = body.get("metadata", {}).get("name", "unknown")
    return f"{kind.lower()}-{name}.yaml"


def _ordered_resources(descriptor: dict) -> list[tuple[dict, str]]:
    """Return an ordered list of ``(crd_body, label)`` tuples from a descriptor.

    Deploy order: infrastructure → underlay → vpns → traffic_tests
    """
    resources: list[tuple[dict, str]] = []
    if descriptor.get("infrastructure"):
        resources.append((descriptor["infrastructure"], "infrastructure"))
    if descriptor.get("underlay"):
        resources.append((descriptor["underlay"], "underlay"))
    for vpn in descriptor.get("vpns", []):
        resources.append((vpn, "vpn"))
    for test in descriptor.get("traffic_tests", []):
        resources.append((test, "traffic_test"))
    return resources


async def deploy_descriptor_to_git(
    descriptor: dict,
    *,
    sio=None,
    network_id: str = "",
) -> dict:
    """Convert every CRD in *descriptor* to YAML and commit it to the Gitea
    ``network`` repository.

    Config Sync will reconcile the cluster state to match the committed files.

    Emits ``deploy_progress`` Socket.IO events for each committed file so
    the dashboard can show per-resource progress.

    Args:
        descriptor: Full network descriptor dict (infrastructure, underlay,
                    vpns, traffic_tests).
        sio:        Optional Socket.IO server instance for progress events.
        network_id: Forwarded to every progress event.

    Returns:
        dict with keys ``committed`` (list[str]) and ``failed`` (list[str]).
    """
    committed: list[str] = []
    failed:    list[str] = []

    async def _emit(stage: str, **extra) -> None:
        if sio is None:
            return
        try:
            await sio.emit("deploy_progress", {"network_id": network_id, "stage": stage, **extra})
        except Exception as e:
            logger.warning("Failed to emit deploy_progress: %s", e)

    for body, label in _ordered_resources(descriptor):
        kind      = body.get("kind", "Unknown")
        name      = body.get("metadata", {}).get("name", "unknown")
        file_path = _cr_filename(body)
        message   = f"Deploy {kind}/{name} for network {network_id}"
        cr_yaml   = yaml.dump(body, indent=2, allow_unicode=True, default_flow_style=False)

        logger.debug("Committing %s/%s as '%s' to '%s'", kind, name, file_path, NETWORK_REPO)
        ok = commit_git_file(file_path, message, cr_yaml, repo_name=NETWORK_REPO)
        if ok:
            committed.append(f"{kind}/{name}")
            await _emit("git_committed", kind=kind, name=name, file=file_path)
        else:
            failed.append(f"{kind}/{name}")
            await _emit("git_commit_failed", kind=kind, name=name, file=file_path)

    logger.debug(
        "Git deploy of '%s' complete — committed: %d, failed: %d | %s | %s",
        network_id, len(committed), len(failed), committed, failed,
    )
    return {"committed": committed, "failed": failed}
