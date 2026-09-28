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

import logging
# Relative: deploy_agents.py vendors lib/src/agent_library into app/agent_library,
# and under agents-cli 1.3.1 the container root is /code with `app` as a package,
# so the library is reachable as app.agent_library -- not as a top-level module
# the way it was when 0.3.0 packaged app/ as the root.
from ..agent_library import get_git_file

DESIGN_REPO = 'networkdesign'

logger = logging.getLogger(__name__)


def getFailureAnalysis() -> str:
    """
    Retrieve the GNN failure analysis documentation from Gitea.

    This document describes all fault injection scenarios for the telco-lab
    L3VPN network, including:
    - The fault type, target router/interface, and what changes in the network
    - Which traffic flows are affected and how
    - The GNN detection approach and which GNN node features each fault exercises
    - The GNN value rating (only GNN can detect / GNN improves on traditional /
      GNN reduces alarm noise)
    - Comparison with traditional fault management tools

    Call this tool when the user asks:
    - "Which fault should I inject?" or "What test should I run?"
    - "What does fault N do?" or "Tell me about the MTU mismatch test"
    - "Which faults exercise the OSPF features?" or "What tests the tx_queue_len feature?"
    - "What is the network impact of injecting X?"
    - "Which faults are silent / undetectable by traditional tools?"

    Args:
        None

    Returns:
        str: The full failure analysis document as a markdown string, or an
             error message if the document could not be retrieved.
    """
    logger.info("Fetching failure-analysis.md from Gitea")
    filename = "failure-analysis.md"
    result = get_git_file(DESIGN_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found in Gitea repo '{DESIGN_REPO}'")
        return (
            f"Error: {filename} could not be retrieved from Gitea. "
            "The file may not have been uploaded to the networkdesign repository yet. "
            "Please upload docs/gnn/failure-analysis.md to the Gitea networkdesign repo "
            "as 'failure-analysis.md' and try again."
        )
