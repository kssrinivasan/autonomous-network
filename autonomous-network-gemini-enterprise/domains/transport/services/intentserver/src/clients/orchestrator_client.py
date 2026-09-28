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

"""A2A client for Transport Orchestrator Agent"""

import logging
import json
import os
import time
import httpx
from typing import Type, TypeVar, Any
from pydantic import BaseModel
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from google.auth import default
from google.auth.transport.requests import Request
from a2a.client import create_client, ClientConfig
from a2a.types import SendMessageRequest, Message, Part, Role, TaskState
from uuid import uuid4

from models.orchestrator_protocol import (
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

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class GoogleCloudAuth(httpx.Auth):
    """Custom auth handler for Google Cloud API requests"""

    def __init__(self):
        self.credentials, self.project = default()

    def auth_flow(self, request):
        # Refresh credentials if needed
        if not self.credentials.valid:
            self.credentials.refresh(Request())

        # Add Bearer token to request
        request.headers['Authorization'] = f'Bearer {self.credentials.token}'
        yield request


class TransportOrchestratorClient:
    """Client for communicating with the Transport Orchestrator via A2A"""

    def __init__(self, agent_url: str):
        """Initialize the Transport Orchestrator client.

        Args:
            agent_url: URL of the Transport Orchestrator A2A endpoint
        """
        self.agent_url = agent_url
        self.client = None

    async def connect(self):
        """Create A2A client connection to Transport Orchestrator"""
        logger.info("Connecting to Transport Orchestrator", extra={
            "agent_url": self.agent_url,
            "timeout_total": 300.0,
            "timeout_connect": 10.0,
            "timeout_read": 120.0,
            "timeout_write": 30.0,
        })

        start_time = time.time()

        try:
            # Create httpx client with Google Cloud auth and timeout for agent processing
            debug = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
            httpx_client = httpx.AsyncClient(
                auth=None if debug else GoogleCloudAuth(),
                timeout=httpx.Timeout(
                    timeout=300.0,   # 5 minutes total
                    connect=10.0,    # 10s to establish connection
                    read=120.0,      # 120s between read chunks
                    write=30.0       # 30s for writes
                )
            )
            client_config = ClientConfig(httpx_client=httpx_client)

            # Create A2A client
            self.client = await create_client(
                agent=self.agent_url,
                client_config=client_config,
                resolver_http_kwargs={'timeout': 30.0}
            )

            duration_ms = (time.time() - start_time) * 1000

            logger.info("Successfully connected to Transport Orchestrator", extra={
                "agent_url": self.agent_url,
                "connect_duration_ms": round(duration_ms, 2),
            })

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Failed to connect to Transport Orchestrator", extra={
                "agent_url": self.agent_url,
                "connect_duration_ms": round(duration_ms, 2),
                "error": str(e),
            }, exc_info=True)
            raise

    def _to_proto_value(self, value: Any) -> struct_pb2.Value:
        """Convert Python value to protobuf Value

        Handles nested dicts, lists, primitives, and None values.

        Args:
            value: Python value to convert

        Returns:
            Protobuf Value object
        """
        proto_value = struct_pb2.Value()

        if value is None:
            proto_value.null_value = struct_pb2.NULL_VALUE
        elif isinstance(value, bool):
            proto_value.bool_value = value
        elif isinstance(value, str):
            proto_value.string_value = value
        elif isinstance(value, (int, float)):
            proto_value.number_value = float(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                proto_value.struct_value.fields[k].CopyFrom(self._to_proto_value(v))
        elif isinstance(value, list):
            for item in value:
                proto_value.list_value.values.add().CopyFrom(self._to_proto_value(item))
        else:
            # Fallback: convert to string
            proto_value.string_value = str(value)

        return proto_value

    def _map_error(self, result: dict) -> None:
        """Raise typed exception based on error_code

        Args:
            result: Response dict from orchestrator

        Raises:
            OrchestratorValidationError: For 400 errors
            OrchestratorConflictError: For 409 errors
            OrchestratorDependencyError: For 424 errors
            OrchestratorError: For other errors
        """
        if result.get("success", True):
            return

        error_code = result.get("error_code")
        message = result.get("message", "Unknown error")

        if error_code == 400:
            raise OrchestratorValidationError(message, error_code)
        elif error_code == 409:
            raise OrchestratorConflictError(message, error_code)
        elif error_code == 424:
            raise OrchestratorDependencyError(message, error_code)
        else:
            raise OrchestratorError(message, error_code)

    async def create_intent(self, request: CreateIntentRequest) -> CreateIntentResponse:
        """Create L3VPN service via orchestrator agent

        Args:
            request: Typed request with Intent

        Returns:
            Typed response with success status, service name, and message

        Raises:
            OrchestratorValidationError: Schema validation failed (400)
            OrchestratorConflictError: Duplicate serviceName (409)
            OrchestratorDependencyError: Underlay not found/ready (424)
            OrchestratorError: Other errors (500)
            OrchestratorCommunicationError: Network/A2A errors
        """
        if not self.client:
            logger.debug("A2A client not connected, connecting now")
            await self.connect()

        logger.info("Orchestrator: creating intent", extra={
            "intent_id": request.intent.id,
            "intent_name": request.intent.name,
        })

        start_time = time.time()

        try:
            # Send structured request and get typed response
            response = await self._send_message(request, CreateIntentResponse)

            duration_ms = (time.time() - start_time) * 1000

            logger.info("Orchestrator: intent created", extra={
                "intent_id": request.intent.id,
                "intent_name": request.intent.name,
                "success": response.success,
                "service_name": response.service_name,
                "response_message": response.message,
                "duration_ms": round(duration_ms, 2),
            })

            return response

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Orchestrator: failed to create intent", extra={
                "intent_id": request.intent.id,
                "intent_name": request.intent.name,
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }, exc_info=True)
            raise

    async def get_intent_status(self, request: GetIntentStatusRequest) -> IntentStatusResponse:
        """Get L3VPN service status

        Args:
            request: Typed request with service name

        Returns:
            Typed response with phase and status message

        Raises:
            OrchestratorCommunicationError: Network/A2A errors
        """
        if not self.client:
            logger.debug("A2A client not connected, connecting now")
            await self.connect()

        logger.debug("Orchestrator: getting intent status", extra={
            "service_name": request.service_name,
        })

        start_time = time.time()

        try:
            # Send structured request and get typed response
            response = await self._send_message(request, IntentStatusResponse)

            duration_ms = (time.time() - start_time) * 1000

            logger.debug("Orchestrator: status retrieved", extra={
                "service_name": request.service_name,
                "phase": response.phase,
                "response_message": response.message,
                "duration_ms": round(duration_ms, 2),
            })

            return response

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Orchestrator: failed to get status", extra={
                "service_name": request.service_name,
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }, exc_info=True)
            raise

    async def delete_intent(self, request: DeleteIntentRequest) -> DeleteIntentResponse:
        """Delete L3VPN service

        Args:
            request: Typed request with service name

        Returns:
            Typed response with success status and message

        Raises:
            OrchestratorCommunicationError: Network/A2A errors
        """
        if not self.client:
            logger.debug("A2A client not connected, connecting now")
            await self.connect()

        logger.info("Orchestrator: deleting intent", extra={
            "service_name": request.service_name,
        })

        start_time = time.time()

        try:
            # Send structured request and get typed response
            response = await self._send_message(request, DeleteIntentResponse)

            duration_ms = (time.time() - start_time) * 1000

            logger.info("Orchestrator: intent deleted", extra={
                "service_name": request.service_name,
                "success": response.success,
                "response_message": response.message,
                "duration_ms": round(duration_ms, 2),
            })

            return response

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("Orchestrator: failed to delete intent", extra={
                "service_name": request.service_name,
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }, exc_info=True)
            raise

    async def _send_message(self, request_model: BaseModel, response_model: Type[T]) -> T:
        """Send A2A message with structured data and parse typed response

        Args:
            request_model: Pydantic request model to send
            response_model: Pydantic response model class to parse into

        Returns:
            Typed response validated against response_model

        Raises:
            OrchestratorError: Business logic errors from orchestrator
            OrchestratorCommunicationError: Network/A2A protocol errors
        """
        # Serialize request to dict
        request_dict = request_model.model_dump(by_alias=True, exclude_none=True)

        # Create A2A message
        message = Message()
        message.role = Role.ROLE_USER
        message.message_id = uuid4().hex

        # Create Part with structured data (part.data instead of part.text)
        part = Part()
        for key, value in request_dict.items():
            part.data.struct_value.fields[key].CopyFrom(self._to_proto_value(value))
        part.media_type = "application/json"
        message.parts.append(part)

        # Create send request
        request = SendMessageRequest()
        request.tenant = "default"
        request.message.CopyFrom(message)

        logger.debug("Sending A2A message", extra={
            "message_id": message.message_id,
            "fields": list(request_dict.keys()),
        })

        try:
            response_data = None
            response_text = ""
            response_start = time.time()

            async for response in self.client.send_message(request):
                # Collect artifact updates (streaming content)
                if response.HasField('artifact_update'):
                    artifact = response.artifact_update.artifact
                    if artifact.parts:
                        artifact_part = artifact.parts[0]

                        # Prefer structured data if available
                        if artifact_part.HasField('data'):
                            # Convert protobuf struct to dict
                            response_data = MessageToDict(
                                artifact_part.data,
                                preserving_proto_field_name=True
                            )
                        # Fallback to text for backward compatibility
                        elif artifact_part.text:
                            response_text += artifact_part.text

                # Check final status
                if response.HasField('status_update'):
                    status = response.status_update.status

                    if status.state == TaskState.TASK_STATE_FAILED:
                        error_msg = "Unknown error"
                        if status.message and status.message.parts:
                            error_msg = status.message.parts[0].text
                        raise OrchestratorCommunicationError(
                            f"Orchestrator task failed: {error_msg}"
                        )

                    if status.state == TaskState.TASK_STATE_COMPLETED:
                        response_duration_ms = (time.time() - response_start) * 1000
                        logger.debug("Orchestrator task completed", extra={
                            "response_duration_ms": round(response_duration_ms, 2),
                        })
                        # Get final message if no data collected yet
                        if not response_data and status.message and status.message.parts:
                            final_part = status.message.parts[0]
                            if final_part.HasField('data'):
                                response_data = MessageToDict(
                                    final_part.data,
                                    preserving_proto_field_name=True
                                )
                            elif final_part.text and not response_text:
                                response_text = final_part.text
                        break

            # Parse response - prefer structured data, fallback to text
            if response_data:
                result_dict = response_data
                logger.debug("Parsed structured response", extra={
                    "fields": list(result_dict.keys()),
                })
            elif response_text:
                # Extract JSON from text (handle markdown code blocks)
                json_text = response_text.strip()
                if json_text.startswith("```json"):
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif json_text.startswith("```"):
                    json_text = json_text.split("```")[1].split("```")[0].strip()

                try:
                    result_dict = json.loads(json_text)
                    logger.debug("Parsed text response as JSON", extra={
                        "fields": list(result_dict.keys()),
                    })
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse JSON response", extra={
                        "response_text": json_text[:200],  # First 200 chars
                        "error": str(e),
                    })
                    raise OrchestratorCommunicationError(
                        f"Invalid JSON response from orchestrator: {e}"
                    )
            else:
                logger.error("No response received from orchestrator")
                raise OrchestratorCommunicationError("No response received from orchestrator")

            # Validate against response model and map errors
            try:
                response_obj = response_model(**result_dict)

                # Check for business logic errors and raise typed exceptions
                self._map_error(result_dict)

                return response_obj

            except Exception as e:
                if isinstance(e, OrchestratorError):
                    raise
                logger.error("Failed to validate response", extra={
                    "error": str(e),
                })
                raise OrchestratorCommunicationError(f"Invalid response format: {e}")

        except httpx.HTTPError as e:
            logger.error("HTTP error communicating with orchestrator", extra={
                "error": str(e),
                "error_type": type(e).__name__,
            }, exc_info=True)
            raise OrchestratorCommunicationError(f"Failed to communicate with orchestrator: {e}")
        except OrchestratorError:
            raise
        except Exception as e:
            logger.error("Error during orchestrator communication", extra={
                "error": str(e),
                "error_type": type(e).__name__,
            }, exc_info=True)
            if isinstance(e, OrchestratorCommunicationError):
                raise
            raise OrchestratorCommunicationError(str(e))

    async def close(self):
        """Close the A2A client connection"""
        if self.client and hasattr(self.client, '_httpx_client'):
            logger.debug("Closing orchestrator A2A client connection")
            try:
                await self.client._httpx_client.aclose()
                logger.debug("Orchestrator A2A client connection closed")
            except Exception as e:
                logger.warning("Error closing orchestrator client", extra={
                    "error": str(e),
                })
