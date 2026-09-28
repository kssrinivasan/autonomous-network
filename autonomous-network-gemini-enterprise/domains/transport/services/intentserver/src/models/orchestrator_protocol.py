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

"""A2A Protocol Models for Transport Orchestrator Communication"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from uuid import UUID
from .tmf921 import Intent


# ============================================================================
# Request Models
# ============================================================================

class CreateIntentRequest(BaseModel):
    """Request to create L3VPN service from TMF921 intent

    This request is sent from the intentserver to the transport orchestrator
    agent via A2A protocol to provision a new L3VPN service.

    Example:
        ```python
        request = CreateIntentRequest(
            intent=intent,  # TMF921 Intent envelope
        )
        response = await orchestrator_client.create_intent(request)
        ```
    """
    intent: Intent = Field(..., description="TMF921 Intent envelope with L3VPN service specification")
    intent_id: Optional[str] = Field(None, description="Intent UUID for Spanner state tracking by the orchestrator")

    @field_validator('intent')
    @classmethod
    def validate_intent_structure(cls, v: Intent) -> Intent:
        """Validate intent has required structure for orchestration"""
        if not v.intentExpression:
            raise ValueError("Intent must have intentExpression")

        if not v.intentExpression.expectation or len(v.intentExpression.expectation) == 0:
            raise ValueError("Intent must have at least one expectation")

        expectation_obj = v.intentExpression.expectation[0].expectationObject
        if not expectation_obj.serviceName:
            raise ValueError("L3VPN service must have serviceName")

        if not expectation_obj.endpoints or len(expectation_obj.endpoints) < 2:
            raise ValueError("L3VPN service must have at least 2 endpoints")

        return v


class GetIntentStatusRequest(BaseModel):
    """Request to get L3VPN service status

    Query the current provisioning status of an L3VPN service.

    Example:
        ```python
        request = GetIntentStatusRequest(
            service_name="red-mesh-vpn"
        )
        status = await orchestrator_client.get_intent_status(request)
        print(f"Phase: {status.phase}, Message: {status.message}")
        ```
    """
    service_name: str = Field(..., min_length=1, description="L3VPN service name")


class DeleteIntentRequest(BaseModel):
    """Request to delete L3VPN service

    Revoke an L3VPN intent and deprovision the service.

    Example:
        ```python
        request = DeleteIntentRequest(
            service_name="red-mesh-vpn"
        )
        response = await orchestrator_client.delete_intent(request)
        ```
    """
    service_name: str = Field(..., min_length=1, description="L3VPN service name")


# ============================================================================
# Response Models
# ============================================================================

class CreateIntentResponse(BaseModel):
    """Response from create intent operation

    The orchestrator returns this response after attempting to create an
    L3VPN service.

    Attributes:
        success: Whether the service was created successfully
        service_name: Name of the created L3VPN service (only if success=true)
        status: TMF921 intent state - Acknowledged if accepted, Rejected if failed
        error_code: HTTP-style error code if success=false (400, 409, 424, 500)
        message: Human-readable message explaining the result
    """
    success: bool = Field(..., description="Whether L3VPN service was created successfully")
    service_name: Optional[str] = Field(None, description="Name of created L3VPN service")
    status: Literal["Acknowledged", "Rejected"] = Field(..., description="TMF921 intent state")
    error_code: Optional[int] = Field(None, description="Error code: 400 (validation), 409 (conflict), 424 (dependency), 500 (internal)")
    message: str = Field(..., description="Human-readable result message")


class IntentStatusResponse(BaseModel):
    """Response from get status operation

    The current provisioning phase and status message of an L3VPN service.

    Phase Mapping:
        - Waiting: Service queued for processing
        - Processing: Service is being configured
        - Ready: VPN service is fully provisioned
        - Error: Provisioning failed
        - Deleted: Service no longer exists
    """
    phase: Literal["Waiting", "Processing", "Ready", "Error", "Deleted"] = Field(
        ..., description="Current provisioning phase"
    )
    message: str = Field(..., description="Status message from orchestrator")


class DeleteIntentResponse(BaseModel):
    """Response from delete operation

    Confirms whether the L3VPN service was successfully deleted.
    """
    success: bool = Field(..., description="Whether service was deleted successfully")
    message: str = Field(..., description="Result message")


# ============================================================================
# Exception Hierarchy
# ============================================================================

class OrchestratorError(Exception):
    """Base exception for orchestrator communication errors

    All orchestrator-specific exceptions inherit from this base class.
    Includes the original error message and optional HTTP-style error code.

    Attributes:
        message: Human-readable error description
        error_code: HTTP-style error code (400, 409, 424, 500, etc.)
    """
    def __init__(self, message: str, error_code: Optional[int] = None):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class OrchestratorValidationError(OrchestratorError):
    """Orchestrator returned validation error (400)

    Raised when the orchestrator rejects an intent due to schema validation
    failures, missing required fields, or invalid field values.

    Example:
        - Missing serviceName
        - Invalid CIDR format in customerPrefix
        - Topology type not in allowed values
    """
    pass


class OrchestratorConflictError(OrchestratorError):
    """Orchestrator returned conflict error (409)

    Raised when the orchestrator detects a conflict with existing resources.

    Example:
        - L3VPN service with same serviceName already exists
        - Duplicate VRF name or RT value
    """
    pass


class OrchestratorDependencyError(OrchestratorError):
    """Orchestrator returned dependency error (424)

    Raised when a required dependency is missing or not in the expected state.

    Example:
        - Underlay network referenced in underlayRef doesn't exist
        - Underlay network exists but is not ready
        - PE router referenced in endpoint not found in topology
    """
    pass


class OrchestratorCommunicationError(OrchestratorError):
    """Network or A2A protocol error

    Raised when communication with the orchestrator agent fails due to
    network issues, timeouts, or A2A protocol errors.

    Example:
        - HTTP connection timeout
        - A2A agent returned TASK_STATE_FAILED
        - Agent card resolution failed
    """
    pass
