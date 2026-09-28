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

"""Event Manager for TMF688 Event Management (WebSocket + Webhook subscriptions)"""

import asyncio
import logging
from typing import Dict, Any
from fastapi import WebSocket
import httpx
from uuid import uuid4

logger = logging.getLogger(__name__)


class EventManager:
    """Manages event subscriptions (WebSocket + Webhook) and event publishing

    Supports TMF688 Event Management for real-time topology change notifications.
    """

    def __init__(self):
        self.websocket_subscriptions: Dict[str, Dict] = {}  # subscription_id -> {ws, filters}
        self.webhook_subscriptions: Dict[str, Dict] = {}    # hub_id -> {callback, filters}
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def register_websocket(
        self,
        subscription_id: str,
        websocket: WebSocket,
        filters: Dict[str, Any]
    ):
        """Register WebSocket subscription

        Args:
            subscription_id: Unique subscription identifier
            websocket: WebSocket connection
            filters: Event filters (resourceSpecification, eventTypes, role, location_city)
        """
        self.websocket_subscriptions[subscription_id] = {
            "ws": websocket,
            "filters": filters
        }
        logger.info(f"Registered WebSocket subscription: {subscription_id} with filters: {filters}")

    async def unregister_websocket(self, subscription_id: str):
        """Unregister WebSocket subscription"""
        if subscription_id in self.websocket_subscriptions:
            del self.websocket_subscriptions[subscription_id]
            logger.info(f"Unregistered WebSocket subscription: {subscription_id}")

    async def register_webhook(self, callback: str, filters: Dict[str, Any]) -> str:
        """Register webhook callback

        Args:
            callback: Webhook callback URL
            filters: Event filters (eventType, resourceSpecification, role, location_city)

        Returns:
            Hub ID (UUID)
        """
        hub_id = str(uuid4())
        self.webhook_subscriptions[hub_id] = {
            "callback": callback,
            "filters": filters
        }
        logger.info(f"Registered webhook: {hub_id} -> {callback} with filters: {filters}")
        return hub_id

    async def unregister_webhook(self, hub_id: str):
        """Unregister webhook callback"""
        if hub_id in self.webhook_subscriptions:
            callback = self.webhook_subscriptions[hub_id]["callback"]
            del self.webhook_subscriptions[hub_id]
            logger.info(f"Unregistered webhook: {hub_id} (was: {callback})")

    async def publish_event(self, event: Dict):
        """Publish event to all matching subscriptions

        Called by background task monitoring Spanner for changes.

        Args:
            event: TMF688 Event dict with eventId, eventTime, eventType, domain, event:{resource}
        """

        # Send to WebSocket subscribers
        for sub_id, sub in list(self.websocket_subscriptions.items()):
            if self._matches_filters(event, sub["filters"]):
                try:
                    await sub["ws"].send_json(event)
                    logger.debug(f"Sent event {event['eventId']} to WebSocket {sub_id}")
                except Exception as e:
                    logger.error(f"Error sending to WebSocket {sub_id}: {e}")
                    # Unregister broken WebSocket
                    await self.unregister_websocket(sub_id)

        # Send to webhook subscribers
        for hub_id, sub in self.webhook_subscriptions.items():
            if self._matches_filters(event, sub["filters"]):
                # Send webhook asynchronously (fire and forget)
                asyncio.create_task(self._send_webhook(sub["callback"], event, hub_id))

    def _matches_filters(self, event: Dict, filters: Dict[str, Any]) -> bool:
        """Check if event matches subscription filters

        Args:
            event: TMF688 Event dict
            filters: Subscription filters

        Returns:
            True if event matches all filters
        """

        # Filter by eventType (or eventTypes array)
        if "eventType" in filters:
            if event["eventType"] != filters["eventType"]:
                return False

        if "eventTypes" in filters:
            if isinstance(filters["eventTypes"], list):
                if event["eventType"] not in filters["eventTypes"]:
                    return False

        # Filter by resourceSpecification
        if "resourceSpecification" in filters:
            resource_type = event["event"]["resource"]["@type"]
            if isinstance(filters["resourceSpecification"], list):
                if resource_type not in filters["resourceSpecification"]:
                    return False
            elif resource_type != filters["resourceSpecification"]:
                return False

        # Filter by role (for PhysicalRouter)
        if "role" in filters:
            characteristics = event["event"]["resource"].get("resourceCharacteristic", [])
            role_char = next((c for c in characteristics if c["name"] == "role"), None)
            if not role_char or role_char["value"] != filters["role"]:
                return False

        # Filter by location_city
        if "location_city" in filters:
            characteristics = event["event"]["resource"].get("resourceCharacteristic", [])
            city_char = next((c for c in characteristics if c["name"] == "location_city"), None)
            if not city_char or city_char["value"] != filters["location_city"]:
                return False

        return True

    async def _send_webhook(self, callback_url: str, event: Dict, hub_id: str):
        """Send event to webhook callback URL

        Args:
            callback_url: Webhook callback URL
            event: TMF688 Event dict
            hub_id: Hub registration ID (for logging)
        """
        try:
            response = await self._http_client.post(callback_url, json=event)
            response.raise_for_status()
            logger.debug(f"Sent event {event['eventId']} to webhook {hub_id}: {callback_url}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Webhook {hub_id} ({callback_url}) returned {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Error sending webhook to {hub_id} ({callback_url}): {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending webhook to {hub_id}: {e}")

    async def close(self):
        """Close HTTP client and cleanup resources"""
        await self._http_client.aclose()
