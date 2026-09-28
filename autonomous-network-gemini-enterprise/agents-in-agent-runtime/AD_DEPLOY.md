# Gemini Enterprise Agent Runtime Deployment Guide (AD_DEPLOY)

This directory (`agents-in-agent-runtime/`) is structured to host and orchestrate multiple modular, self-contained AI agents deployed as professional **Google Cloud Vertex AI Agent Runtime (Reasoning Engine)** applications, fully integrated with **Gemini Enterprise**.

---

## 🏗️ Directory Layout (Clean Nouns)

To establish absolute architectural clarity, all agent projects are organized under pristine clean nouns, managing their own code, manifest, dependencies (`pyproject.toml`), and local ASGI runtimes:

```text
agents-in-agent-runtime/
├── deploy_agents.py             # Central Deployment & Registration Orchestrator
├── AD_DEPLOY.md                 # This Guide
├── runtime_variables.json       # Authoritative cloud orchestration constants
│
├── assets/telco_icon.jpg        # Icon uploaded to {project}-agent-assets at register time
│
├── chaos/                       # Autonomous MPLS/L3VPN Failure Injection
├── designer/                    # High-Level Intent to VyOS K8s CRDs
├── detective/                   # Root Cause Analysis over telemetry and topology
├── impact/                      # Outage & SLA Blast Radius Assessment
├── supervisor/                  # Incident Management & Multi-Agent Orchestration
├── trust/                       # Design review of the designer's CRs, verdict on the PR
└── tester/                      # Distributed Go Traffic Generation & Validation
    ├── app/                     # Python Agent Source Code
    │   ├── agent.py             # ADK App, System Prompt, and Tools
    │   └── agent_runtime_app.py # Hybrid ASGI App complete with top-level query() REST method
    ├── pyproject.toml           # name = "tester"
    ├── agents-cli-manifest.yaml # name: "tester"
    └── publish_config.json      # Gemini Enterprise UI branding (Title, Icon, Description)
```

---

## 🚀 How to Deploy & Register Agents (Two-Step Orchestrator)

The orchestration script decouples deployment (Step 1) and registration/branding (Step 2) to give you total control over execution time.

### Available Orchestrator Commands:

* **End-to-End Execution (Both Step 1 and Step 2 for ALL agents):**
  ```bash
  ./deploy_agents.py
  ```
* **Step 1 Only (Deploy/update code on Vertex AI Agent Runtime for ALL agents):**
  ```bash
  ./deploy_agents.py --deploy      # or -d
  ```
* **Step 2 Only (Register/Publish and apply custom icon/description for ALL agents):**
  ```bash
  ./deploy_agents.py --register    # or -r
  ```
* **Target a Specific Agent (e.g. detective only, both steps):**
  ```bash
  ./deploy_agents.py detective
  ```
* **Target a Specific Agent for a Specific Step (e.g. patch branding on tester only):**
  ```bash
  ./deploy_agents.py tester --register
  ```

### What the Orchestrator Automates:
1. **Scans Directories:** Discovers any subfolder containing an `agents-cli-manifest.yaml` configuration.
2. **Loads Configs:** Parses the locally defined `publish_config.json` for custom descriptions, tool descriptions, and branding icons.
3. **Installs Packages:** Runs `uv sync` (bypassing credential blocks by querying standard PyPI) to configure local virtual environments.
4. **Step 1 (Deploy):** Packages source code and publishes it to Vertex AI Agent Runtime in the region named by `runtime_variables.json` (`GCP_REGION`, currently `europe-west1`). If an existing instance ID is tracked inside `deployment_metadata.json`, it performs an **in-place update** without creating duplicate cloud resources!
5. **Step 2 (Register & Patch):** Links the running Reasoning Engine to your Gemini Enterprise App, and executes an authenticated Discovery Engine API PATCH call to inject the custom description and **icon URI**.

---

## ⚡ Advanced Capabilities: Native Async Query REST Handlers

Every agent inside this suite is equipped with a professional hybrid ASGI runtime (`agent_runtime_app.py`) complete with a top-level unary execution entrypoint:

```python
async def query(self, request: Any) -> str:
```

When Gemini Enterprise or an external peer agent POSTs a direct execution prompt to `/query`, the handler transparently unpacks the payload, routes it directly to the underlying language model (`target.run` or `target.run_live`), gathers all asynchronous streaming chunks automatically, and returns a clean, fully consolidated string response!

---

## ➕ How to Add a New Agent to the Layout

Follow these steps to add a new modular agent:

### 1. Scaffold the New Agent
From the `agents-in-agent-runtime/` root, run the scaffolding command to create the workspace (ensure the name is a clean noun, lowercase, alphanumeric/hyphens, and $\le$ 26 characters):
```bash
agents-cli scaffold create myagent --agent adk --deployment-target agent_runtime --prototype
```

### 2. Configure Local Manifest
Open `agents-in-agent-runtime/myagent/agents-cli-manifest.yaml` and set standard ADK parameters:
- `base_template: "adk"`
- `is_a2a: false`

Ensure `name` inside `agents-cli-manifest.yaml` and `pyproject.toml` is set exactly to `myagent`.

### 3. Create Custom `publish_config.json`
Create a file named `publish_config.json` inside your new agent subdirectory containing your custom Gemini Enterprise metadata:
```json
{
  "displayName": "Network Designer",
  "description": "A specialized agent that handles transport topology design and peering recommendations.",
  "icon_url": "https://storage.googleapis.com/${GCP_PROJECT}-agent-assets/telco_icon.jpg",
  "tool_description": "Use this tool to consult network service architectures and router topology models."
}
```

### 4. Implement Agent Code & Tools
- Update `app/agent.py` to define your LLM configuration, tools, and system prompts.
- Ensure `app/agent_runtime_app.py` includes our universal session sanitization hook and native async `query()` wrapper (refer to the implementation inside `detective/app/agent_runtime_app.py`). The sanitization hook is not cosmetic: Gemini Enterprise sends a full resource path as `session_id`, which the runtime rejects unless it is reduced to the bare id.
- If the agent needs the shared Gitea client, import it from `agent_library` — `deploy_agents.py` vendors `lib/src/agent_library` into `app/` at build time. The vendored copy is gitignored; never edit or commit it.

### 5. Run the Orchestrator
Execute `./deploy_agents.py myagent` to fully compile, upload, register, and brand your new agent automatically!

Deploy **supervisor last**. It resolves its peers' Reasoning Engine ids from their
`deployment_metadata.json`, so any peer deployed after it will be missing from its tool wiring until
it is redeployed.
