#!/usr/bin/env python3
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
import sys
import json
import argparse
import subprocess
from string import Template
import google.auth
from google.auth.transport.requests import Request
import requests

# Ensure uv uses PyPI default index
os.environ["UV_DEFAULT_INDEX"] = "https://pypi.org/simple"
os.environ["UV_INDEX_URL"] = "https://pypi.org/simple"


# Authoritative Runtime Variables Configuration
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
VARS_FILE = os.path.join(SCRIPT_DIR, "runtime_variables.json")

try:
    with open(VARS_FILE, "r") as vf:
        rvars = json.load(vf)
    GCP_PROJECT = rvars["GCP_PROJECT"]
    GCP_REGION = rvars["GCP_REGION"]
    SERVICE_ACCOUNT = rvars["SERVICE_ACCOUNT"]
    AGENT_MCP_TOOLS_ADDRESS = rvars["AGENT_MCP_TOOLS_ADDRESS"]
    GEMINI_APP_ID = rvars["GEMINI_APP_ID"]
except Exception as e:
    print(f"❌ Error: Could not load orchestration constants from {VARS_FILE}: {e}")
    sys.exit(1)

def discover_gitea_url():
    """Return the base URL of the Gitea instance, or '' if it is not up.

    Gitea runs on GKE behind a load balancer pinned to the reserved regional
    address ``gitea-ip`` (services/gitea/deployment.tpl.yaml), so the address is
    the identity -- there is no hostname and no Cloud Run service to describe.
    It moved off Cloud Run in migration plan 7.2 so its SQLite database and
    repositories could live on a persistent disk.

    Empty is not fatal -- the helpers fall back to discovering it themselves at
    call time.
    """
    try:
        result = subprocess.run(
            ["gcloud", "compute", "addresses", "describe",
             os.getenv("GITEA_ADDRESS_NAME", "gitea-ip"),
             f"--project={GCP_PROJECT}", f"--region={GCP_REGION}",
             "--format=value(address)"],
            capture_output=True, text=True, check=True,
        )
        ip = result.stdout.strip()
        return f"https://{ip}" if ip else ""
    except Exception as e:
        print(f"⚠️ Warning: could not read the Gitea address: {e}")
        return ""


# Where the Gemini Enterprise tiles (and the designer's Google Chat card) fetch
# their icon from. Per-project and public-read, because neither GE nor Chat
# presents a credential when it renders the image.
ICON_OBJECT = "telco_icon.jpg"
ICON_BUCKET = f"{GCP_PROJECT}-agent-assets"
ICON_URL = f"https://storage.googleapis.com/{ICON_BUCKET}/{ICON_OBJECT}"


def ensure_icon_asset():
    """Publish assets/telco_icon.jpg to the project's public asset bucket.

    The icon was previously hardcoded to gs://gnn-telco-tmp, a bucket in a
    different project that has since been deleted -- so every registration
    pointed at a 404 and the agent tiles rendered blank. Serving it from a
    bucket this script creates means a fresh project gets the branding without
    anyone having to remember to upload anything.

    Never fatal. A missing icon is cosmetic, and a project whose org policy
    forbids public objects should still be able to deploy its agents.
    """
    src = os.path.join(SCRIPT_DIR, "assets", ICON_OBJECT)
    if not os.path.exists(src):
        print(f"⚠️  Icon asset not found at {src}; leaving the icon URL unpublished.")
        return

    def gcloud(*args):
        return subprocess.run(["gcloud", *args], capture_output=True, text=True)

    describe = gcloud("storage", "buckets", "describe", f"gs://{ICON_BUCKET}",
                      f"--project={GCP_PROJECT}", "--format=value(name)")
    if describe.returncode != 0:
        print(f"🪣 Creating agent asset bucket gs://{ICON_BUCKET}...")
        created = gcloud("storage", "buckets", "create", f"gs://{ICON_BUCKET}",
                         f"--project={GCP_PROJECT}", f"--location={GCP_REGION}",
                         "--uniform-bucket-level-access",
                         "--no-public-access-prevention")
        if created.returncode != 0:
            print(f"⚠️  Could not create gs://{ICON_BUCKET}: {created.stderr.strip()}")
            return
        # Only bind allUsers on a bucket we just created, so this never widens
        # access on a pre-existing bucket that someone locked down deliberately.
        public = gcloud("storage", "buckets", "add-iam-policy-binding",
                        f"gs://{ICON_BUCKET}", "--member=allUsers",
                        "--role=roles/storage.objectViewer",
                        f"--project={GCP_PROJECT}")
        if public.returncode != 0:
            print(f"⚠️  Could not make gs://{ICON_BUCKET} public: {public.stderr.strip()}")

    uploaded = gcloud("storage", "cp", src, f"gs://{ICON_BUCKET}/{ICON_OBJECT}",
                      "--content-type=image/jpeg")
    if uploaded.returncode != 0:
        print(f"⚠️  Could not upload the agent icon: {uploaded.stderr.strip()}")
    else:
        print(f"🎨 Agent icon published at {ICON_URL}")


def run_cmd(cmd, cwd=None, check=True):
    """Utility to run shell commands and display stdout/stderr in real-time."""
    print(f"\n🚀 Running: {' '.join(cmd)} in {cwd or '.'}")
    process = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while True:
        output = process.stdout.readline()
        if output == '' and process.poll() is not None:
            break
        if output:
            print(output.strip())
    rc = process.poll()
    if rc != 0 and check:
        print(f"❌ Command failed with return code {rc}")
        sys.exit(rc)
    return rc

def is_agent_registered(display_name):
    """Checks if an agent registration matching display_name already exists in Gemini Enterprise."""
    try:
        credentials, _ = google.auth.default()
        credentials.refresh(Request())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "x-goog-user-project": GCP_PROJECT,
            "Content-Type": "application/json",
        }
        parts = GEMINI_APP_ID.split("/")
        list_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{parts[1]}/locations/{parts[3]}/collections/{parts[5]}/engines/{parts[7]}/assistants/default_assistant/agents"
        resp = requests.get(list_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            for agent in resp.json().get("agents", []):
                if agent.get("displayName") == display_name:
                    return True
    except Exception as e:
        print(f"⚠️ Warning: Check for existing agent failed: {e}")
    return False

def update_gemini_metadata(agent_runtime_id, config):
    """Utility to query Gemini Enterprise agent list, find the agent, and PATCH description/icon."""
    print(f"\n🔒 Acquiring Google Cloud credentials for metadata patching...")
    credentials, project_id = google.auth.default()
    credentials.refresh(Request())
    access_token = credentials.token

    headers = {
        "Authorization": f"Bearer {access_token}",
        "x-goog-user-project": GCP_PROJECT,
        "Content-Type": "application/json",
    }

    # Extract info from App ID
    parts = GEMINI_APP_ID.split("/")
    project_number = parts[1]
    location = parts[3]
    collection = parts[5]
    engine_id = parts[7]

    base_url = "https://discoveryengine.googleapis.com/v1alpha"
    list_url = f"{base_url}/projects/{project_number}/locations/{location}/collections/{collection}/engines/{engine_id}/assistants/default_assistant/agents"

    print("🔍 Fetching registered agents in Gemini Enterprise to apply metadata...")
    response = requests.get(list_url, headers=headers, timeout=30)
    response.raise_for_status()
    agents_list = response.json().get("agents", [])

    existing_agent = None
    for agent in agents_list:
        if agent.get("displayName") == config["displayName"]:
            existing_agent = agent
            break

    if not existing_agent:
        print(f"⚠️  Could not find registered agent matching display name '{config['displayName']}' in Gemini Enterprise.")
        return

    agent_name = existing_agent["name"]
    update_url = f"{base_url}/{agent_name}"

    # Build PATCH payload
    payload = {
        "displayName": config["displayName"],
        "description": config["description"],
        "icon": {
            "uri": config.get("icon_url", "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/smart_toy/default/24px.svg")
        },
        "adkAgentDefinition": {
            "toolSettings": {
                "toolDescription": config.get("tool_description", "AI Agent tool description")
            },
            "provisionedReasoningEngine": {
                "reasoningEngine": agent_runtime_id
            }
        }
    }

    print(f"📝 Applying custom details to '{config['displayName']}'...")
    print(f"   Icon: {payload['icon']['uri']}")
    patch_response = requests.patch(update_url, headers=headers, json=payload, timeout=30)
    patch_response.raise_for_status()
    print(f"✅ Successfully updated agent registration metadata!")

def main():
    parser = argparse.ArgumentParser(
        description="Two-Step Orchestration Script: Deploy to Vertex AI & Register/Patch Gemini Enterprise."
    )
    parser.add_argument(
        "agents",
        nargs="*",
        help="Optional specific agent folder names to target (e.g. logsagent). If omitted, targets all agents."
    )
    parser.add_argument(
        "-d", "--deploy",
        action="store_true",
        help="Run only Step 1: Install packages and Deploy agent to Vertex AI Agent Runtime (Reasoning Engine)."
    )
    parser.add_argument(
        "-r", "--register",
        action="store_true",
        help="Run only Step 2: Register/Publish agent to Gemini Enterprise and patch icon/description metadata."
    )
    args = parser.parse_args()

    # Determine execution mode (default to both if neither is passed)
    run_deploy = args.deploy or not (args.deploy or args.register)
    run_register = args.register or not (args.deploy or args.register)

    root_dir = os.path.dirname(os.path.realpath(__file__))
    subdirs = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]

    all_agents = []
    for d in subdirs:
        agent_path = os.path.join(root_dir, d)
        manifest_path = os.path.join(agent_path, "agents-cli-manifest.yaml")
        if os.path.exists(manifest_path):
            all_agents.append(d)

    if not all_agents:
        print("ℹ️  No subfolder agents found under agents-in-agent-runtime/.")
        sys.exit(0)

    # Sort agents and ensure supervisor is always last so it receives all peer engine IDs
    all_agents.sort()
    if "supervisor" in all_agents:
        all_agents.remove("supervisor")
        all_agents.append("supervisor")

    # Filter by targeted agents
    if args.agents:
        agents = [a for a in all_agents if a in args.agents]
        if not agents:
            print(f"❌ Error: None of the specified agents {args.agents} were found. Available: {all_agents}")
            sys.exit(1)
        if "supervisor" in agents and len(agents) > 1:
            agents.remove("supervisor")
            agents.append("supervisor")
    else:
        agents = all_agents

    # Print execution steps
    print(f"📋 Target agents: {', '.join(agents)}")
    steps = []
    if run_deploy:
         steps.append("Step 1: Deploy to Vertex AI")
    if run_register:
         steps.append("Step 2: Register & Patch Gemini Enterprise")
    print(f"⚙️  Planned steps: {' & '.join(steps)}")

    GITEA_URL = discover_gitea_url() if run_deploy else ""
    if run_register:
        ensure_icon_asset()

    # --- STEP 1: DEPLOY ALL AGENTS ---
    if run_deploy:
        print("\n=======================================================")
        print("🚀 [PHASE 1] Deploying Agents to Vertex AI Agent Runtime")
        print("=======================================================")
        for agent_dir in agents:
            agent_path = os.path.join(root_dir, agent_dir)
            print(f"\n-------------------------------------------------------")
            print(f"▶️  Deploying Agent: {agent_dir}")
            print(f"-------------------------------------------------------")

            # Load configs
            config_path = os.path.join(agent_path, "publish_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
            else:
                config = {
                    "displayName": agent_dir.capitalize(),
                    "description": "AI Agent",
                    "icon_url": ICON_URL,
                    "tool_description": "AI Agent tool description"
                }

            # Run local package installation
            run_cmd(["uv", "sync", "--index", "https://pypi.org/simple"], cwd=agent_path)

            # Temporarily vendor agent_library for packaging
            src_lib_path = os.path.join(root_dir, "../lib/src/agent_library")
            dest_lib_path = os.path.join(agent_path, "app/agent_library")

            import shutil

            print(f"📦 Vendoring shared agent_library for deployment packaging...")
            if os.path.exists(dest_lib_path):
                shutil.rmtree(dest_lib_path)
            shutil.copytree(src_lib_path, dest_lib_path)

            # Collect peer engine IDs from local metadata files
            peer_env_vars = []
            for other_agent in all_agents:
                meta_path = os.path.join(root_dir, other_agent, "deployment_metadata.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    id_val = meta.get("remote_agent_runtime_id")
                    if id_val and id_val != "None":
                        peer_env_vars.append(f"{other_agent.upper()}AGENT_ENGINE_ID={id_val}")

            # Build environment variables map ensuring no empty values are sent to Agent Runtime
            env_map = {
                "AGENT_MCP_TOOLS_ADDRESS": AGENT_MCP_TOOLS_ADDRESS,
                "GCP_REGION": GCP_REGION,
            }
            webapps_login = rvars.get("WEBAPPS_LOGIN") or os.getenv("WEBAPPS_LOGIN", "networkagent")
            webapps_pwd = rvars.get("WEBAPPS_PWD") or os.getenv("WEBAPPS_PWD", "changeme")
            if webapps_login:
                env_map["WEBAPPS_LOGIN"] = webapps_login
            if webapps_pwd:
                env_map["WEBAPPS_PWD"] = webapps_pwd
            if GITEA_URL:
                env_map["GITEA_URL"] = GITEA_URL
            chat_space = rvars.get("GOOGLE_CHAT_SPACE_ID") or os.getenv("GOOGLE_CHAT_SPACE_ID", "")
            if chat_space:
                env_map["GOOGLE_CHAT_SPACE_ID"] = chat_space
            chat_webhook = rvars.get("GOOGLE_CHAT_WEBHOOK_URL") or os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "")
            if chat_webhook:
                env_map["GOOGLE_CHAT_WEBHOOK_URL"] = chat_webhook
            for p_env in peer_env_vars:
                if "=" in p_env:
                    pk, pv = p_env.split("=", 1)
                    if pv and pv != "None":
                        env_map[pk] = pv

            update_env_val = ",".join(f"{k}={v}" for k, v in env_map.items())

            try:
                # Run Reasoning Engine deployment
                deploy_cmd = [
                    "uv", "run", "--index", "https://pypi.org/simple", "agents-cli", "deploy",
                    f"--project={GCP_PROJECT}",
                    f"--region={GCP_REGION}",
                    f"--service-account={SERVICE_ACCOUNT}",
                    f"--update-env-vars={update_env_val}"
                ]
                run_cmd(deploy_cmd, cwd=agent_path)
            finally:
                print("🧹 Cleaning up vendored agent_library...")
                if os.path.exists(dest_lib_path):
                    shutil.rmtree(dest_lib_path)

    # --- STEP 2: REGISTER & PATCH ALL AGENTS ---
    if run_register:
        print("\n=======================================================")
        print("🎨 [PHASE 2] Registering & Branding in Gemini Enterprise")
        print("=======================================================")
        for agent_dir in agents:
            agent_path = os.path.join(root_dir, agent_dir)
            print(f"\n-------------------------------------------------------")
            print(f"▶️  Registering Agent: {agent_dir}")
            print(f"-------------------------------------------------------")

            # Load configs
            config_path = os.path.join(agent_path, "publish_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
            else:
                config = {
                    "displayName": agent_dir.capitalize(),
                    "description": "AI Agent",
                    "icon_url": ICON_URL,
                    "tool_description": "AI Agent tool description"
                }

            config["icon_url"] = Template(
                config.get("icon_url") or ICON_URL
            ).safe_substitute(GCP_PROJECT=GCP_PROJECT)

            # Fetch Runtime ID from metadata
            metadata_path = os.path.join(agent_path, "deployment_metadata.json")
            if not os.path.exists(metadata_path):
                print(f"⚠️  Warning: deployment_metadata.json not found in {agent_dir}. Skipping registration.")
                continue

            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            agent_runtime_id = metadata.get("remote_agent_runtime_id")
            if not agent_runtime_id or agent_runtime_id == "None":
                print(f"⚠️  Warning: remote_agent_runtime_id is missing or 'None' in {agent_dir}/deployment_metadata.json. Skipping registration.")
                continue

            # Check if already registered to prevent unbranded duplicates
            if is_agent_registered(config["displayName"]):
                print(f"⚡ Agent '{config['displayName']}' is already registered in Gemini Enterprise!")
            else:
                # Publish to Gemini Enterprise
                publish_cmd = [
                    "uv", "run", "--index", "https://pypi.org/simple", "agents-cli", "publish", "gemini-enterprise",
                    f"--project-id={GCP_PROJECT}",
                    f"--gemini-enterprise-app-id={GEMINI_APP_ID}",
                    f"--display-name={config['displayName']}",
                    "--registration-type=adk",
                    f"--agent-runtime-id={agent_runtime_id}"
                ]
                rc = run_cmd(publish_cmd, cwd=agent_path, check=False)
                if rc != 0:
                    print(f"⚠️  Publish to Gemini Enterprise returned code {rc} for '{config['displayName']}'.")

            # Apply custom metadata (PATCH)
            try:
                update_gemini_metadata(agent_runtime_id, config)
            except Exception as e:
                print(f"⚠️  Could not patch Gemini Enterprise metadata for '{config['displayName']}': {e}")

    print("\n🎉 Orchestration process completed successfully!")

if __name__ == "__main__":
    main()
