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

"""Async Spanner client for topology queries with TMF639 resource mapping"""

import logging
from google.cloud.spanner_v1 import AsyncClient
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class SpannerTopologyClient:
    """Async Spanner client for topology queries

    Implements TMF639 Resource Inventory queries against transporttopology-db
    with security filtering (PE routers and customer interfaces only).
    """

    def __init__(self, instance_id: str, database_id: str):
        self.instance_id = instance_id
        self.database_id = database_id
        self._client = None
        self._database = None

    async def get_database(self):
        """Get or create async Spanner database connection"""
        if self._database is None:
            self._client = AsyncClient()
            instance = self._client.instance(self.instance_id)
            self._database = await instance.database(self.database_id)
            logger.info(f"Connected to Spanner: {self.instance_id}/{self.database_id}")
        return self._database

    async def query_resources(
        self,
        spec: Optional[str],
        status: Optional[str],
        filters: Dict[str, Any],
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict]:
        """Query resources with filtering

        Args:
            spec: PhysicalRouter, PhysicalInterface, L3VPNService (None = all types)
            status: Active, Available, InUse, etc.
            filters: Additional filters (role, location_city, router_id, interface_type)
            limit: Page size (max 100)
            offset: Page offset

        Returns:
            List of TMF639 Resource dicts
        """

        # Auto-detect resource type from filters if not specified
        if spec is None:
            # If role or location_city specified, assume PhysicalRouter
            if filters.get("role") or filters.get("location_city"):
                spec = "PhysicalRouter"
            # If router_id specified, assume PhysicalInterface
            elif filters.get("router_id"):
                spec = "PhysicalInterface"

        if spec == "PhysicalRouter":
            return await self._query_routers(status, filters, limit, offset)
        elif spec == "PhysicalInterface":
            return await self._query_interfaces(status, filters, limit, offset)
        elif spec == "L3VPNService":
            return await self._query_vpn_services(status, filters, limit, offset)
        else:
            # Query all resource types (only if no specific filters)
            routers = await self._query_routers(status, filters, limit, offset)
            interfaces = await self._query_interfaces(status, filters, limit, offset)
            vpns = await self._query_vpn_services(status, filters, limit, offset)
            all_resources = routers + interfaces + vpns
            # Apply limit/offset to combined results
            return all_resources[offset:offset + limit]

    async def _query_routers(
        self,
        status: Optional[str],
        filters: Dict[str, Any],
        limit: int,
        offset: int
    ) -> List[Dict]:
        """Query PhysicalRouter resources (PE routers only)

        SECURITY: Only expose PE routers via WHERE role='PE'
        """

        db = await self.get_database()

        # Build WHERE clause - CRITICAL: Only PE routers
        where_clauses = ["valid_end_ts IS NULL", "role = 'PE'"]

        # Map TMF639 resourceStatus to Spanner status
        if status:
            if status == "Active":
                where_clauses.append("status = 'Ready'")
            elif status == "Suspended":
                where_clauses.append("status = 'Error'")
            elif status == "Standby":
                where_clauses.append("status = 'Maintenance'")

        if filters.get("location_city"):
            where_clauses.append(f"location_city = '{filters['location_city']}'")

        if filters.get("role") and filters["role"] != "PE":
            # Requested non-PE routers - return empty list (security filter)
            return []

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT
                id, name, role, status, location_city, location_lat, location_lon,
                valid_start_ts
            FROM PhysicalRouter
            WHERE {where_sql}
            ORDER BY name
            LIMIT {limit} OFFSET {offset}
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_router_to_tmf639(row))

        return resources

    def _map_router_to_tmf639(self, row) -> Dict:
        """Map Spanner PhysicalRouter row to TMF639 Resource"""

        router_id = row[0]
        characteristics = [
            {"name": "role", "value": row[2]},
            {"name": "status", "value": row[3]}
        ]

        if row[4]:  # location_city
            characteristics.append({"name": "location_city", "value": row[4]})
        if row[5] is not None:  # location_lat
            characteristics.append({"name": "location_lat", "value": row[5]})
        if row[6] is not None:  # location_lon
            characteristics.append({"name": "location_lon", "value": row[6]})

        # Map Spanner status to TMF639 resourceStatus
        spanner_status = row[3]
        if spanner_status == "Ready":
            resource_status = "Active"
        elif spanner_status == "Error":
            resource_status = "Suspended"
        elif spanner_status == "Maintenance":
            resource_status = "Standby"
        else:
            resource_status = "Active"  # Default

        return {
            "@type": "PhysicalRouter",
            "id": router_id,
            "href": f"/tmf-api/resourceInventoryManagement/v4/transport/resource/{router_id}",
            "name": row[1],
            "resourceSpecification": {
                "id": "PhysicalRouter",
                "name": "Physical Router"
            },
            "resourceStatus": resource_status,
            "resourceCharacteristic": characteristics,
            "validFor": {
                "startDateTime": row[7].isoformat() if row[7] else None,
                "endDateTime": None
            }
        }

    async def _query_interfaces(
        self,
        status: Optional[str],
        filters: Dict[str, Any],
        limit: int,
        offset: int
    ) -> List[Dict]:
        """Query PhysicalInterface resources on PE routers

        NOTE: The Spanner schema doesn't have interface_type column.
        All interfaces on PE routers are exposed.
        """

        db = await self.get_database()

        # Build WHERE clause
        where_clauses = [
            "i.valid_end_ts IS NULL",
            "r.role = 'PE'"  # Only interfaces on PE routers
        ]

        # Map TMF639 resourceStatus to Spanner fields
        if status:
            if status == "Available":
                where_clauses.append("i.status = 'UP'")
            elif status == "InUse":
                where_clauses.append("i.status = 'DOWN'")  # Approximation

        if filters.get("router_id"):
            where_clauses.append(f"i.router_id = '{filters['router_id']}'")

        # Note: interface_type filter ignored - column doesn't exist in schema

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT
                i.id, i.name, i.router_id, i.status, i.ip_address,
                i.speed, i.valid_start_ts,
                r.name AS router_name
            FROM PhysicalInterface i
            JOIN PhysicalRouter r ON i.router_id = r.id AND r.valid_end_ts IS NULL
            WHERE {where_sql}
            ORDER BY i.name
            LIMIT {limit} OFFSET {offset}
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_interface_to_tmf639(row))

        return resources

    def _map_interface_to_tmf639(self, row) -> Dict:
        """Map Spanner PhysicalInterface row to TMF639 Resource

        Row columns: id, name, router_id, status, ip_address, speed, valid_start_ts, router_name
        """

        interface_id = row[0]
        router_id = row[2]
        status = row[3]

        # Map Spanner status to TMF639 resourceStatus
        # UP = Available, DOWN = InUse (approximation)
        if status == "UP":
            resource_status = "Available"
        else:
            resource_status = "InUse"

        characteristics = [
            {"name": "router_id", "value": router_id},
            {"name": "status", "value": status}
        ]

        if row[4]:  # ip_address
            characteristics.append({"name": "ip_address", "value": row[4]})
        if row[5]:  # speed
            characteristics.append({"name": "speed", "value": row[5]})

        return {
            "@type": "PhysicalInterface",
            "id": interface_id,
            "href": f"/tmf-api/resourceInventoryManagement/v4/transport/resource/{interface_id}",
            "name": row[1],
            "resourceSpecification": {
                "id": "PhysicalInterface",
                "name": "Physical Interface"
            },
            "resourceStatus": resource_status,
            "resourceCharacteristic": characteristics,
            "relatedParty": [
                {
                    "id": router_id,
                    "href": f"/tmf-api/resourceInventoryManagement/v4/transport/resource/{router_id}",
                    "role": "parent",
                    "name": row[7]  # router_name
                }
            ],
            "validFor": {
                "startDateTime": row[6].isoformat() if row[6] else None,
                "endDateTime": None
            }
        }

    async def _query_vpn_services(
        self,
        status: Optional[str],
        filters: Dict[str, Any],
        limit: int,
        offset: int
    ) -> List[Dict]:
        """Query L3VPNService resources"""

        db = await self.get_database()

        where_clauses = ["vpn.valid_end_ts IS NULL"]

        if status:
            where_clauses.append(f"vpn.status = '{status}'")

        where_sql = " AND ".join(where_clauses)

        # Count endpoints per VPN
        query = f"""
            SELECT
                vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts,
                COUNT(vrf.id) AS endpoint_count
            FROM L3VPNService vpn
            LEFT JOIN VRF vrf ON vpn.id = vrf.vpn_id AND vrf.valid_end_ts IS NULL
            WHERE {where_sql}
            GROUP BY vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts
            ORDER BY vpn.name
            LIMIT {limit} OFFSET {offset}
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_vpn_to_tmf639(row))

        return resources

    def _map_vpn_to_tmf639(self, row) -> Dict:
        """Map Spanner L3VPNService row to TMF639 Resource"""

        vpn_id = row[0]

        return {
            "@type": "L3VPNService",
            "id": vpn_id,
            "href": f"/tmf-api/resourceInventoryManagement/v4/transport/resource/{vpn_id}",
            "name": row[1],
            "resourceSpecification": {
                "id": "L3VPNService",
                "name": "L3 VPN Service"
            },
            "resourceStatus": row[3],  # Ready, InProgress, Error, Inactive
            "resourceCharacteristic": [
                {"name": "topology", "value": row[2]},
                {"name": "endpoint_count", "value": row[5]}
            ],
            "validFor": {
                "startDateTime": row[4].isoformat() if row[4] else None,
                "endDateTime": None
            }
        }

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict]:
        """Get specific resource by ID

        SECURITY: Enforce visibility rules even for direct ID lookups
        """

        # Determine resource type from ID prefix
        if resource_id.startswith("vpn:"):
            return await self._get_vpn_by_id(resource_id)
        elif ":interface:" in resource_id:
            return await self._get_interface_by_id(resource_id)
        elif resource_id.startswith("router:"):
            return await self._get_router_by_id(resource_id)
        else:
            return None

    async def _get_router_by_id(self, router_id: str) -> Optional[Dict]:
        """Get router by ID - SECURITY: Only return PE routers"""

        db = await self.get_database()

        query = f"""
            SELECT
                id, name, role, status, location_city, location_lat, location_lon,
                valid_start_ts
            FROM PhysicalRouter
            WHERE id = '{router_id}' AND valid_end_ts IS NULL AND role = 'PE'
        """

        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                return self._map_router_to_tmf639(row)

        return None  # Not found or not a PE router

    async def _get_interface_by_id(self, interface_id: str) -> Optional[Dict]:
        """Get interface by ID on PE routers only"""

        db = await self.get_database()

        query = f"""
            SELECT
                i.id, i.name, i.router_id, i.status, i.ip_address,
                i.speed, i.valid_start_ts,
                r.name AS router_name
            FROM PhysicalInterface i
            JOIN PhysicalRouter r ON i.router_id = r.id AND r.valid_end_ts IS NULL
            WHERE i.id = '{interface_id}' AND i.valid_end_ts IS NULL AND r.role = 'PE'
        """

        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                return self._map_interface_to_tmf639(row)

        return None  # Not found or not a customer interface

    async def _get_vpn_by_id(self, vpn_id: str) -> Optional[Dict]:
        """Get VPN service by ID"""

        db = await self.get_database()

        query = f"""
            SELECT
                vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts,
                COUNT(vrf.id) AS endpoint_count
            FROM L3VPNService vpn
            LEFT JOIN VRF vrf ON vpn.id = vrf.vpn_id AND vrf.valid_end_ts IS NULL
            WHERE vpn.id = '{vpn_id}' AND vpn.valid_end_ts IS NULL
            GROUP BY vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts
        """

        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                return self._map_vpn_to_tmf639(row)

        return None

    async def get_changes_since(self, timestamp: datetime, resource_type: str) -> List[Dict]:
        """Get resources created or modified since timestamp

        Used by background event poller to detect topology changes.

        Args:
            timestamp: Query resources with valid_start_ts > this timestamp
            resource_type: PhysicalRouter, PhysicalInterface, L3VPNService

        Returns:
            List of changed resources as TMF639 dicts
        """

        if resource_type == "PhysicalRouter":
            return await self._get_router_changes_since(timestamp)
        elif resource_type == "PhysicalInterface":
            return await self._get_interface_changes_since(timestamp)
        elif resource_type == "L3VPNService":
            return await self._get_vpn_changes_since(timestamp)
        else:
            return []

    async def _get_router_changes_since(self, timestamp: datetime) -> List[Dict]:
        """Get PE router changes since timestamp"""

        db = await self.get_database()

        query = f"""
            SELECT
                id, name, role, status, location_city, location_lat, location_lon,
                valid_start_ts
            FROM PhysicalRouter
            WHERE valid_end_ts IS NULL
              AND role = 'PE'
              AND valid_start_ts > TIMESTAMP '{timestamp.isoformat()}'
            ORDER BY valid_start_ts
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_router_to_tmf639(row))

        return resources

    async def _get_interface_changes_since(self, timestamp: datetime) -> List[Dict]:
        """Get interface changes on PE routers since timestamp"""

        db = await self.get_database()

        query = f"""
            SELECT
                i.id, i.name, i.router_id, i.status, i.ip_address,
                i.speed, i.valid_start_ts,
                r.name AS router_name
            FROM PhysicalInterface i
            JOIN PhysicalRouter r ON i.router_id = r.id AND r.valid_end_ts IS NULL
            WHERE i.valid_end_ts IS NULL
              AND r.role = 'PE'
              AND i.valid_start_ts > TIMESTAMP '{timestamp.isoformat()}'
            ORDER BY i.valid_start_ts
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_interface_to_tmf639(row))

        return resources

    async def _get_vpn_changes_since(self, timestamp: datetime) -> List[Dict]:
        """Get VPN service changes since timestamp"""

        db = await self.get_database()

        query = f"""
            SELECT
                vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts,
                COUNT(vrf.id) AS endpoint_count
            FROM L3VPNService vpn
            LEFT JOIN VRF vrf ON vpn.id = vrf.vpn_id AND vrf.valid_end_ts IS NULL
            WHERE vpn.valid_end_ts IS NULL
              AND vpn.valid_start_ts > TIMESTAMP '{timestamp.isoformat()}'
            GROUP BY vpn.id, vpn.name, vpn.topology, vpn.status, vpn.valid_start_ts
            ORDER BY vpn.valid_start_ts
        """

        resources = []
        async with db.snapshot() as snapshot:
            result_set = await snapshot.execute_sql(query)

            async for row in result_set:
                resources.append(self._map_vpn_to_tmf639(row))

        return resources
