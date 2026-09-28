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

"""Gitea webhook service for triggering Impact Agent analysis."""

import logging
import os
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from a2a_client import ImpactAgentClient
from gitea_client import GiteaClient

# Configure logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Gitea Impact Webhook")

# Environment configuration
#: Resource path of the agent that reviews the change, e.g.
#: projects/N/locations/R/reasoningEngines/ID. The agents are Reasoning
#: Engines, not Cloud Run services -- see ImpactAgentClient.
TRUST_AGENT_URL = os.getenv('TRUST_AGENT_URL')
GITEA_URL = os.getenv('GITEA_URL', '')  # Will be discovered dynamically if not set
GITEA_API_TOKEN = os.getenv('GITEA_API_TOKEN', '')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')

#: Branch the update branch is merged back into. services/gitea/seed.sh creates
#: every repo with default_branch 'main' and cuts 'update' from it, so 'main'
#: is the only correct default here; 'master' would 404 on PR creation.
BASE_BRANCH = os.getenv('GITEA_BASE_BRANCH', 'main')

if not TRUST_AGENT_URL:
    logger.warning("TRUST_AGENT_URL not set - webhook will fail")

# Gitea webhook payload models
class GiteaCommit(BaseModel):
    id: str
    message: str
    url: str
    author: dict

class GiteaRepository(BaseModel):
    name: str
    owner: dict
    full_name: str

class GiteaWebhookPayload(BaseModel):
    ref: str
    before: str
    after: str
    commits: list[GiteaCommit]
    repository: GiteaRepository
    pusher: dict


def get_gitea_url() -> str:
    """Get Gitea URL from the environment, or discover it from its address.

    install.sh sets GITEA_URL when Gitea is already up, so the discovery path
    only matters when the webhook was deployed first. Gitea runs on GKE behind a
    load balancer pinned to the reserved regional address ``gitea-ip``
    (services/gitea/deployment.tpl.yaml) -- there is no hostname, the address is
    the identity -- and this image has no gcloud, so it asks the Compute API.
    """
    if GITEA_URL:
        return GITEA_URL.rstrip('/')

    try:
        import google.auth
        import httpx
        from google.auth.transport.requests import Request

        credentials, project_id = google.auth.default()
        credentials.refresh(Request())
        project = os.getenv('GOOGLE_PROJECT', project_id)
        region = os.getenv('GOOGLE_REGION', 'us-central1')
        address = os.getenv('GITEA_ADDRESS_NAME', 'gitea-ip')

        response = httpx.get(
            f"https://compute.googleapis.com/compute/v1/projects/{project}"
            f"/regions/{region}/addresses/{address}",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        ip = (response.json().get('address') or '').strip()
        if ip:
            discovered_url = f"https://{ip}"
            logger.info(f"Discovered Gitea URL from reserved address: {discovered_url}")
            return discovered_url
    except Exception as e:
        logger.warning(f"Could not discover the Gitea address: {e}")

    raise Exception("GITEA_URL not set and could not be discovered")


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify Gitea webhook signature."""
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not set - skipping signature verification")
        return True

    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "gitea-trust-webhook",
        "trust_agent_configured": bool(TRUST_AGENT_URL)
    }


# services/gitea/seed.sh registers the hook at /webhook/gitea/trust, so that
# path has to work or every push 404s. /impact is kept as an alias because it
# is the documented path and may already be registered on a live Gitea.
@app.post("/webhook/gitea/trust")
@app.post("/webhook/gitea/impact")
async def gitea_webhook_handler(request: Request):
    """Handle Gitea webhook for design compliance and impact analysis.

    Triggered on push to update branch in network repo.
    """
    logger.info("Received Gitea webhook")

    # Get raw body for signature verification
    body = await request.body()

    # Verify webhook signature
    signature = request.headers.get('X-Gitea-Signature', '')
    if not verify_signature(body, signature):
        logger.error("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON payload
    try:
        payload_data = await request.json()
        payload = GiteaWebhookPayload(**payload_data)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Extract branch name from ref
    branch = payload.ref.split('/')[-1]

    logger.info(f"Push to branch '{branch}' in repo '{payload.repository.full_name}'")

    # Only process update branch
    if branch != "update":
        logger.info(f"Ignoring push to '{branch}' - only processing 'update' branch")
        return {"status": "ignored", "reason": f"Branch '{branch}' is not 'update'"}

    # Get commit information
    if not payload.commits:
        logger.info("No commits in push - ignoring")
        return {"status": "ignored", "reason": "No commits"}

    # Process the latest commit
    commit = payload.commits[-1]
    commit_sha = commit.id
    commit_message = commit.message

    logger.info(f"Processing commit {commit_sha[:8]}: {commit_message}")

    # Extract repository owner and name
    repo_owner = payload.repository.owner.get('username') or payload.repository.owner.get('login')
    repo_name = payload.repository.name

    try:
        # Initialize Gitea client
        gitea_url = get_gitea_url()
        gitea_client = GiteaClient(gitea_url, GITEA_API_TOKEN)

        # Fetch commit diff and files
        logger.info("Fetching commit diff from Gitea...")
        diff = await gitea_client.get_commit_diff(repo_owner, repo_name, commit_sha)
        changed_files = await gitea_client.get_commit_files(repo_owner, repo_name, commit_sha)

        if not changed_files:
            # Fallback: parse files from diff
            changed_files = []
            for line in diff.split('\n'):
                if line.startswith('diff --git'):
                    # Extract filename from diff line
                    parts = line.split()
                    if len(parts) >= 3:
                        file_path = parts[2].replace('a/', '')
                        changed_files.append(file_path)

        logger.info(f"Changed files: {changed_files}")

        # Call Trust Agent for analysis
        if not TRUST_AGENT_URL:
            raise Exception("TRUST_AGENT_URL not configured")

        logger.info("Calling Trust Agent for analysis...")
        trust_client = ImpactAgentClient(TRUST_AGENT_URL)

        analysis_result = await trust_client.analyze_changes(
            branch=branch,
            commit_sha=commit_sha,
            diff=diff,
            changed_files=changed_files
        )

        await trust_client.close()

        logger.info("Trust analysis completed successfully")

        # Format analysis as comment
        comment = f"""## 🛡️ Design Compliance & Impact Analysis

**Branch:** {branch}
**Commit:** {commit_sha[:8]}
**Validated by:** Trust Agent

### Analysis Results

{analysis_result}

---
*Automated design validation triggered by webhook*
"""

        # Post analysis to Pull Request
        logger.info("Finding or creating Pull Request for branch updates...")
        pr_number = None
        prs = await gitea_client.get_pull_requests(repo_owner, repo_name, state="open")
        for pr in prs:
            if pr.get("head", {}).get("ref") == branch and pr.get("base", {}).get("ref") == BASE_BRANCH:
                pr_number = pr.get("number")
                logger.info(f"Found existing open PR #{pr_number} for {branch}")
                break
        
        if not pr_number:
            logger.info(f"No open PR found for {branch}. Creating new PR...")
            new_pr = await gitea_client.create_pull_request(
                owner=repo_owner,
                repo=repo_name,
                title=f"Validate update branch compliance: {commit_sha[:8]}",
                body="This Pull Request was automatically created by the validation webhook to track trust and design compliance analysis.",
                head=branch,
                base=BASE_BRANCH
            )
            if new_pr:
                pr_number = new_pr.get("number")
        
        if pr_number:
            logger.info(f"Posting validation analysis to PR #{pr_number}...")
            await gitea_client.post_pr_comment(repo_owner, repo_name, pr_number, comment)

            # Auto-merge PR if Trust Agent recommends PROCEED (and not DO NOT PROCEED / PROCEED WITH CAUTION)
            verdict = analysis_result.upper()
            if "**PROCEED**" in verdict and "**DO NOT PROCEED**" not in verdict and "**PROCEED WITH CAUTION**" not in verdict:
                logger.info(f"Trust Agent recommended PROCEED. Auto-merging PR #{pr_number}...")
                merged = await gitea_client.merge_pull_request(repo_owner, repo_name, pr_number)
                if merged:
                    logger.info(f"PR #{pr_number} merged successfully.")
                else:
                    logger.error(f"Failed to merge PR #{pr_number} automatically.")
            else:
                logger.info(f"PR #{pr_number} requires manual review/merge (verdict not auto-mergeable).")
        else:
            logger.error("Could not find or create Pull Request to post comment.")

        return {
            "status": "success",
            "commit": commit_sha[:8],
            "branch": branch,
            "pr_number": pr_number,
            "analysis_posted": pr_number is not None
        }

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)

        # Try to post error comment to Gitea Pull Request
        try:
            error_comment = f"""## ⚠️ Design Compliance Validation Failed

**Branch:** {branch}
**Commit:** {commit_sha[:8]}

**Error:** {str(e)}

Please check the webhook service logs for details.

---
*Automated design validation triggered by webhook*
"""
            gitea_url = get_gitea_url()
            gitea_client = GiteaClient(gitea_url, GITEA_API_TOKEN)
            
            # Find or create PR
            pr_number = None
            prs = await gitea_client.get_pull_requests(repo_owner, repo_name, state="open")
            for pr in prs:
                if pr.get("head", {}).get("ref") == branch and pr.get("base", {}).get("ref") == BASE_BRANCH:
                    pr_number = pr.get("number")
                    break
            if not pr_number:
                new_pr = await gitea_client.create_pull_request(
                    owner=repo_owner,
                    repo=repo_name,
                    title=f"Validate update branch compliance: {commit_sha[:8]}",
                    body="This Pull Request was automatically created by the validation webhook to track trust and design compliance analysis.",
                    head=branch,
                    base=BASE_BRANCH
                )
                if new_pr:
                    pr_number = new_pr.get("number")
            
            if pr_number:
                await gitea_client.post_pr_comment(repo_owner, repo_name, pr_number, error_comment)
        except Exception as comment_error:
            logger.error(f"Failed to post error comment: {comment_error}")

        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = "0.0.0.0"

    logger.info(f"Starting Gitea webhook service on {host}:{port}")
    logger.info(f"Trust Agent URL: {TRUST_AGENT_URL}")

    uvicorn.run(app, host=host, port=port)
