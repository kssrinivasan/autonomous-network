# Copyright 2024-2028 Google LLC
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

"""Transport Orchestrator Root Agent

This agent handles structured JSON requests via A2A protocol.

## Structured Data Support

The agent accepts requests with structured JSON data via A2A's `part.data` field
or legacy text-based JSON via `part.text`. The A2A server framework (DefaultRequestHandler
+ A2aAgentExecutor) handles message parsing and routing to the agent.

### Request Format (via part.data):
```json
{
  "intent": {
    "@type": "Intent",
    "name": "service-name",
    "intentExpression": { ... }
  },
  "namespace": "default"
}
```

### Response Format:
Responses are returned as JSON strings that the client parses from the agent's output.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from google.adk.agents import SequentialAgent
from google.adk.apps import App

from .subagents import (
    create_validate_intent_agent,
    create_planner_agent,
    create_designer_agent,
    create_deploy_crs_agent
)

# Note: .env is already loaded in config.py (executed during subagent imports above)
# This is kept for explicitness but won't override existing variables
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# Create the orchestration pipeline with all four subagents
root_agent = SequentialAgent(
    name="TransportOrchestrationAgent",
    description=(
        "Decomposes TMF921 L3ConnectivityIntent into VyOS Kubernetes CRs "
    ),
    sub_agents=[
        create_validate_intent_agent(),
        create_planner_agent(),
        create_designer_agent(),
        create_deploy_crs_agent(),
    ],
)

# Only load DevLoggerPlugin if DEBUG=true
plugins = []
if os.getenv("DEBUG", "").lower() in ("true", "1", "yes"):
    from app.app_utils.logging_plugin import DevLoggerPlugin
    plugins.append(DevLoggerPlugin())

app = App(
    root_agent=root_agent,
    name="app",
    plugins=plugins,
)
