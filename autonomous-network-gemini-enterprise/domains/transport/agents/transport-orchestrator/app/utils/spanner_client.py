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

"""Spanner client for infrastructure topology queries"""

import logging
import json
from typing import Optional, Dict
from google.cloud import spanner
from google.cloud.spanner_v1 import param_types
from ..config import GOOGLE_PROJECT, SPANNER_INSTANCE, SPANNER_TRANSPORT_DB

logger = logging.getLogger(__name__)


class SpannerClient:
    """Spanner client for transport topology queries"""

    def __init__(self):
        """Initialize Spanner client"""
        self.client = spanner.Client(project=GOOGLE_PROJECT, disable_builtin_metrics=True)
        self.instance = self.client.instance(SPANNER_INSTANCE)
        self.database = self.instance.database(SPANNER_TRANSPORT_DB)

    def get_pe_router(self, name: str) -> Optional[Dict]:
        """Get PE router information from Spanner

        Args:
            name: Router name

        Returns:
            Router dict with loopback_ip, role, config or None if not found
        """
        return self._get_router(name, role="PE")

    def get_ce_router(self, name: str) -> Optional[Dict]:
        """Get CE router information from Spanner

        Args:
            name: Router name

        Returns:
            Router dict with bgp_as, router_id, config or None if not found
        """
        return self._get_router(name, role="CE")

    def check_router_exists(self, name: str, role: Optional[str] = None) -> bool:
        """Check if router exists in Spanner

        Args:
            name: Router name
            role: Optional role filter (PE, CE, P, RR)

        Returns:
            True if router exists, False otherwise
        """
        router = self._get_router(name, role)
        return router is not None

    def _get_router(self, name: str, role: Optional[str] = None) -> Optional[Dict]:
        """Get router from Spanner (current version)

        Args:
            name: Router name
            role: Optional role filter

        Returns:
            Router dict or None if not found
        """
        query = """
            SELECT id, name, role, config
            FROM PhysicalRouter
            WHERE name = @name
              AND valid_end_ts IS NULL
        """
        params = {"name": name}
        param_types_dict = {"name": param_types.STRING}

        if role:
            query += " AND role = @role"
            params["role"] = role
            param_types_dict["role"] = param_types.STRING

        query += " LIMIT 1"

        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                query, params=params, param_types=param_types_dict
            )
            rows = list(results)

        if not rows:
            logger.debug(f"Router {name} (role={role}) not found in Spanner")
            return None

        row = rows[0]
        config = json.loads(row[3]) if row[3] else {}

        # Extract loopback IP and BGP AS from config
        loopback_ip = self._extract_loopback_ip(config)
        bgp_as = self._extract_bgp_as(config)
        router_id = self._extract_router_id(config)

        return {
            "id": row[0],
            "name": row[1],
            "role": row[2],
            "config": config,
            "loopback_ip": loopback_ip,
            "bgp_as": bgp_as,
            "router_id": router_id
        }

    def _extract_loopback_ip(self, config: Dict) -> Optional[str]:
        """Extract loopback IP from router config

        Args:
            config: Router config JSON

        Returns:
            Loopback IP address or None
        """
        # Try spec.interfaces[] for loopback interface
        spec = config.get("spec", {})
        interfaces = spec.get("interfaces", [])

        for iface in interfaces:
            if iface.get("name", "").lower().startswith("lo"):
                return iface.get("address")

        # Try protocols.ospf.router_id as fallback
        protocols = spec.get("protocols", {})
        ospf = protocols.get("ospf", {})
        router_id = ospf.get("router_id")

        if router_id:
            return router_id

        return None

    def _extract_bgp_as(self, config: Dict) -> Optional[int]:
        """Extract BGP AS number from router config

        Args:
            config: Router config JSON

        Returns:
            BGP AS number or None
        """
        spec = config.get("spec", {})
        protocols = spec.get("protocols", {})
        bgp = protocols.get("bgp", {})

        as_number = bgp.get("as_number")
        if as_number:
            return int(as_number)

        return None

    def _extract_router_id(self, config: Dict) -> Optional[str]:
        """Extract router ID from router config

        Args:
            config: Router config JSON

        Returns:
            Router ID or None
        """
        spec = config.get("spec", {})
        protocols = spec.get("protocols", {})

        # Try BGP router_id first
        bgp = protocols.get("bgp", {})
        router_id = bgp.get("router_id")
        if router_id:
            return router_id

        # Try OSPF router_id
        ospf = protocols.get("ospf", {})
        router_id = ospf.get("router_id")
        if router_id:
            return router_id

        return None

    def get_interface_status(self, router_name: str, interface_name: str) -> Optional[str]:
        """Get interface status from Spanner

        Args:
            router_name: Router name
            interface_name: Interface name

        Returns:
            Interface status (up, down, admin-down) or None if not found
        """
        query = """
            SELECT i.status
            FROM PhysicalInterface i
            JOIN PhysicalRouter r ON i.router_id = r.id
            WHERE r.name = @router_name
              AND i.name = @interface_name
              AND r.valid_end_ts IS NULL
              AND i.valid_end_ts IS NULL
            LIMIT 1
        """
        params = {
            "router_name": router_name,
            "interface_name": interface_name
        }
        param_types_dict = {
            "router_name": param_types.STRING,
            "interface_name": param_types.STRING
        }

        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                query, params=params, param_types=param_types_dict
            )
            rows = list(results)

        if not rows:
            logger.debug(
                f"Interface {interface_name} on {router_name} not found in Spanner"
            )
            return None

        return rows[0][0]


# Global instance
_spanner_client = None


def get_spanner_client() -> SpannerClient:
    """Get or create global SpannerClient instance"""
    global _spanner_client
    if _spanner_client is None:
        _spanner_client = SpannerClient()
    return _spanner_client
