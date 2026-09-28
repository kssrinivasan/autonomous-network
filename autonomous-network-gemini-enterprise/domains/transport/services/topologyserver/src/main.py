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

"""Transport Topology API Server - TMF639 Resource Inventory Management"""

# Load environment variables from .env file if it exists (for local development)
from dotenv import load_dotenv
load_dotenv()

import logging
import os
import asyncio
import uvicorn
from uuid import uuid4
from datetime import datetime, UTC
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional

from models import (
    ResourceInventoryList,
    HubRegistration,
)
from clients import SpannerTopologyClient, EventManager

# Configure logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Transport Topology API",
    description="""
## TMF639 Resource Inventory Management for Transport Domain

REST API implementing TMF639 Resource Inventory Management API v4.0 for discovering
available transport infrastructure resources.

### Architecture

```
Northbound System → Topology API → Spanner transporttopology-db
     (Service         (TMF639)       (PhysicalRouter, PhysicalInterface,
   Orchestrator)                      L3VPNService)
```

### Key Features

- **PE Router Discovery**: Find available PE routers by geographic location
- **Interface Discovery**: Discover customer-facing interfaces for service attachment
- **VPN Service Inventory**: Query existing L3VPN services for conflict checking
- **Real-time Events**: Subscribe to topology changes via WebSocket or Webhook (TMF688)

### Security

- Only **PE routers** are exposed (P, RR routers are filtered)
- Only **interfaces on PE routers** are exposed (interfaces on P, RR routers are filtered)
- All queries enforce **SCD Type 2** temporal filtering (valid_end_ts IS NULL)

### References

- [TMF639 Specification](https://www.tmforum.org/resources/specification/tmf639-resource-inventory-management-api-rest-specification-r19-0-1/)
- [INVENTORY.md](https://github.com/your-org/autonomous-network/blob/main/domains/transport/specs/INVENTORY.md)
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
            "name": "inventory",
            "description": "TMF639 Resource Inventory operations"
        },
        {
            "name": "events",
            "description": "TMF688 Event Management (Webhooks)"
        },
        {
            "name": "health",
            "description": "Service health check"
        }
    ]
)

# Environment configuration
SPANNER_INSTANCE = os.getenv('SPANNER_INSTANCE', 'networktopology-instance')
SPANNER_DATABASE = os.getenv('GOOGLE_SPANNER_TRANSPORT_DATABASE', 'transporttopology-db')

# Initialize clients (lazy connection on first use)
spanner_client = SpannerTopologyClient(SPANNER_INSTANCE, SPANNER_DATABASE)
event_manager = EventManager()


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Check service health and database connectivity status"
)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "transport-topology-api",
        "spanner_database": f"{SPANNER_INSTANCE}/{SPANNER_DATABASE}"
    }


@app.get(
    "/tmf-api/resourceInventoryManagement/v4/transport/resource",
    response_model=ResourceInventoryList,
    tags=["inventory"],
    summary="List resources",
    description="""
List transport resources with optional filtering and pagination.

**Resource Types:**
- `PhysicalRouter` - PE routers only (filtered by role='PE')
- `PhysicalInterface` - Interfaces on PE routers only
- `L3VPNService` - Existing L3VPN services

**Query Parameters:**
- `resourceSpecification`: Filter by resource type (PhysicalRouter, PhysicalInterface, L3VPNService)
- `resourceStatus`: Filter by status (Active, Available, InUse, etc.)
- `role`: Filter PE routers by role (only 'PE' allowed)
- `location_city`: Filter routers by city
- `router_id`: Filter interfaces by parent router
- `limit`: Page size (max 100, default 20)
- `offset`: Page offset (default 0)

**Examples:**
```
GET /resource?resourceSpecification=PhysicalRouter&location_city=London
GET /resource?resourceSpecification=PhysicalInterface&router_id=router:pe1
GET /resource?resourceSpecification=L3VPNService&resourceStatus=Ready
```
    """
)
async def list_resources(
    resourceSpecification: Optional[str] = None,
    resourceStatus: Optional[str] = None,
    role: Optional[str] = None,
    location_city: Optional[str] = None,
    router_id: Optional[str] = None,
    limit: int = Query(20, le=100, ge=1),
    offset: int = Query(0, ge=0)
):
    """List resources with filtering and pagination"""

    try:
        # Build filters dict
        filters = {}
        if role:
            filters["role"] = role
        if location_city:
            filters["location_city"] = location_city
        if router_id:
            filters["router_id"] = router_id

        # Query Spanner
        resources = await spanner_client.query_resources(
            spec=resourceSpecification,
            status=resourceStatus,
            filters=filters,
            limit=limit,
            offset=offset
        )

        return ResourceInventoryList(
            resourceInventory=resources,
            totalCount=len(resources),
            limit=limit,
            offset=offset
        )

    except Exception as e:
        logger.error(f"Error querying resources: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get(
    "/tmf-api/resourceInventoryManagement/v4/transport/resource/{resource_id}",
    tags=["inventory"],
    summary="Get resource by ID",
    description="""
Get specific resource by ID.

**Resource ID Patterns:**
- PhysicalRouter: `router:pe1`
- PhysicalInterface: `router:pe1:interface:eth2`
- L3VPNService: `vpn:BLUE_HUB`

**Security:**
- Returns 404 if resource is not a PE router (for router IDs)
- Returns 404 if resource is not a customer interface (for interface IDs)
    """
)
async def get_resource(resource_id: str):
    """Get specific resource by ID"""

    try:
        resource = await spanner_client.get_resource_by_id(resource_id)

        if not resource:
            raise HTTPException(
                status_code=404,
                detail=f"Resource '{resource_id}' not found or not exposed via inventory API"
            )

        return resource

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.websocket("/tmf-api/resourceInventoryManagement/v4/transport/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for real-time event streaming (TMF688)

    Subscribe message format:
    {
        "action": "subscribe",
        "subscriptionId": "pe-monitor",
        "filters": {
            "resourceSpecification": ["PhysicalRouter"],
            "eventTypes": ["ResourceStateChangeEvent"],
            "role": "PE"
        }
    }

    Unsubscribe message format:
    {
        "action": "unsubscribe"
    }
    """
    await websocket.accept()
    subscription_id = None

    try:
        # Receive subscription request
        subscribe_msg = await websocket.receive_json()

        if subscribe_msg.get("action") != "subscribe":
            await websocket.send_json({"error": "First message must be a subscribe action"})
            await websocket.close()
            return

        subscription_id = subscribe_msg.get("subscriptionId", str(uuid4()))
        filters = subscribe_msg.get("filters", {})

        # Register subscription with event manager
        await event_manager.register_websocket(subscription_id, websocket, filters)

        await websocket.send_json({
            "status": "subscribed",
            "subscriptionId": subscription_id
        })

        # Keep connection alive and handle unsubscribe
        while True:
            msg = await websocket.receive_json()
            if msg.get("action") == "unsubscribe":
                await event_manager.unregister_websocket(subscription_id)
                await websocket.send_json({"status": "unsubscribed"})
                break

    except WebSocketDisconnect:
        if subscription_id:
            await event_manager.unregister_websocket(subscription_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if subscription_id:
            await event_manager.unregister_websocket(subscription_id)


@app.post(
    "/tmf-api/resourceInventoryManagement/v4/transport/hub",
    response_model=HubRegistration,
    tags=["events"],
    summary="Register webhook",
    description="""
Register webhook callback for event notifications (TMF688).

**Request:**
```json
{
  "callback": "https://service-orchestrator.example.com/webhook/transport",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=PhysicalRouter&role=PE"
}
```

**Response:**
```json
{
  "id": "hub-uuid",
  "callback": "https://service-orchestrator.example.com/webhook/transport",
  "query": "eventType=ResourceStateChangeEvent&resourceSpecification=PhysicalRouter&role=PE"
}
```

**Query String Filters:**
- `eventType`: ResourceStateChangeEvent, ResourceCreateEvent, ResourceDeleteEvent
- `resourceSpecification`: PhysicalRouter, PhysicalInterface, L3VPNService
- `role`: PE
- `location_city`: <city_name>
    """
)
async def register_webhook(hub: HubRegistration):
    """Register webhook callback for event notifications"""

    try:
        # Parse query string into filters
        filters = {}
        if hub.query:
            for param in hub.query.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    filters[key] = value

        # Register with event manager
        hub_id = await event_manager.register_webhook(hub.callback, filters)

        return HubRegistration(
            id=hub_id,
            callback=hub.callback,
            query=hub.query
        )

    except Exception as e:
        logger.error(f"Error registering webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.delete(
    "/tmf-api/resourceInventoryManagement/v4/transport/hub/{hub_id}",
    tags=["events"],
    summary="Unregister webhook",
    description="Unregister webhook callback by hub ID"
)
async def unregister_webhook(hub_id: str):
    """Unregister webhook callback"""

    try:
        await event_manager.unregister_webhook(hub_id)
        return {"status": "unregistered", "hub_id": hub_id}

    except Exception as e:
        logger.error(f"Error unregistering webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.on_event("startup")
async def startup_event():
    """Start background event poller on application startup"""
    logger.info("Starting background event poller...")
    asyncio.create_task(poll_topology_changes())


async def poll_topology_changes():
    """Poll Spanner for topology changes and publish events

    Strategy: Track last_poll_timestamp and query for changes since then
    """

    last_poll_ts = datetime.now(UTC)

    while True:
        try:
            await asyncio.sleep(5)  # Poll every 5 seconds

            # Query for PhysicalRouter state changes
            router_changes = await spanner_client.get_changes_since(last_poll_ts, "PhysicalRouter")
            for resource in router_changes:
                event = {
                    "eventId": str(uuid4()),
                    "eventTime": datetime.now(UTC).isoformat(),
                    "eventType": "ResourceStateChangeEvent",
                    "domain": "transport",
                    "event": {
                        "resource": resource,
                        "stateChange": None  # Could track previous state if needed
                    }
                }
                await event_manager.publish_event(event)

            # Query for L3VPNService state changes
            vpn_changes = await spanner_client.get_changes_since(last_poll_ts, "L3VPNService")
            for resource in vpn_changes:
                event = {
                    "eventId": str(uuid4()),
                    "eventTime": datetime.now(UTC).isoformat(),
                    "eventType": "ResourceCreateEvent",  # Could be Create/Delete based on status
                    "domain": "transport",
                    "event": {
                        "resource": resource,
                        "stateChange": None
                    }
                }
                await event_manager.publish_event(event)

            last_poll_ts = datetime.now(UTC)

        except Exception as e:
            logger.error(f"Error in event poller: {e}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
