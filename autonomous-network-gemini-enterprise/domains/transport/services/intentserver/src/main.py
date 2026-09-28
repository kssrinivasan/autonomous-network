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

"""Transport Intent API Server - TMF921 Intent Management for L3 VPN provisioning"""

import json
import logging
import os
import time
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

# Load .env file if it exists (for local development)
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

from models import (
    Intent,
    IntentList,
    IntentState,
    CreateIntentRequest,
    DeleteIntentRequest,
)
from clients import (
    TransportOrchestratorClient,
    SpannerIntentStore,
)

# Configure structured logging for Cloud Logging
class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured Cloud Logging"""

    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields from record
        if hasattr(record, 'request_id'):
            log_obj['request_id'] = record.request_id
        if hasattr(record, 'intent_id'):
            log_obj['intent_id'] = record.intent_id
        if hasattr(record, 'service_name'):
            log_obj['service_name'] = record.service_name
        if hasattr(record, 'duration_ms'):
            log_obj['duration_ms'] = record.duration_ms

        # Add exception info if present
        if record.exc_info:
            log_obj['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_obj)

# Setup logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
handler = logging.StreamHandler()

# Use JSON formatting for production (GCP Cloud Run), simple format for local
if os.getenv("K_SERVICE"):  # Cloud Run environment
    handler.setFormatter(StructuredFormatter())
else:
    log_format = "%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
    handler.setFormatter(logging.Formatter(log_format))

logging.basicConfig(level=log_level, handlers=[handler])
logger = logging.getLogger(__name__)

# Context var for request tracking
request_id_ctx: ContextVar[str] = ContextVar('request_id', default=None)

# Create FastAPI app
app = FastAPI(
    title="Transport Intent API",
    description="""
## TMF921 Intent Management for Layer 3 VPN provisioning

REST API implementing TMF921 Intent Management aligned with IETF ACTN (RFC 8453) architecture.

### Intent Lifecycle

```
POST /intent → Acknowledged → InProgress → Completed/Rejected
DELETE /intent → Inactive
```

### References

- [TMF921 Specification](https://www.tmforum.org/resources/specification/tmf921-intent-management-api-user-guide-v1-0-0/)
- [IETF ACTN RFC 8453](https://datatracker.ietf.org/doc/html/rfc8453)
    """,
    version="1.0.0",
    contact={
        "name": "Autonomous Network Lab",
        "url": "https://github.com/your-org/autonomous-network",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    openapi_tags=[
        {
            "name": "intents",
            "description": "TMF921 Intent Management operations"
        },
        {
            "name": "health",
            "description": "Service health check"
        }
    ]
)

# Environment configuration
GOOGLE_PROJECT = os.getenv('GOOGLE_PROJECT')
ORCHESTRATOR_URL = os.getenv('ORCHESTRATOR_URL')
SPANNER_INSTANCE = os.getenv('SPANNER_INSTANCE', 'networktopology-instance')
SPANNER_DATABASE = os.getenv('GOOGLE_SPANNER_OPS_DATABASE', 'agentops-db')

if not GOOGLE_PROJECT:
    logger.error("GOOGLE_PROJECT environment variable not set")
    raise ValueError("GOOGLE_PROJECT is required")

logger.info("Intent server initializing", extra={
    "google_project": GOOGLE_PROJECT,
    "orchestrator_url": ORCHESTRATOR_URL or "NOT_SET",
    "spanner_instance": SPANNER_INSTANCE,
    "spanner_database": SPANNER_DATABASE,
})

if not ORCHESTRATOR_URL:
    logger.warning("ORCHESTRATOR_URL not set - intent creation will fail")

# Initialize clients (lazy connection on first use)
orchestrator_client = TransportOrchestratorClient(ORCHESTRATOR_URL) if ORCHESTRATOR_URL else None
spanner_store = SpannerIntentStore(GOOGLE_PROJECT, SPANNER_INSTANCE, SPANNER_DATABASE)

logger.info("Clients initialized", extra={
    "orchestrator_configured": bool(orchestrator_client),
    "spanner_configured": True,
})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log all requests and track duration"""
    request_id = str(uuid4())
    request_id_ctx.set(request_id)

    start_time = time.time()

    logger.info("Request started", extra={
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query) if request.url.query else None,
        "client": request.client.host if request.client else None,
    })

    try:
        response = await call_next(request)
        duration_ms = (time.time() - start_time) * 1000

        logger.info("Request completed", extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        })

        return response
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error("Request failed", extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "duration_ms": round(duration_ms, 2),
            "error": str(e),
        }, exc_info=True)
        raise


_PHASE_TO_TMF921 = {
    "Acknowledged": IntentState.ACKNOWLEDGED,
    "Planning": IntentState.IN_PROGRESS,
    "Designing": IntentState.IN_PROGRESS,
    "Deploying": IntentState.IN_PROGRESS,
    "Completed": IntentState.COMPLETED,
    "Rejected": IntentState.REJECTED,
    "Inactive": IntentState.INACTIVE,
    # Legacy orchestrator phases (kept for backward compatibility)
    "Waiting": IntentState.IN_PROGRESS,
    "Processing": IntentState.IN_PROGRESS,
    "Ready": IntentState.COMPLETED,
    "Error": IntentState.REJECTED,
    "Deleted": IntentState.INACTIVE,
}


def map_phase_to_tmf921(phase: str) -> str:
    """Map Spanner phase to TMF921 state"""
    return _PHASE_TO_TMF921.get(phase, IntentState.IN_PROGRESS)


async def _run_orchestration(intent_id: str, intent: Intent) -> None:
    """Background task: call orchestrator and handle connection-level errors.

    The orchestrator's own after_agent_callbacks write progress phases to Spanner.
    This task only handles the case where the A2A call itself fails to connect.
    """
    try:
        request = CreateIntentRequest(intent=intent, intent_id=intent_id)
        await orchestrator_client.create_intent(request)
    except Exception as e:
        logger.error("Background orchestration failed — marking intent Rejected", extra={
            "intent_id": intent_id,
            "error": str(e),
        }, exc_info=True)
        try:
            await spanner_store.update_phase(intent_id, "Rejected")
        except Exception as spanner_err:
            logger.error("Failed to update Spanner after orchestration error", extra={
                "intent_id": intent_id,
                "error": str(spanner_err),
            })


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Check service health and configuration status"
)
async def health_check():
    """Health check endpoint"""
    request_id = request_id_ctx.get()

    logger.debug("Health check requested", extra={
        "request_id": request_id,
    })

    health_status = {
        "status": "healthy",
        "service": "transport-intent-api",
        "orchestrator_configured": bool(ORCHESTRATOR_URL)
    }

    return health_status


@app.post(
    "/intent",
    status_code=202,
    response_model=Intent,
    tags=["intents"],
    summary="Create new L3 VPN intent",
    description="""
Submit a new L3 VPN service intent for provisioning.

**8-Step Orchestration Pipeline:**

1. Validate TMF921 schema (Pydantic)
2. Generate intent ID (UUID)
3. Check for serviceId conflicts (Spanner)
4. Call orchestrator to validate and provision L3VPN service (via A2A)
5. Store intent record (Spanner)
6. Return 202 Acknowledged
7. Orchestrator provisions VPN service asynchronously

**State Transitions:**

- Initial: `Acknowledged`
- Service Waiting/Processing: `InProgress`
- Service Ready: `Completed`
- Service Error: `Rejected`

Poll `GET /intent/{id}` for status updates.
    """,
    responses={
        202: {
            "description": "Intent accepted and queued for orchestration",
            "content": {
                "application/json": {
                    "example": {
                        "@type": "Intent",
                        "id": "a3f2c1d4-5e6f-7g8h-9i0j-1k2l3m4n5o6p",
                        "name": "red-mesh-vpn",
                        "state": "Acknowledged",
                        "message": "Intent received and queued for orchestration",
                        "intentExpression": {"@type": "IntentExpression", "expectation": []}
                    }
                }
            }
        },
        400: {"description": "Validation error - missing or invalid fields"},
        409: {"description": "Conflict - serviceId already exists"},
        424: {"description": "Failed dependency - underlay network not ready"},
        500: {"description": "Internal server error"}
    }
)
async def create_intent(intent: Intent, background_tasks: BackgroundTasks):
    """Submit a new L3 VPN service intent

    Returns immediately with 202 Acknowledged after persisting the intent to
    Spanner. The orchestration pipeline runs asynchronously in the background;
    poll GET /intent/{id} for progress.

    Args:
        intent: TMF921 Intent envelope

    Returns:
        Intent with assigned ID and state=Acknowledged

    Raises:
        409: Conflict - serviceId already exists
        500: Internal error
    """
    request_id = request_id_ctx.get()

    logger.info("Creating intent", extra={
        "request_id": request_id,
        "intent_name": intent.name,
        "has_intent_id": bool(intent.id),
    })

    if not intent.id:
        intent.id = str(uuid4())

    intent.state = IntentState.ACKNOWLEDGED
    intent.message = "Intent received and queued for orchestration"

    try:
        expectation = intent.intentExpression.expectation[0]
        service_id = expectation.expectationObject.serviceName

        conflict = await spanner_store.check_service_conflict(service_id)
        if conflict:
            logger.warning("Service conflict detected", extra={
                "request_id": request_id,
                "intent_id": intent.id,
                "service_id": service_id,
            })
            raise HTTPException(
                status_code=409,
                detail=f"Conflict: VPN service with serviceId '{service_id}' already exists"
            )

        if not orchestrator_client:
            raise HTTPException(status_code=500, detail="Orchestrator not configured")

        # Persist intent record immediately — crName populated by orchestrator callback
        await spanner_store.create_intent(
            intent.model_dump(by_alias=True, exclude_none=True),
        )

        logger.info("Intent persisted; launching background orchestration", extra={
            "request_id": request_id,
            "intent_id": intent.id,
            "service_id": service_id,
        })

        background_tasks.add_task(_run_orchestration, intent.id, intent)

        return intent

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating intent", extra={
            "request_id": request_id,
            "intent_id": intent.id if intent.id else None,
            "error": str(e),
        }, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get(
    "/intents",
    response_model=IntentList,
    tags=["intents"],
    summary="List all active intents",
    description="Retrieve all L3 VPN service intents with state != Inactive. Each intent is enriched with live status from the orchestrator.",
    responses={
        200: {
            "description": "List of active intents",
            "content": {
                "application/json": {
                    "example": {
                        "intents": [
                            {
                                "@type": "Intent",
                                "id": "a3f2c1d4-...",
                                "name": "red-mesh-vpn",
                                "state": "Completed",
                                "serviceStatus": {"phase": "Ready", "message": "All routers configured"},
                                "intentExpression": {"@type": "IntentExpression", "expectation": []}
                            }
                        ]
                    }
                }
            }
        }
    }
)
async def list_intents():
    """List all active L3 VPN service intents

    Returns:
        IntentList with all active intents (state != Inactive)
    """
    request_id = request_id_ctx.get()

    logger.info("Listing all active intents", extra={
        "request_id": request_id,
    })

    try:
        # Fetch intents from Spanner
        spanner_start = time.time()
        intent_records = await spanner_store.list_intents()
        spanner_duration_ms = (time.time() - spanner_start) * 1000

        logger.info("Fetched intents from Spanner", extra={
            "request_id": request_id,
            "count": len(intent_records),
            "spanner_duration_ms": round(spanner_duration_ms, 2),
        })

        intents = []
        for record in intent_records:
            intent = Intent(**record["envelope"])
            intent.state = map_phase_to_tmf921(record["state"])
            intents.append(intent)

        logger.info("Intents listed", extra={
            "request_id": request_id,
            "count": len(intents),
        })

        return IntentList(intents=intents)

    except Exception as e:
        logger.error("Error listing intents", extra={
            "request_id": request_id,
            "error": str(e),
        }, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get(
    "/intent/{intent_id}",
    response_model=Intent,
    tags=["intents"],
    summary="Get intent status",
    description="""
Retrieve a specific L3 VPN intent by ID with live status from the orchestrator.

**State Mapping:**

- Service `Waiting/Processing` → TMF921 `InProgress`
- Service `Ready` → TMF921 `Completed`
- Service `Error` → TMF921 `Rejected`
- Service `Deleted` → TMF921 `Inactive`

The response includes both the original intent envelope and live `serviceStatus` from the orchestrator.
    """,
    responses={
        200: {
            "description": "Intent with live state and service status",
            "content": {
                "application/json": {
                    "example": {
                        "@type": "Intent",
                        "id": "a3f2c1d4-...",
                        "name": "red-mesh-vpn",
                        "state": "Completed",
                        "message": "VPN service is Ready",
                        "serviceStatus": {"phase": "Ready", "message": "All PE and CE routers configured successfully"},
                        "intentExpression": {"@type": "IntentExpression", "expectation": []}
                    }
                }
            }
        },
        404: {"description": "Intent not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_intent(intent_id: str):
    """Get the current state of an L3 VPN intent

    Fetches intent from Spanner and enriches with live service status from orchestrator.

    Args:
        intent_id: Intent UUID

    Returns:
        Intent with live state and service status

    Raises:
        404: Intent not found
        500: Internal error
    """
    request_id = request_id_ctx.get()

    logger.info("Fetching intent", extra={
        "request_id": request_id,
        "intent_id": intent_id,
    })

    try:
        # Fetch intent from Spanner
        spanner_start = time.time()
        record = await spanner_store.get_intent(intent_id)
        spanner_duration_ms = (time.time() - spanner_start) * 1000

        if not record:
            logger.warning("Intent not found", extra={
                "request_id": request_id,
                "intent_id": intent_id,
            })
            raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")

        logger.debug("Intent fetched from Spanner", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "service_name": record.get("crName"),
            "spanner_duration_ms": round(spanner_duration_ms, 2),
        })

        intent = Intent(**record["envelope"])
        intent.state = map_phase_to_tmf921(record["state"])
        intent.serviceStatus = {"phase": record["state"]}

        logger.info("Intent retrieved", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "phase": record["state"],
            "state": intent.state,
        })

        return intent

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching intent", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "error": str(e),
        }, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.patch(
    "/intent/{intent_id}",
    tags=["intents"],
    summary="Update intent (not implemented)",
    description="Update an existing intent with partial modifications. Reserved for future enhancement.",
    responses={
        501: {"description": "Not implemented - use POST to create new intents instead"}
    }
)
async def update_intent(intent_id: str, request: Request):
    """Update an existing intent (PATCH support)

    Currently not implemented - reserved for future enhancement.

    Args:
        intent_id: Intent UUID
        request: PATCH payload

    Returns:
        Updated intent

    Raises:
        501: Not implemented
    """
    request_id = request_id_ctx.get()

    logger.info("PATCH request received (not implemented)", extra={
        "request_id": request_id,
        "intent_id": intent_id,
    })

    raise HTTPException(
        status_code=501,
        detail="PATCH /intent is not yet implemented. Use POST to create new intents."
    )


@app.delete(
    "/intent/{intent_id}",
    response_model=Intent,
    tags=["intents"],
    summary="Revoke intent",
    description="""
Revoke an L3 VPN intent and delete the associated service.

**Actions:**

1. Delete L3VPN service via orchestrator
2. Orchestrator removes VRF configs from PE/CE routers
3. Update Spanner intent state to `Inactive`
4. Return intent with state=Inactive

The VPN service is fully deprovisioned after successful deletion.
    """,
    responses={
        200: {
            "description": "Intent revoked and VPN service deleted",
            "content": {
                "application/json": {
                    "example": {
                        "@type": "Intent",
                        "id": "a3f2c1d4-...",
                        "state": "Inactive",
                        "message": "Intent revoked, VPN service deleted"
                    }
                }
            }
        },
        404: {"description": "Intent not found"},
        500: {"description": "Internal server error"}
    }
)
async def delete_intent(intent_id: str):
    """Revoke intent and delete L3VPN service

    Args:
        intent_id: Intent UUID

    Returns:
        Intent with state=Inactive

    Raises:
        404: Intent not found
        500: Internal error
    """
    request_id = request_id_ctx.get()

    logger.info("Deleting intent", extra={
        "request_id": request_id,
        "intent_id": intent_id,
    })

    try:
        # Fetch intent from Spanner
        spanner_start = time.time()
        record = await spanner_store.get_intent(intent_id)
        spanner_duration_ms = (time.time() - spanner_start) * 1000

        if not record:
            logger.warning("Intent not found for deletion", extra={
                "request_id": request_id,
                "intent_id": intent_id,
            })
            raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")

        logger.debug("Intent fetched for deletion", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "service_name": record.get("crName"),
            "spanner_duration_ms": round(spanner_duration_ms, 2),
        })

        # Delete L3VPN service via orchestrator
        if orchestrator_client and record["crName"]:
            try:
                logger.info("Calling orchestrator to delete L3VPN service", extra={
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "service_name": record["crName"],
                })

                orchestrator_start = time.time()
                delete_request = DeleteIntentRequest(
                    service_name=record["crName"]
                )
                response = await orchestrator_client.delete_intent(delete_request)
                orchestrator_duration_ms = (time.time() - orchestrator_start) * 1000

                if response.success:
                    logger.info("L3VPN service deleted successfully", extra={
                        "request_id": request_id,
                        "intent_id": intent_id,
                        "service_name": record["crName"],
                        "message": response.message,
                        "orchestrator_duration_ms": round(orchestrator_duration_ms, 2),
                    })
                else:
                    logger.warning("Service deletion reported failure", extra={
                        "request_id": request_id,
                        "intent_id": intent_id,
                        "service_name": record["crName"],
                        "message": response.message,
                        "orchestrator_duration_ms": round(orchestrator_duration_ms, 2),
                    })

            except Exception as e:
                logger.warning("Error deleting service from orchestrator", extra={
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "service_name": record["crName"],
                    "error": str(e),
                })
        else:
            logger.info("Skipping orchestrator deletion", extra={
                "request_id": request_id,
                "intent_id": intent_id,
                "reason": "no_orchestrator" if not orchestrator_client else "no_service_name",
            })

        # Update Spanner state to Inactive
        logger.debug("Updating intent state to Inactive in Spanner", extra={
            "request_id": request_id,
            "intent_id": intent_id,
        })

        spanner_update_start = time.time()
        await spanner_store.update_state(intent_id, IntentState.INACTIVE)
        spanner_update_duration_ms = (time.time() - spanner_update_start) * 1000

        # Return updated intent
        intent = Intent(**record["envelope"])
        intent.state = IntentState.INACTIVE
        intent.message = "Intent revoked, VPN service deleted"

        logger.info("Intent deleted successfully", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "spanner_update_duration_ms": round(spanner_update_duration_ms, 2),
        })

        return intent

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting intent", extra={
            "request_id": request_id,
            "intent_id": intent_id,
            "error": str(e),
        }, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    """Handle Pydantic validation errors"""
    request_id = request_id_ctx.get()

    logger.error("Validation error", extra={
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "errors": exc.errors(),
    })

    return JSONResponse(
        status_code=400,
        content={
            "detail": "Validation error",
            "errors": exc.errors()
        }
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = "0.0.0.0"

    logger.info("Starting Transport Intent API server", extra={
        "host": host,
        "port": port,
        "google_project": GOOGLE_PROJECT,
        "orchestrator_url": ORCHESTRATOR_URL or "NOT_SET",
        "spanner_instance": SPANNER_INSTANCE,
        "spanner_database": SPANNER_DATABASE,
        "log_level": log_level,
        "cloud_run": bool(os.getenv("K_SERVICE")),
        "service_name": os.getenv("K_SERVICE"),
        "revision": os.getenv("K_REVISION"),
    })

    uvicorn.run(app, host=host, port=port)
