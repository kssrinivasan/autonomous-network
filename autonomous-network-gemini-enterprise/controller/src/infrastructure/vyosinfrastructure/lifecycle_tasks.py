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

import logging

logger = logging.getLogger(__name__)


def extract_routers_from_spec(spec: dict) -> list:
    """
    Extract the management-network router list from a VyOSInfrastructure spec.

    Returns a list of dicts::

        [{"name": "r1", "ip_address": "192.168.122.11"}, ...]

    Only routers connected to a network with ``network_type: management`` are
    included, because those IPs are reachable from the monitoring VM where the
    Ops Agent runs.
    """
    routers = []
    for network in spec.get("networks", []):
        if network.get("network_type") == "management":
            for cr in network.get("connected_routers", []):
                routers.append({
                    "name": cr["router_name"],
                    "ip_address": cr["ip_address"],
                })
    logger.info(routers)
    return routers


def extract_devices_from_spec(spec: dict) -> list:
    """
    Extract device name and management IP from a VyOSInfrastructure spec.

    Returns a list of dicts::

        [{"name": "dev1", "mgmt_ip": "192.168.122.50"}, ...]

    Only devices with both ``name`` and ``mgmt_ip`` set are included.
    These are the devices running traffic-agent in daemon mode; their
    Prometheus metrics endpoint is reachable on port 9091 via the
    management network from the monitoring VM.
    """
    devices = []
    for device in spec.get("devices", []):
        name = device.get("name", "")
        mgmt_ip = device.get("mgmt_ip", "")
        if name and mgmt_ip:
            devices.append({"name": name, "mgmt_ip": mgmt_ip})
    logger.info("Extracted %d device(s) for Ops Agent scrape config: %s",
                len(devices), [d["name"] for d in devices])
    return devices


