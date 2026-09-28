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

"""Utility modules for transport orchestrator"""

from .sanitizers import (
    sanitize_service_name,
    sanitize_cr_name,
    validate_cidr,
    parse_cidr,
    validate_ipv4,
    validate_rd_format,
    validate_rt_format
)
from .resource_pools import get_resource_manager
from .spanner_client import get_spanner_client
from .a2a_response import (
    format_create_response,
    format_status_response,
    format_delete_response
)

__all__ = [
    "sanitize_service_name",
    "sanitize_cr_name",
    "validate_cidr",
    "parse_cidr",
    "validate_ipv4",
    "validate_rd_format",
    "validate_rt_format",
    "get_resource_manager",
    "get_spanner_client",
    "format_create_response",
    "format_status_response",
    "format_delete_response",
]
