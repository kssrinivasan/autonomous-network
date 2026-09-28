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

"""TMF639 Resource Inventory Management API v4.0 - Pydantic Models"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any
from datetime import datetime


class ResourceSpecification(BaseModel):
    """TMF639 Resource Specification reference"""
    id: str
    name: str


class ResourceCharacteristic(BaseModel):
    """TMF639 Resource Characteristic"""
    name: str
    value: Any


class RelatedParty(BaseModel):
    """TMF639 Related Party (parent resource)"""
    id: str
    href: str
    role: str
    name: str


class ValidFor(BaseModel):
    """TMF639 Valid For time period"""
    startDateTime: datetime
    endDateTime: Optional[datetime] = None


class Resource(BaseModel):
    """TMF639 Base Resource"""
    type: str = Field(alias="@type")
    id: str
    href: str
    name: str
    resourceSpecification: ResourceSpecification
    resourceStatus: str
    resourceCharacteristic: List[ResourceCharacteristic] = []
    relatedParty: Optional[List[RelatedParty]] = None
    validFor: ValidFor

    class Config:
        populate_by_name = True


class PhysicalRouter(Resource):
    """TMF639 Physical Router (PE only)"""
    type: Literal["PhysicalRouter"] = Field(default="PhysicalRouter", alias="@type")


class PhysicalInterface(Resource):
    """TMF639 Physical Interface (customer-facing only)"""
    type: Literal["PhysicalInterface"] = Field(default="PhysicalInterface", alias="@type")


class L3VPNService(Resource):
    """TMF639 L3 VPN Service"""
    type: Literal["L3VPNService"] = Field(default="L3VPNService", alias="@type")


class ResourceInventoryList(BaseModel):
    """TMF639 Resource Inventory List response"""
    resourceInventory: List[Resource]
    totalCount: int
    limit: int
    offset: int


# TMF688 Event Management Models

class StateChange(BaseModel):
    """TMF688 State Change details"""
    previousState: str
    newState: str
    reason: Optional[str] = None


class ResourceEvent(BaseModel):
    """TMF688 Resource Event payload"""
    resource: Resource
    stateChange: Optional[StateChange] = None


class Event(BaseModel):
    """TMF688 Event envelope"""
    eventId: str
    eventTime: datetime
    eventType: Literal["ResourceStateChangeEvent", "ResourceCreateEvent", "ResourceDeleteEvent"]
    domain: Literal["transport"]
    event: ResourceEvent


# Webhook Hub Models

class HubRegistration(BaseModel):
    """TMF688 Hub Registration for webhook callbacks"""
    id: Optional[str] = None
    callback: str
    query: str
