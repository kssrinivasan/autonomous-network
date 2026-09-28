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

import os
import logging
from typing import Optional, List, Dict, Any
import google.auth
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request

# Import Google Chat API Python Client if available
try:
    from google.apps import chat_v1
    HAS_CHAT_SDK = True
except ImportError:
    HAS_CHAT_SDK = False

import requests

logger = logging.getLogger(__name__)

# Configurable target space or webhook via environment variables
DEFAULT_SPACE_ID = os.environ.get("GOOGLE_CHAT_SPACE_ID", "")
DEFAULT_WEBHOOK_URL = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "")

# Service account to impersonate for Google Chat app authentication.
# Defaults to the runtime service account (self-impersonation).
CHAT_APP_SERVICE_ACCOUNT = os.environ.get("CHAT_APP_SERVICE_ACCOUNT", "")

# Card header image. This used to be a hardcoded URL in a bucket outside the
# project (gs://gnn-telco-tmp), which has since been deleted -- the card header
# has been rendering a broken image ever since. The bucket is now per-project
# and created by deploy_agents.py from agents-in-agent-runtime/assets/, so it
# exists wherever the agents do.
CARD_ICON_URL = os.environ.get(
    "AGENT_ICON_URL",
    "https://storage.googleapis.com/"
    f"{os.environ.get('GOOGLE_CLOUD_PROJECT', '')}-agent-assets/telco_icon.jpg",
)

# Message (app-auth) scope. Space creation additionally needs the chat.app.spaces scopes.
CHAT_MESSAGE_SCOPES = ["https://www.googleapis.com/auth/chat.bot"]
CHAT_SPACE_SCOPES = [
    "https://www.googleapis.com/auth/chat.app.spaces.create",
    "https://www.googleapis.com/auth/chat.app.spaces",
    "https://www.googleapis.com/auth/chat.bot",
]


def _runtime_service_account_email() -> str:
    """Resolve the runtime service account email (env override, else metadata)."""
    if CHAT_APP_SERVICE_ACCOUNT:
        return CHAT_APP_SERVICE_ACCOUNT
    try:
        from google.auth.compute_engine import _metadata
        info = _metadata.get_service_account_info(Request())
        return info.get("email", "")
    except Exception as e:
        logger.warning("Could not resolve runtime service account email: %s", e)
        return ""


def _chat_credentials(scopes: List[str]) -> Any:
    """
    Returns refreshed credentials scoped for the Google Chat API.

    On Vertex AI Agent Engine, Application Default Credentials come from the
    GCE metadata server, whose runtime service account is only granted the
    ``cloud-platform`` access scope. Google Chat scopes are Workspace scopes
    outside that grant, so requesting them via ``google.auth.default(scopes=...)``
    and refreshing fails with:

        400 ... Requested credential with invalid scopes.

    We instead self-impersonate the runtime service account through the IAM
    Credentials API (``generateAccessToken``), which can mint arbitrary OAuth
    scopes for the target service account. Prerequisites:
      * ``iamcredentials.googleapis.com`` enabled on the project
      * the runtime SA holds ``roles/iam.serviceAccountTokenCreator`` on itself
      * a Google Chat app configured against this SA (app authentication)
    """
    source_creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    sa_email = _runtime_service_account_email()
    if not sa_email:
        raise RuntimeError(
            "Unable to determine the runtime service account for Chat "
            "impersonation. Set CHAT_APP_SERVICE_ACCOUNT explicitly."
        )
    creds = impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=sa_email,
        target_scopes=scopes,
        lifetime=300,
    )
    creds.refresh(Request())
    return creds

def build_card_payload(
    title: str,
    message: str,
    status: str = "SUCCESS",
    resources_changed: Optional[List[str]] = None,
    gitops_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds a Google Chat CardsV2 payload consistent with Workspace Chat UI standards."""
    status_icon = "✅" if status.upper() == "SUCCESS" else "⚠️"
    widgets = [{"textParagraph": {"text": message}}]

    if resources_changed:
        resource_items = "<br/>".join([f"• <b>{r}</b>" for r in resources_changed])
        widgets.append({"textParagraph": {"text": f"<b>Modified Resources:</b><br/>{resource_items}"}})

    if gitops_commit:
        widgets.append({"textParagraph": {"text": f"<b>GitOps Commit:</b> <code>{gitops_commit}</code>"}})

    return {
        "card_id": "designer_completion_card",
        "card": {
            "header": {
                "title": f"{status_icon} {title}",
                "subtitle": "Network Designer Agent — gnn-telco",
                "image_url": CARD_ICON_URL,
                "image_type": "CIRCLE",
            },
            "sections": [{"header": "Topology & Change Details", "widgets": widgets}],
        },
    }

def create_space_on_the_fly(credentials: Any, space_title: str = "Network Designer Notifications") -> Optional[str]:
    """
    Creates a new Google Chat Space on the fly using Google Chat API CreateSpaceRequest.
    Ref: https://developers.google.com/workspace/chat/create-spaces
    """
    try:
        if HAS_CHAT_SDK:
            # Mint Chat App space-creation scopes via self-impersonation.
            space_creds = _chat_credentials(CHAT_SPACE_SCOPES)
            client = chat_v1.ChatServiceClient(credentials=space_creds)
            
            # Search existing spaces first
            try:
                for space in client.list_spaces():
                    if space.display_name == space_title:
                        logger.info("Found existing space: %s (%s)", space_title, space.name)
                        return space.name
            except Exception as search_err:
                logger.debug("Listing spaces skipped: %s", search_err)

            # Create space on the fly
            req = chat_v1.CreateSpaceRequest(
                space=chat_v1.Space(
                    display_name=space_title,
                    space_type=chat_v1.Space.SpaceType.SPACE,
                    customer="customers/my_customer",
                )
            )
            created_space = client.create_space(request=req)
            logger.info("Created new space on the fly: %s (%s)", space_title, created_space.name)
            return created_space.name
        else:
            url = "https://chat.googleapis.com/v1/spaces"
            headers = {
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            body = {
                "displayName": space_title,
                "spaceType": "SPACE",
                "customer": "customers/my_customer",
            }
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            if resp.status_code == 200:
                space_name = resp.json().get("name")
                logger.info("Created space via REST API on the fly: %s", space_name)
                return space_name
            else:
                logger.warning("REST create space failed (%s): %s", resp.status_code, resp.text)
                return None
    except Exception as e:
        logger.warning("Could not create space on the fly: %s", e)
        return None

def send_chat_message_api(
    space_name: str,
    title: str,
    message: str,
    status: str = "SUCCESS",
    resources_changed: Optional[List[str]] = None,
    gitops_commit: Optional[str] = None,
) -> str:
    """
    Sends a message using the official Google Chat API Python Client (google-apps-chat).
    Ref: https://developers.google.com/workspace/chat/create-messages
    Authenticated with Service Account Application Default Credentials (ADC).
    """
    try:
        credentials = _chat_credentials(CHAT_MESSAGE_SCOPES)

        # If space_name is empty, attempt to create a space on the fly!
        if not space_name:
            created_space = create_space_on_the_fly(credentials, "Network Designer Notifications")
            if created_space:
                space_name = created_space
            else:
                return "Error: GOOGLE_CHAT_SPACE_ID not set and dynamic space creation on the fly failed."

        space_name = space_name.strip()
        if not space_name.startswith("spaces/"):
            space_name = f"spaces/{space_name}"

        card_dict = build_card_payload(title, message, status, resources_changed, gitops_commit)
        
        if HAS_CHAT_SDK:
            client = chat_v1.ChatServiceClient(credentials=credentials)
            card_v2 = chat_v1.CardWithId(
                card_id=card_dict["card_id"],
                card=chat_v1.ContextualAddOnMarkup.Card(**card_dict["card"])
            )
            request = chat_v1.CreateMessageRequest(
                parent=space_name,
                message=chat_v1.Message(cards_v2=[card_v2])
            )
            response = client.create_message(request=request)
            logger.info("Successfully created message via Google Chat API SDK: %s", response.name)
            return f"Notification sent via Google Chat API to {space_name} (Message ID: {response.name})"
        else:
            url = f"https://chat.googleapis.com/v1/{space_name}/messages"
            headers = {
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            payload = {"cardsV2": [{"cardId": card_dict["card_id"], "card": card_dict["card"]}]}
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                msg_name = resp.json().get("name", "ok")
                return f"Notification sent via Google Chat REST API to {space_name} ({msg_name})"
            else:
                return f"Google Chat REST API error ({resp.status_code}): {resp.text}"
    except Exception as e:
        logger.exception("Failed to send message via Google Chat API")
        return f"Error sending Google Chat API message: {e}"

def send_chat_webhook(
    webhook_url: str,
    title: str,
    message: str,
    status: str = "SUCCESS",
    resources_changed: Optional[List[str]] = None,
    gitops_commit: Optional[str] = None,
) -> str:
    """
    Posts a CardsV2 notification to a Google Chat incoming webhook.

    Webhooks require no OAuth or scopes — the secret is embedded in the URL —
    so this path always works from Agent Engine. It can only post to the single
    space the webhook was created in (no dynamic space creation).
    Ref: https://developers.google.com/workspace/chat/quickstart/webhooks
    """
    card_dict = build_card_payload(title, message, status, resources_changed, gitops_commit)
    payload = {"cardsV2": [{"cardId": card_dict["card_id"], "card": card_dict["card"]}]}
    try:
        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json; charset=utf-8"},
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return "Notification sent via Google Chat webhook."
        return f"Google Chat webhook error ({resp.status_code}): {resp.text}"
    except Exception as ex:
        return f"Webhook error: {ex}"

def send_google_chat_notification(
    summary: str,
    title: str = "Network Topology Change Completed",
    status: str = "SUCCESS",
    resources_changed: Optional[List[str]] = None,
    gitops_commit: Optional[str] = None,
    space_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> str:
    """
    Main entrypoint for sending Google Chat notifications.

    Prefers an incoming webhook when configured (no OAuth/scopes, always works
    from Agent Engine). Falls back to the Chat API with app authentication
    (service-account self-impersonation) when only a space ID is available.
    """
    target_space = space_id or DEFAULT_SPACE_ID
    target_webhook = webhook_url or DEFAULT_WEBHOOK_URL

    if target_webhook:
        return send_chat_webhook(
            webhook_url=target_webhook,
            title=title,
            message=summary,
            status=status,
            resources_changed=resources_changed,
            gitops_commit=gitops_commit,
        )
    elif target_space:
        return send_chat_message_api(
            space_name=target_space,
            title=title,
            message=summary,
            status=status,
            resources_changed=resources_changed,
            gitops_commit=gitops_commit,
        )
    else:
        return "Warning: Neither GOOGLE_CHAT_WEBHOOK_URL nor GOOGLE_CHAT_SPACE_ID is configured."

