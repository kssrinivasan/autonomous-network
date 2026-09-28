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

import json
import logging
import re
import kubernetes
import yaml
from .git import (
    get_git_file,
    commit_git_file,
    delete_git_file,
    resolve_cr_filename,
    resolve_existing_cr_filename,
    DESIGN_REPO,
    SERVICE_REPO,
    TARGET_BRANCH,
)
from .k8s import get_client

logger = logging.getLogger(__name__)

def represent_str(dumper, data):
    if isinstance(data, str) and data.isdigit() and data.startswith('0'):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

yaml.add_representer(str, represent_str)

def getDesignDoc() -> str:
    """
    Retrieve the Vyos network design documentation from git.
    """
    logger.info("Getting network design from git")
    filename = "designdoc.md"
    result = get_git_file(DESIGN_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found")
        return "Design documentation not found."

def validate_descriptor(parsed: dict):
    api_version = parsed.get("apiVersion")
    if not api_version:
        return None
    try:
        dyn = kubernetes.dynamic.DynamicClient(get_client())
        res = dyn.resources.get(api_version=api_version, kind=parsed.get("kind"))
        dyn.server_side_apply(
            res,
            body=parsed,
            namespace=parsed.get("metadata", {}).get("namespace", "network"),
            field_manager="designer-agent-validate",
            force_conflicts=True,
            dry_run="All",
        )
        return None
    except kubernetes.client.exceptions.ApiException as e:
        if e.status and 400 <= e.status < 500:
            message = str(e.body)
            try:
                message = json.loads(e.body).get("message", message)
            except Exception:
                pass
            return message
        return None
    except Exception:
        return None

def deployDescriptor(descriptor: str) -> str:
    """
    Write a new/updated custom resource descriptor YAML string to git.
    """
    logger.info("Deploying descriptor:\n%s", descriptor)

    descriptor = descriptor.strip()
    if '\\n' in descriptor:
        descriptor = descriptor.replace('\\n', '\n')
    descriptor = re.sub(r'^```[^\n]*\n', '', descriptor)
    descriptor = re.sub(r'\n```\s*$', '', descriptor)

    try:
        parsed = yaml.safe_load(descriptor)
    except Exception as e:
        return f"Error: could not parse descriptor YAML: {e}"

    if not parsed or not isinstance(parsed, dict):
        return "Error: could not parse descriptor YAML into a dictionary"

    name = parsed.get("metadata", {}).get("name")
    kind = parsed.get("kind")
    if not name:
        return "Error: descriptor is missing metadata.name"
    if not kind:
        return "Error: descriptor is missing kind"

    validation_error = validate_descriptor(parsed)
    if validation_error is not None:
        return f"Resource {kind}/{name} schema validation failed (not committed): {validation_error}"

    cr_yaml = yaml.dump(parsed, indent=2, allow_unicode=True, default_flow_style=False)
    filename = resolve_cr_filename(kind, name)
    commit_msg = f"Deployment of {kind}/{name}"
    result = commit_git_file(filename, commit_msg, cr_yaml, SERVICE_REPO, branch=TARGET_BRANCH)
    if result:
        # Automatically trigger Google Chat notification card on GitOps commit
        try:
            from .notification import send_google_chat_notification
            send_google_chat_notification(
                summary=f"Automated deployment of Custom Resource {kind}/{name} committed to GitOps repository ({filename}).",
                title=f"GitOps Topology Change: {kind}/{name}",
                status="SUCCESS",
                resources_changed=[f"{kind}/{name}"],
                gitops_commit=commit_msg,
            )
        except Exception as notif_err:
            logger.warning("Auto-notification failed: %s", notif_err)
        return f"Resource {filename} successfully committed to GitOps for deployment."
    else:
        return f"Failed to commit resource {filename} to git."

def deleteDescriptor(name: str) -> str:
    """
    Delete a VyOS Custom Resource from GitOps by name.
    """
    logger.info(f"Deleting resource descriptor from git: {name}")
    filename = resolve_existing_cr_filename(name)
    if filename is None:
        return f"Resource {name} could not be deleted — no matching descriptor file found in git."
    commit_msg = f"{name} deletion"
    result = delete_git_file(filename, commit_msg, SERVICE_REPO, branch=TARGET_BRANCH)
    if result:
        try:
            from .notification import send_google_chat_notification
            send_google_chat_notification(
                summary=f"Automated deletion of Custom Resource {name} committed to GitOps repository ({filename}).",
                title=f"GitOps Resource Deleted: {name}",
                status="DELETED",
                resources_changed=[name],
                gitops_commit=commit_msg,
            )
        except Exception as notif_err:
            logger.warning("Auto-notification failed: %s", notif_err)
        return f"Resource {filename} successfully deleted from GitOps."
    else:
        return f"Failed to delete resource {filename} from git."

