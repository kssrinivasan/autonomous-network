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

"""Deploying custom resources by committing them to the GitOps repo.

Nothing here talks to the Kubernetes API directly. The controller reconciles
from the ``network`` Gitea repo, so "deploy" means "commit a descriptor" and
"delete" means "remove the file". Writing to the cluster instead would be
undone on the next sync.

New descriptors are named ``<kind-lowercase>-<name>.yaml``, the convention
documented as the single source of truth in
``controller/src/utils/git.py::crd_to_git_filename``. A descriptor that already
exists is rewritten wherever it already lives instead: the convention describes
files this code creates, and a repo seeded from ``examples/`` keeps its CRs
under other names and in directories. Writing a second file for a resource that
already has one leaves Config Sync with two sources for one object.
"""

import json
import logging
import re
from typing import Annotated

import yaml
from mcp.types import ToolAnnotations

import utils.globals as globals
from utils.git_helpers import (
    SERVICE_REPO,
    commit_git_file,
    delete_git_file,
    find_crd_git_path,
)

logger = logging.getLogger(__name__)


def _represent_str(dumper, data):
    """Keep leading-zero numeric strings quoted so they survive a round trip."""
    if isinstance(data, str) and data.isdigit() and data.startswith('0'):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


yaml.add_representer(str, _represent_str)


def _strip_fences(text):
    """Remove a markdown code fence, which models routinely wrap YAML in."""
    text = text.strip()
    text = re.sub(r'^```[^\n]*\n', '', text)
    return re.sub(r'\n```\s*$', '', text)


def crd_to_git_filename(kind, name):
    """Mirror of controller/src/utils/git.py::crd_to_git_filename."""
    return f"{kind.lower()}-{name}.yaml"


def resolve_git_filename(kind, name):
    """Where this descriptor belongs: its existing file, else the convention."""
    return find_crd_git_path(SERVICE_REPO, kind, name) or crd_to_git_filename(kind, name)


def deploy_descriptor(parsed):
    """Commit one already-parsed descriptor. Returns a human-readable result."""
    name = (parsed.get("metadata") or {}).get("name")
    kind = parsed.get("kind")
    if not name:
        return "Error: descriptor is missing metadata.name"
    if not kind:
        return "Error: descriptor is missing kind"

    filename = resolve_git_filename(kind, name)
    cr_yaml = yaml.dump(parsed, indent=2, allow_unicode=True, default_flow_style=False)

    logger.info("Committing %s/%s as '%s'", kind, name, filename)
    if commit_git_file(filename, f"Deployment of {kind}/{name}", cr_yaml, SERVICE_REPO):
        return f"resource {filename} successfully submitted for deployment"

    logger.error("Failed to commit %s; descriptor was:\n%s", filename, cr_yaml)
    return f"resource {filename} error deploying - check logs for details"


@globals.mutating_tool()
def deployDescriptors(
    yaml_content: Annotated[str, "One or more Kubernetes custom resource descriptors as YAML, separated by ---"]
) -> str:
    """
    Deploy one or more network descriptors from a multi-document YAML string.

    Splits the YAML on '---' and submits every descriptor. Descriptors are
    independent: one that fails does not stop the rest, and every outcome is
    reported so nothing fails silently.

    Returns:
        A JSON object with 'total', 'successful', 'failed' and a 'results'
        array holding one message per descriptor.
    """
    try:
        documents = [d for d in yaml.safe_load_all(_strip_fences(yaml_content)) if d]
    except yaml.YAMLError as e:
        logger.error("deployDescriptors: YAML parse failed: %s", e)
        return json.dumps({"total": 0, "successful": 0, "failed": 1,
                           "results": [f"ERROR: failed to parse YAML - {e}"]})

    if not documents:
        return json.dumps({"total": 0, "successful": 0, "failed": 0,
                           "results": ["WARNING: no descriptors found in the YAML content"]})

    logger.info("deployDescriptors: processing %d descriptors", len(documents))

    results, successful, failed = [], 0, 0
    for idx, doc in enumerate(documents, 1):
        kind = doc.get("kind", "Unknown") if isinstance(doc, dict) else "Unknown"
        name = (doc.get("metadata") or {}).get("name", "Unknown") if isinstance(doc, dict) else "Unknown"
        try:
            if not isinstance(doc, dict):
                raise ValueError("descriptor is not a YAML mapping")
            outcome = deploy_descriptor(doc)
            ok = outcome.startswith("resource") and "successfully" in outcome
        except Exception as e:
            outcome, ok = f"ERROR: {e}", False
            logger.error("deployDescriptors: %s/%s failed", kind, name, exc_info=True)

        successful, failed = (successful + 1, failed) if ok else (successful, failed + 1)
        results.append(f"[{idx}/{len(documents)}] {kind}/{name}: {outcome}")

    logger.info("deployDescriptors: %d successful, %d failed of %d",
                successful, failed, len(documents))
    return json.dumps({"total": len(documents), "successful": successful,
                       "failed": failed, "results": results}, indent=2)


# mutating_tool, not networkagent_mcp.tool: tools/devices.py calls this
# in-process from delete_device, so it must stay a plain callable even where
# it is not registered.
@globals.mutating_tool()
def deleteDescriptor(
    kind: Annotated[str, "The kind of the resource to delete (e.g. 'VyOSRouter', 'Device')"],
    name: Annotated[str, "The name of the resource to delete"]
) -> str:
    """
    Delete a deployed custom resource by removing its descriptor from GitOps.

    Both kind and name are required: the filename is derived from them, and
    guessing the kind would risk deleting the wrong resource.

    Returns:
        A message stating whether the deletion was submitted.
    """
    filename = find_crd_git_path(SERVICE_REPO, kind, name)
    if not filename:
        return (f"resource {kind}/{name} not found in the {SERVICE_REPO} repo - "
                f"nothing to delete")
    logger.info("deleteDescriptor: removing '%s'", filename)
    if delete_git_file(filename, f"Deletion of {kind}/{name}", SERVICE_REPO):
        return f"resource {filename} successfully submitted for deletion"
    return f"resource {filename} could not be deleted - check logs for details"
