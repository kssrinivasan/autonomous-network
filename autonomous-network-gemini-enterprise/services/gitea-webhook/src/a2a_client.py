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

"""REST client for calling the Trust Agent Reasoning Engine."""

import logging
import httpx
import google.auth
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)


class ImpactAgentClient:
    """Client for communicating with the Trust Agent via Vertex AI REST API."""

    def __init__(self, agent_url: str):
        """Initialize the Trust Agent client.

        Args:
            agent_url: Resource name of the Trust Agent reasoning engine
                       (e.g., projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_AGENT_ENGINE_ID)
        """
        self.agent_url = agent_url.strip('/')

    async def analyze_changes(self, branch: str, commit_sha: str, diff: str,
                              changed_files: list[str]) -> str:
        """Request impact analysis from the Trust Agent.

        Args:
            branch: Git branch name
            commit_sha: Commit SHA
            diff: Git diff content
            changed_files: List of changed file paths

        Returns:
            Analysis result as text

        Raises:
            Exception: If agent communication fails
        """
        # Build analysis prompt
        file_list = "\n".join([f"  - {f}" for f in changed_files])
        prompt = f"""Analyze the impact of these changes to the network repository:

**Branch:** {branch}
**Commit:** {commit_sha}
**Changed Files:**
{file_list}

**Diff:**
```
{diff}
```

Please assess:
1. Impact on network topology and configuration
2. Potential service disruptions or conflicts
3. Configuration validation issues
4. Recommendations for safe deployment

Provide a clear, structured analysis."""

        logger.info(f"Acquiring GCP credentials for calling Trust Agent Reasoning Engine...")
        credentials, project_id = google.auth.default()
        
        # Get location from agent_url
        # Path format: projects/{project}/locations/{location}/reasoningEngines/{id}
        parts = self.agent_url.split('/')
        if len(parts) >= 4 and parts[2] == 'locations':
            region = parts[3]
        else:
            region = 'europe-west3' # default fallback
            
        auth_request = Request()
        credentials.refresh(auth_request)
        
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json"
        }
        
        url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{self.agent_url}:query"
        logger.info(f"Calling Reasoning Engine query endpoint: {url}")
        
        payload = {
            "classMethod": "query",
            "input": {
                "request": prompt
            }
        }
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code != 200:
                    logger.error(f"Reasoning Engine returned HTTP {response.status_code}: {response.text}")
                    raise Exception(f"Trust Agent returned HTTP {response.status_code}")
                    
                data = response.json()
                output = data.get("output")
                if not output:
                    raise Exception(f"Unexpected response format: {data}")
                    
                return str(output)
                
            except httpx.HTTPError as e:
                logger.error(f"HTTP error communicating with Trust Agent: {e}")
                raise Exception(f"Failed to communicate with Trust Agent: {e}")

    async def close(self):
        """No-op close method to satisfy webhook caller expectations."""
        pass
