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

import logging
import re
from utils.gitea_utils import commit_git_file, delete_git_file
import yaml
from mcp.types import ToolAnnotations
import utils.globals as globals

# Repository constants
SERVICE_REPO = 'network'
UPDATE_BRANCH = 'update'

logger = logging.getLogger(__name__)

# Custom YAML representer to preserve quotes for numeric strings
def represent_str(dumper, data):
    """
    Custom string representer that preserves quotes for numeric strings
    that should remain as strings (e.g., phone numbers, IDs starting with 0)
    """
    # Preserve quotes for numeric strings that start with 0 or other patterns
    # that should remain as strings
    if isinstance(data, str) and data.isdigit() and data.startswith('0'):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

# Register the custom representer
yaml.add_representer(str, represent_str)

def deployDescriptor(descriptor: str):
    """
    write a new/updated custom resource descriptor to git

    Args:
        descriptor: kubernetes custom resource descriptor as a YAML string
    Returns:
        success or failure message
    """
    logger.info("Deploying descriptor (length: %d bytes)", len(descriptor))

    # Strip markdown code fences if the LLM wrapped the YAML (e.g. ```yaml ... ```)
    descriptor = descriptor.strip()
    descriptor = re.sub(r'^```[^\n]*\n', '', descriptor)
    descriptor = re.sub(r'\n```\s*$', '', descriptor)

    try:
        parsed = yaml.safe_load(descriptor)
    except yaml.YAMLError as e:
        logger.error("deployDescriptor: YAML parsing failed: %s | Input:\n%s", e, descriptor[:500])
        return f"Error: could not parse descriptor YAML: {e}"

    if not parsed or not isinstance(parsed, dict):
        logger.error("deployDescriptor: parsed YAML is not a dict: %s", type(parsed))
        return "Error: could not parse descriptor YAML - result is not a dictionary"

    name = parsed.get("metadata", {}).get("name")
    kind = parsed.get("kind")
    if not name:
        logger.error("deployDescriptor: descriptor missing metadata.name | Parsed: %s", parsed)
        return "Error: descriptor is missing metadata.name"
    if not kind:
        logger.error("deployDescriptor: descriptor missing kind | Parsed: %s", parsed)
        return "Error: descriptor is missing kind"

    cr_yaml = yaml.dump(parsed, indent=2, allow_unicode=True, default_flow_style=False)
    # Use standard naming convention: {kind-lowercase}-{name}.yaml
    # This must match operator/src/utils/git.py::crd_to_git_filename()
    filename = f"{kind.lower()}-{name}.yaml"

    logger.info(
        "deployDescriptor: committing %s/%s as '%s' to repo '%s' on branch '%s'",
        kind, name, filename, SERVICE_REPO, UPDATE_BRANCH
    )

    result = commit_git_file(
        filename,
        f"Deployment of {kind}/{name}",
        cr_yaml,
        repo_name=SERVICE_REPO,
        branch=UPDATE_BRANCH
    )

    if result:
        logger.info("deployDescriptor: resource %s successfully submitted for deployment", filename)
        return f"resource {filename} successfully submitted for deployment"
    else:
        logger.error(
            "deployDescriptor: resource %s failed to deploy (see git error logs above)",
            filename
        )
        logger.debug("Failed descriptor YAML:\n%s", cr_yaml)
        return f"resource {filename} error deploying - check logs for details"

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def deployDescriptors(yaml_content: str) -> dict:
    """
    Deploy multiple network descriptors from a multi-document YAML string.

    Handles splitting and deploying all descriptors in a single tool call.
    All errors are captured and returned in the results array for user visibility.

    Args:
        yaml_content: Complete YAML string with --- separators between descriptors

    Returns:
        {
            "total": int,
            "successful": int,
            "failed": int,
            "results": ["resource X successfully...", "ERROR: resource Y failed - reason..."]
        }
    """
    logger.info("Deploy descriptors")

    results = []
    successful = 0
    failed = 0

    # Strip markdown code fences if present
    yaml_content = yaml_content.strip()
    yaml_content = re.sub(r'^```[^\n]*\n', '', yaml_content)
    yaml_content = re.sub(r'\n```\s*$', '', yaml_content)

    # Parse multi-document YAML - yaml.safe_load_all returns generator
    try:
        documents = list(yaml.safe_load_all(yaml_content))
    except yaml.YAMLError as e:
        error_msg = f"ERROR: Failed to parse YAML - {str(e)}"
        logger.error("deployDescriptors: %s", error_msg)
        return {
            "total": 0,
            "successful": 0,
            "failed": 1,
            "results": [error_msg]
        }

    # Filter out None/empty documents
    documents = [doc for doc in documents if doc]

    if not documents:
        warning_msg = "WARNING: No descriptors found in YAML content"
        logger.warning("deployDescriptors: %s", warning_msg)
        return {
            "total": 0,
            "successful": 0,
            "failed": 0,
            "results": [warning_msg]
        }

    logger.info("deployDescriptors: processing %d descriptors", len(documents))

    # Deploy each document - continue on errors to deploy all possible descriptors
    for idx, doc in enumerate(documents, 1):
        try:
            # Get descriptor info for better error messages
            kind = doc.get("kind", "Unknown")
            name = doc.get("metadata", {}).get("name", "Unknown")

            # Serialize back to YAML string for deployDescriptor
            descriptor_yaml = yaml.dump(doc, indent=2, allow_unicode=True, default_flow_style=False)
            result = deployDescriptor(descriptor_yaml)  # Reuse existing logic

            # Enhance result message with descriptor info for clarity
            enhanced_result = f"[{idx}/{len(documents)}] {kind}/{name}: {result}"
            results.append(enhanced_result)

            if "successfully" in result.lower():
                successful += 1
                logger.info("deployDescriptors: ✓ %s", enhanced_result)
            else:
                failed += 1
                logger.error("deployDescriptors: ✗ %s", enhanced_result)

        except Exception as e:
            # Catch any unexpected errors during deployment
            error_msg = f"[{idx}/{len(documents)}] ERROR: Unexpected error deploying descriptor - {str(e)}"
            results.append(error_msg)
            failed += 1
            logger.error("deployDescriptors: %s", error_msg, exc_info=True)

    logger.info(
        "deployDescriptors: completed - %d successful, %d failed out of %d total",
        successful, failed, len(documents)
    )

    return {
        "total": len(documents),
        "successful": successful,
        "failed": failed,
        "results": results
    }


def deleteDescriptor(name: str) -> str:
    """
    Delete a Custom Resource from the cluster.

    Args:
        name (str): The name of the resource

    Returns:
        str: Success or error message
    """
    logger.info("deleteDescriptor: deleting resource '%s'", name)

    filename = f"{name}.yaml"
    result = delete_git_file(filename, f"{name} deletion", repo_name=SERVICE_REPO)
    if result:
        logger.info("deleteDescriptor: resource %s successfully submitted for deletion", filename)
        return f"service {name} successfully submitted for deletion"
    else:
        logger.error("deleteDescriptor: resource %s could not be deleted (see git error logs above)", filename)
        return f"service {name} could not be deleted - check logs for details"
