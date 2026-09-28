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
import os
import google.auth
from google.adk.apps import App
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from .app_utils.prompts import root_prompt
from .app_utils.descriptions import description
from .app_utils.docs import getFailureAnalysis
from .app_utils.faults import deleteFault, deploySpec, getDeployedFaults, getFaultDescriptors
from .app_utils.infra import getInfrastructure

# Set up project environment
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

logger = logging.getLogger(__name__)

# Bound to a module-level name (as the rest of the fleet does) so
# tests/integration/test_agent.py can drive it through a Runner without
# standing up the whole App.
root_agent = LlmAgent(
    name="ChaosAgent",
    description=description,
    model="gemini-3.5-flash",
    instruction=root_prompt,
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(
            thinking_level="low",
            include_thoughts=False
        )
    ),
    tools=[
        getFaultDescriptors,
        getDeployedFaults,
        deploySpec,
        deleteFault,
        getFailureAnalysis,
        getInfrastructure,
    ],
)

app = App(
    name="ChaosAgent",
    root_agent=root_agent,
)
