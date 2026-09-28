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

"""Sanitization and validation utilities"""

import re
import ipaddress
from typing import Optional, Dict


def sanitize_service_name(name: str) -> str:
    """Sanitize service name for use as VRF name

    Args:
        name: Original service name

    Returns:
        Sanitized name (lowercase, alphanumeric + hyphens, max 32 chars)
    """
    # Convert to lowercase
    name = name.lower()

    # Replace spaces and underscores with hyphens
    name = name.replace(" ", "-").replace("_", "-")

    # Remove all non-alphanumeric characters except hyphens
    name = re.sub(r'[^a-z0-9-]', '', name)

    # Remove consecutive hyphens
    name = re.sub(r'-+', '-', name)

    # Remove leading/trailing hyphens
    name = name.strip('-')

    # Truncate to 32 characters
    name = name[:32]

    return name


def sanitize_cr_name(name: str) -> str:
    """Sanitize name for Kubernetes CR name (DNS-1123 subdomain)

    Args:
        name: Original name

    Returns:
        K8s-compliant CR name
    """
    # Same as service name for now
    sanitized = sanitize_service_name(name)

    # Ensure it starts with alphanumeric
    if sanitized and not sanitized[0].isalnum():
        sanitized = 'vpn-' + sanitized

    # Ensure it ends with alphanumeric
    if sanitized and not sanitized[-1].isalnum():
        sanitized = sanitized.rstrip('-')

    return sanitized


def validate_cidr(cidr: str) -> bool:
    """Validate CIDR notation

    Args:
        cidr: CIDR string (e.g., "10.100.0.0/24")

    Returns:
        True if valid, False otherwise
    """
    try:
        ipaddress.ip_network(cidr, strict=False)
        return True
    except (ValueError, TypeError):
        return False


def parse_cidr(cidr: str) -> Optional[Dict[str, str]]:
    """Parse CIDR into network information

    Args:
        cidr: CIDR string (e.g., "10.100.0.0/24")

    Returns:
        Dict with network_address, first_host, last_host, gateway, broadcast
        None if invalid CIDR
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)

        # Get usable host addresses
        hosts = list(network.hosts())

        if not hosts:
            # Handle /31 or /32 networks
            return {
                "network_address": str(network.network_address),
                "first_host": str(network.network_address),
                "last_host": str(network.broadcast_address),
                "gateway": str(network.broadcast_address),
                "broadcast": str(network.broadcast_address),
                "prefix_length": network.prefixlen,
                "netmask": str(network.netmask)
            }

        return {
            "network_address": str(network.network_address),
            "first_host": str(hosts[0]),
            "last_host": str(hosts[-1]),
            "gateway": str(hosts[-1]),  # Convention: last usable IP is gateway
            "broadcast": str(network.broadcast_address),
            "prefix_length": network.prefixlen,
            "netmask": str(network.netmask)
        }
    except (ValueError, TypeError):
        return None


def validate_ipv4(ip: str) -> bool:
    """Validate IPv4 address

    Args:
        ip: IP address string

    Returns:
        True if valid IPv4, False otherwise
    """
    try:
        ipaddress.IPv4Address(ip)
        return True
    except (ValueError, TypeError):
        return False


def validate_rd_format(rd: str) -> bool:
    """Validate Route Distinguisher format

    Args:
        rd: Route Distinguisher string (e.g., "10.255.1.1:200" or "65001:200")

    Returns:
        True if valid format, False otherwise
    """
    pattern = r'^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|[0-9]+):[0-9]+$'
    return bool(re.match(pattern, rd))


def validate_rt_format(rt: str) -> bool:
    """Validate Route Target format

    Args:
        rt: Route Target string (e.g., "65001:2000")

    Returns:
        True if valid format, False otherwise
    """
    pattern = r'^[0-9]+:[0-9]+$'
    return bool(re.match(pattern, rt))
