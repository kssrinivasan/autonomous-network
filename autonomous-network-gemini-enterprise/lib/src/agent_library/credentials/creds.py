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

import google.auth
import os
import logging

logger = logging.getLogger(__name__)

def get_credentials():
    """
    Get credentials from the environment.

    Returns:
        tuple: (credentials, project) from google.auth
    """
    logger.debug("get credentials")
    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
    # Try loading from file first
    if os.path.exists(creds_file):
        credentials, project = google.auth.load_credentials_from_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return credentials, project

    # Fallback to default credentials
    credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials, project
