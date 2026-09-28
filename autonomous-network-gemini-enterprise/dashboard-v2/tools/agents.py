# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from agent_library import get_credentials
import google.cloud.run as run_v2
import os

logger = logging.getLogger(__name__)

async def get_available_agents(project_id=os.getenv("GOOGLE_PROJECT"),
                           location=os.getenv("GOOGLE_REGION")) -> list:
    """
    List available network agents running as Cloud Run services,
    filters them by name, and returns an array of their names and URLs.

    Args:
        project_id: Your Google Cloud project ID.
        location: The region to list services from. Use "-" for all locations.

    Returns:
        A list of dictionaries, where each dictionary contains 'name' and 'url'
        for the matching Cloud Run services.
    """
    credentials,_ = get_credentials()
    logger.debug(credentials)
    client = run_v2.ServicesClient(credentials=credentials)
    parent = f"projects/{project_id}/locations/{location}"
    logger.info(f"Listing Cloud Run services in project '{project_id}' for location '{location}'...")

    # Initialize an empty list to store the results
    available_agents = []

    try:
        # List all services in the specified project and location
        # The API doesn't support complex server-side filtering like "contains"
        # and "NOT contains" directly in the ListServicesRequest.
        # So, we'll fetch all and filter client-side.
        request = run_v2.ListServicesRequest(parent=parent)
        page_result = client.list_services(request=request)

        for service in page_result:
            service_name = service.name.split("/")[-1] # Extract just the service name
            service_url = service.uri

            # Apply the filtering logic: name contains "agent" but not "supervisor"
            if "agent" in service_name and "supervisor" not in service_name:
                available_agents.append({
                    "name": service_name,
                    "url": service_url
                })

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        # You might want to handle specific exceptions like PermissionDenied

    return available_agents