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

from .tmf921 import (
    Intent,
    IntentList,
    IntentExpression,
    L3ConnectivityExpectation,
    L3VPNService,
    VPNEndpoint,
    SLA,
    IntentState,
)

from .orchestrator_protocol import (
    CreateIntentRequest,
    CreateIntentResponse,
    GetIntentStatusRequest,
    IntentStatusResponse,
    DeleteIntentRequest,
    DeleteIntentResponse,
    OrchestratorError,
    OrchestratorValidationError,
    OrchestratorConflictError,
    OrchestratorDependencyError,
    OrchestratorCommunicationError,
)

__all__ = [
    # TMF921 models
    "Intent",
    "IntentList",
    "IntentExpression",
    "L3ConnectivityExpectation",
    "L3VPNService",
    "VPNEndpoint",
    "SLA",
    "IntentState",
    # Orchestrator protocol models
    "CreateIntentRequest",
    "CreateIntentResponse",
    "GetIntentStatusRequest",
    "IntentStatusResponse",
    "DeleteIntentRequest",
    "DeleteIntentResponse",
    # Orchestrator exceptions
    "OrchestratorError",
    "OrchestratorValidationError",
    "OrchestratorConflictError",
    "OrchestratorDependencyError",
    "OrchestratorCommunicationError",
]
