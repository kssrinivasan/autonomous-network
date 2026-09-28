#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
import google.auth
from google.auth.transport.requests import Request
import requests

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

GCP_PROJECT = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_REGION = os.environ.get("GCP_REGION", "europe-west1")
SERVICE_ACCOUNT = os.environ.get("AGENT_SERVICE_ACCOUNT", "")
SHARED_MCP_URL = os.environ.get("SHARED_MCP_URL", "")
GITEA_MCP_URL = os.environ.get("GITEA_MCP_URL", "")
GEMINI_APP_ID = os.environ.get("GEMINI_APP_ID", "")
SPANNER_OPS_INSTANCE = os.environ.get("GOOGLE_SPANNER_OPS_INSTANCE", "networktopology-instance")
SPANNER_OPS_DB = os.environ.get("GOOGLE_SPANNER_OPS_DATABASE", "agentops-db")

if not GCP_PROJECT:
    print("❌ Error: GCP_PROJECT or GOOGLE_CLOUD_PROJECT environment variable is required.")
    sys.exit(1)

def run_cmd(cmd, cwd=None):
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
    if rc != 0:
        print(f"❌ Command failed with return code {rc}")
        sys.exit(rc)

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
    parser.add_argument(
        "-u", "--undeploy",
        action="store_true",
        help="Delete agent from Vertex AI Agent Runtime and unregister from Gemini Enterprise."
    )
    args = parser.parse_args()

    # Determine execution mode
    run_undeploy = args.undeploy
    run_deploy = not run_undeploy and (args.deploy or not (args.deploy or args.register))
    run_register = not run_undeploy and (args.register or not (args.deploy or args.register))

    root_dir = os.path.dirname(os.path.realpath(__file__))
    subdirs = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]

    all_agents = []
    for d in subdirs:
        agent_path = os.path.join(root_dir, d)
        manifest_path = os.path.join(agent_path, "agents-cli-manifest.yaml")
        if os.path.exists(manifest_path):
            all_agents.append(d)

    if not all_agents:
        print("ℹ️  No subfolder agents found under agents/.")
        sys.exit(0)

    # Filter by targeted agents
    if args.agents:
        agents = [a for a in all_agents if a in args.agents]
        if not agents:
            print(f"❌ Error: None of the specified agents {args.agents} were found. Available: {all_agents}")
            sys.exit(1)
    else:
        agents = all_agents

    # Print execution steps
    print(f"📋 Target agents: {', '.join(agents)}")
    steps = []
    if run_deploy:
        steps.append("Step 1: Deploy to Vertex AI")
    if run_register:
        if GEMINI_APP_ID:
            steps.append("Step 2: Register & Patch Gemini Enterprise")
        else:
            steps.append("Step 2: Skipped (GEMINI_APP_ID not configured)")
    if run_undeploy:
        steps.append("Undeploy from Vertex AI & Gemini Enterprise")
    print(f"⚙️  Planned steps: {' & '.join(steps)}")

    for agent_dir in agents:
        agent_path = os.path.join(root_dir, agent_dir)
        print(f"\n=======================================================")
        print(f"▶️  Processing Agent: {agent_dir}")
        print(f"=======================================================")

        # Load configs
        config_path = os.path.join(agent_path, "publish_config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            config = {
                "displayName": agent_dir.capitalize(),
                "description": "AI Agent",
                "icon_url": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/smart_toy/default/24px.svg",
                "tool_description": "AI Agent tool description"
            }

        # --- UNDEPLOY ---
        if run_undeploy:
            print(f"\n🗑️  [UNDEPLOY] Removing '{agent_dir}' from Vertex AI and Gemini Enterprise...")
            metadata_path = os.path.join(agent_path, "deployment_metadata.json")
            if not os.path.exists(metadata_path):
                print(f"⚠️  No deployment_metadata.json found for {agent_dir} — skipping.")
                continue

            with open(metadata_path, "r") as f:
                metadata = json.load(f)
            agent_runtime_id = metadata.get("remote_agent_runtime_id")
            if not agent_runtime_id or agent_runtime_id == "None":
                print(f"⚠️  No remote_agent_runtime_id in {agent_dir}/deployment_metadata.json — skipping.")
                continue

            # Delete via the agent's own venv (which has google-cloud-aiplatform installed)
            print(f"   Deleting Reasoning Engine: {agent_runtime_id}")
            run_cmd([
                "uv", "run", "--index", "https://pypi.org/simple",
                "python", "-c",
                f"import vertexai; from vertexai import agent_engines; "
                f"vertexai.init(project='{GCP_PROJECT}', location='{GCP_REGION}'); "
                f"agent_engines.get('{agent_runtime_id}').delete(force=True)"
            ], cwd=agent_path)
            print(f"✅ Reasoning Engine deleted.")

            # Unregister from Gemini Enterprise
            if GEMINI_APP_ID:
                try:
                    credentials, _ = google.auth.default()
                    credentials.refresh(Request())
                    headers = {
                        "Authorization": f"Bearer {credentials.token}",
                        "x-goog-user-project": GCP_PROJECT,
                        "Content-Type": "application/json",
                    }
                    parts = GEMINI_APP_ID.split("/")
                    base_url = "https://discoveryengine.googleapis.com/v1alpha"
                    list_url = f"{base_url}/projects/{parts[1]}/locations/{parts[3]}/collections/{parts[5]}/engines/{parts[7]}/assistants/default_assistant/agents"
                    resp = requests.get(list_url, headers=headers, timeout=30)
                    resp.raise_for_status()
                    for agent_entry in resp.json().get("agents", []):
                        if agent_entry.get("displayName") == config["displayName"]:
                            delete_url = f"{base_url}/{agent_entry['name']}"
                            requests.delete(delete_url, headers=headers, timeout=30).raise_for_status()
                            print(f"✅ Unregistered '{config['displayName']}' from Gemini Enterprise.")
                            break
                    else:
                        print(f"⚠️  Agent '{config['displayName']}' not found in Gemini Enterprise — skipping.")
                except Exception as e:
                    print(f"⚠️  Gemini Enterprise unregister failed: {e}")

            # Clear the stored resource ID
            metadata["remote_agent_runtime_id"] = None
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            print(f"✅ Cleared deployment_metadata.json for {agent_dir}.")
            continue

        # --- STEP 1: DEPLOY ---
        if run_deploy:
            print(f"\n🔨 [STEP 1/2] Deploying '{config['displayName']}' to Vertex AI...")
            # Run local package installation
            run_cmd(["uv", "sync", "--index", "https://pypi.org/simple"], cwd=agent_path)

            # Collect peer engine IDs from local metadata files
            peer_env_vars = []
            for other_agent in all_agents:
                meta_path = os.path.join(root_dir, other_agent, "deployment_metadata.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    id_val = meta.get("remote_agent_runtime_id")
                    if id_val and id_val != "None":
                        peer_env_vars.append(f"{other_agent.upper().replace('-', '_')}_ENGINE_ID={id_val}")
            
            peer_env_str = ",".join(peer_env_vars)
            update_env_val = f"GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=global,SHARED_MCP_URL={SHARED_MCP_URL},GITEA_MCP_URL={GITEA_MCP_URL},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY,GOOGLE_SPANNER_OPS_INSTANCE={SPANNER_OPS_INSTANCE},GOOGLE_SPANNER_OPS_DATABASE={SPANNER_OPS_DB}"
            if peer_env_str:
                update_env_val += f",{peer_env_str}"

            # Run Reasoning Engine deployment
            deploy_cmd = [
                "uv", "run", "--index", "https://pypi.org/simple", "agents-cli", "deploy",
                f"--project={GCP_PROJECT}",
                f"--region={GCP_REGION}",
                f"--update-env-vars={update_env_val}",
            ]
            if SERVICE_ACCOUNT:
                deploy_cmd.append(f"--service-account={SERVICE_ACCOUNT}")
            run_cmd(deploy_cmd, cwd=agent_path)

        # --- STEP 2: REGISTER & PATCH ---
        if run_register:
            if not GEMINI_APP_ID:
                print(f"⚠️  GEMINI_APP_ID not configured — skipping Gemini Enterprise registration for '{agent_dir}'.")
                continue
            print(f"\n🎨 [STEP 2/2] Registering & Branding '{config['displayName']}' in Gemini Enterprise...")
            
            # Fetch Runtime ID from metadata
            metadata_path = os.path.join(agent_path, "deployment_metadata.json")
            if not os.path.exists(metadata_path):
                print(f"❌ Error: deployment_metadata.json not found in {agent_dir}. Ensure you run Step 1 (Deploy) first.")
                sys.exit(1)

            with open(metadata_path, "r") as f:
                metadata = json.load(f)
            
            agent_runtime_id = metadata.get("remote_agent_runtime_id")
            if not agent_runtime_id or agent_runtime_id == "None":
                print(f"❌ Error: remote_agent_runtime_id is missing or 'None' in {agent_dir}/deployment_metadata.json.")
                print("   Please run Step 1 (Deploy) first to deploy and acquire the ID.")
                sys.exit(1)

            # Check if already registered to prevent unbranded duplicates
            if is_agent_registered(config["displayName"]):
                print(f"⚡ Agent '{config['displayName']}' is already registered in Gemini Enterprise! Skipping duplicate creation.")
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
                run_cmd(publish_cmd, cwd=agent_path)

            # Apply custom metadata (PATCH)
            update_gemini_metadata(agent_runtime_id, config)

    print("\n🎉 Orchestration process completed successfully!")

if __name__ == "__main__":
    main()
