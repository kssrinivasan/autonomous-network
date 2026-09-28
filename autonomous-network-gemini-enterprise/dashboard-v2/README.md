# Autonomous Network Lab — Dashboard v2

Welcome to **Dashboard v2**, the modernized, enterprise-grade telecommunications operations console for the **Autonomous Network Lab**.

This directory implements a next-generation **Flutter Web UI** paired directly with Google Cloud's fully managed **Vertex AI Reasoning Engine** suite, completely decoupling dynamic client agent registries and moving conversational AI workloads straight to authoritative multi-agent cloud engines.

---

## 🏛️ Architectural Evolution: v1 vs. v2

The following table highlights the five profound operational differences between the legacy `dashboard/` console and `dashboard-v2/`:

| Subsystem / Feature | Legacy v1 (`dashboard/`) | Modernized v2 (`dashboard-v2/`) |
| :--- | :--- | :--- |
| **Conversational AI Bridge** | Connected over unauthenticated **Socket.io WebSockets** to an intermediate Cloud Run gateway (`network-agent-supervisor`). | Communicates via direct REST HTTP POST streaming (`/streamQuery`) directly to the cloud **Vertex AI Reasoning Engine** (`supervisoragent`). |
| **Authentication Boundary** | **Server-Side Hand-off**: Browser sent plain JSON socket events; intermediate Python container automatically minted IAM tokens. | **Unified Proxy Middleware**: Built-in `aiohttp` container server ([server.py](server.py)) transparently attaches managed Cloud Run IAM OAuth access tokens server-side. |
| **Agent Team Management** | **Dynamic Client-Side Registry**: Required operators to manually add or remove peer agent endpoints via interactive UI forms. | **Decoupled Autonomous Orchestration**: Hardcodes authoritative cloud Reasoning Engine IDs programmatically via Python `delegate_to_agent`. |
| **Web Server Engine** | Standard built-in Python `http.server` running static file delivery. | Advanced Single Page Application (SPA) `aiohttp` middleware guaranteeing `index.html` rendering across all sub-routes (`/login`, `/dashboard`). |
| **Payload Restructuring** | Handled natively by custom intermediate socket event handlers. | Automated outgoing payload enrichment injecting pristine `google.genai` mapping dictionaries (`contents`, `messages`, `history`). |

---

## 🚀 Key Engineering Components inside `dashboard-v2/`

1. **Direct Vertex AI REST Provider ([agent_engine_provider.dart](lib/agent/agent_engine_provider.dart))**: Implements Google's official `flutter_ai_toolkit` `LlmProvider` interface. It parses Server-Sent Events line-by-line and populates live `ChatMessage.llm()` widgets instantly.
2. **Authoritative Team Roster ([settings_screen.dart](lib/screens/settings_screen.dart))**: Replaced legacy dynamic registries with a clean, professional overview showcasing your active online cloud team (`SupervisorAgent`, `LogsAgent`, `ImpactAgent`, `DesignerAgent`, `TesterAgent`, `ChaosAgent`).
3. **Unified Zero-Auth Proxy Server ([server.py](server.py))**: A robust `aiohttp` backend running as the container's main serving process. It mounts static Flutter Web compile outputs (`/`) with strict SPA sub-routing immunity and exposes an unauthenticated `/streamQuery` proxy route that attaches IAM OAuth Bearer tokens securely server-side.
4. **Rollout**: `flutter clean`, `flutter build web`, Cloud Build submission and the Cloud Run rollout are all driven by `install.sh --dashboard` at the repository root. There is no dashboard-local deploy script — see below.

---

## 🛠️ Rollout Workflows

To trigger an end-to-end production build and serverless rollout targeting your active Google Cloud environment:

```bash
# From the repository root
source ./config.sh
./install.sh --dashboard
```

> There used to be a `dashboard-v2/deploy_dashboard_v2.sh`. It has been deleted rather than
> kept in sync: it deployed the service `--allow-unauthenticated`, so anyone who used it got a
> dashboard with no access control at all, while the installer puts the same service behind IAP.
> `install.sh --dashboard` runs standalone — it renders its own `cloudbuild.yaml` and does not
> require a full `--all` install.

### 👉 Clickable Live Endpoints

Once fully deployed, access your console at either authoritative cloud link:
1. [https://dashboard-v2-YOUR_PROJECT_NUMBER.YOUR_REGION.run.app](https://dashboard-v2-YOUR_PROJECT_NUMBER.YOUR_REGION.run.app)
