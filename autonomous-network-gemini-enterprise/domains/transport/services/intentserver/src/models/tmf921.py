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

"""TMF921 Intent Management Pydantic models"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Any
from enum import Enum


class IntentState(str, Enum):
    """TMF921 intent states"""
    ACKNOWLEDGED = "Acknowledged"
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"
    REJECTED = "Rejected"
    INACTIVE = "Inactive"


class VPNEndpoint(BaseModel):
    """L3VPN endpoint definition (high-level intent)"""
    peRouter: str = Field(..., description="Provider Edge router name")
    peInterface: str = Field(..., description="PE interface facing customer")
    customerRouter: str = Field(..., description="Customer Edge router name")
    customerPrefix: str = Field(..., description="Customer IP prefix to advertise (CIDR)")
    customerVlan: Optional[int] = Field(None, description="Customer VLAN ID (optional)")


class SLA(BaseModel):
    """Service Level Agreement requirements"""
    bandwidth: str = Field(..., description="Guaranteed bandwidth (e.g., '100 Mbps per endpoint')")
    latency: str = Field(..., description="Maximum latency requirement (e.g., '< 10ms')")
    availability: str = Field(..., description="Service availability SLA (e.g., '99.99%')")
    trafficClass: Literal["business-critical", "standard", "best-effort"] = Field(
        ..., description="Traffic class for QoS mapping"
    )


class L3VPNService(BaseModel):
    """TMF921 L3VPNService expectation object (high-level intent)

    The orchestrator derives low-level details (VRF name, RD, RT, BGP neighbors, QoS)
    from this high-level intent specification.
    """
    serviceName: str = Field(..., description="Human-readable service name")
    topology: Literal["any-to-any", "hub-spoke", "point-to-point"] = Field(
        ..., description="VPN topology type"
    )
    underlayRef: str = Field(..., description="Underlay network reference to validate")
    endpoints: List[VPNEndpoint] = Field(..., description="PE-CE connection points (min 2)")
    sla: SLA = Field(..., description="Service level agreement requirements")

    @field_validator('endpoints')
    @classmethod
    def validate_endpoints(cls, v):
        if not v or len(v) < 2:
            raise ValueError("At least two endpoints required for L3VPN service")
        return v


class L3ConnectivityExpectation(BaseModel):
    """TMF921 L3 connectivity expectation"""
    type: Literal["L3ConnectivityExpectation"] = Field(alias="@type")
    expectationVerb: Literal["DELIVER"]
    expectationObject: L3VPNService

    class Config:
        populate_by_name = True


class IntentExpression(BaseModel):
    """TMF921 Intent expression"""
    type: Literal["IntentExpression"] = Field(alias="@type")
    expectation: List[L3ConnectivityExpectation]

    @field_validator('expectation')
    @classmethod
    def validate_expectation(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one expectation required")
        return v

    class Config:
        populate_by_name = True


class ServiceStatus(BaseModel):
    """L3VPN service status"""
    phase: Optional[str] = None
    message: Optional[str] = None


class Intent(BaseModel):
    """TMF921 Intent envelope"""
    type: Literal["Intent"] = Field(alias="@type")
    id: Optional[str] = Field(None, description="Server-assigned UUID")
    name: str = Field(..., description="Human-readable intent name")
    description: Optional[str] = None
    priority: int = 50
    state: Optional[IntentState] = None
    message: Optional[str] = None
    intentExpression: IntentExpression
    serviceStatus: Optional[ServiceStatus] = None

    class Config:
        populate_by_name = True
        use_enum_values = True


class IntentList(BaseModel):
    """List of intents response"""
    intents: List[Intent]
