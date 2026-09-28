# Network Agents

The Autonomous Network Lab runs two separate fleets of agents. They differ in where they run, how
they are deployed, and which tool server they call — telling them apart is the first thing to get
right when reading the code.

| | Cross-domain fleet | Domain orchestrator |
|---|---|---|
| Location | [`agents-in-agent-runtime/`](../../agents-in-agent-runtime/) | [`domains/transport/agents/`](../../domains/transport/agents/) |
| Runtime | Vertex AI Agent Runtime (Reasoning Engine) | Cloud Run |
| Framework | Google ADK, scaffolded with `agents-cli` | Google ADK |
| Deployed by | `python3 deploy_agents.py` (manual step) | `install.sh --agents` |
| Tool server | `mcp_server_for_ge/re` (SSE, IAM + OIDC) | `services/sharedmcp` (Cloud Run) |
| Front end | dashboard-v2, and Gemini Enterprise | the supervisor, via A2A |

## The cross-domain fleet

Seven agents, one directory each under `agents-in-agent-runtime/`. Each has an
`agents-cli-manifest.yaml`, an `app/agent.py`, and a `publish_config.json` holding the description
and icon used when registering it with Gemini Enterprise.

| Agent | What it does |
|---|---|
| **supervisor** | Primary orchestration interface. Drives the structured troubleshooting loop and delegates to the others via `delegate_to_agent`. Deploy it **last** — it needs the peers' engine ids. |
| **detective** | Root-cause analysis. Queries VNF deployments, live telemetry, fault status and link bottlenecks. |
| **impact** | Impact assessment before a change is applied — routing paths, link utilisation, traffic flows, simulation. |
| **designer** | Translates design intent into VyOS CRs and pushes them to the Gitea `network` repo on the **`update`** branch. Never writes to `main`. |
| **trust** | Reviews the designer's diff against network design principles and posts a verdict on the PR. `**PROCEED**` auto-merges to `main`, where Config Sync picks it up. |
| **tester** | Configures, runs and monitors traffic tests across the network. |
| **chaos** | Injects controlled failures by creating and deleting `NetworkFailure` custom resources. |

### Deploying

```bash
cd agents-in-agent-runtime
python3 deploy_agents.py                      # deploy + register all agents
python3 deploy_agents.py --deploy detective   # one agent, deploy only
python3 deploy_agents.py --register           # re-register with Gemini Enterprise only
```

`deploy_agents.py` vendors `lib/src/agent_library` into each agent's `app/` at build time (it is
outside their build context and cannot be pip-installed). Those copies are gitignored — never edit
or commit them. See [lib/README.md](../../lib/README.md).

Deployment notes and troubleshooting live in
[agents-in-agent-runtime/AD_DEPLOY.md](../../agents-in-agent-runtime/AD_DEPLOY.md).

## The transport domain orchestrator

[`domains/transport/agents/transport-orchestrator/`](../../domains/transport/agents/transport-orchestrator/)
monitors, troubleshoots and autonomously manages transport resources — VyOS routers, underlays and
L3VPN services. It is the only domain orchestrator that exists: the core, radio and service
orchestrators were the unmodified ADK starter sample and were deleted rather than kept as dead
weight. Adding one back means adding the domain to the loop in `install.sh`'s `DeployAgents`.

```bash
cd domains/transport/agents && ./install.sh
```

## Tool servers

Agents reach the network through MCP, never directly:

- **[`mcp_server_for_ge/`](../../mcp_server_for_ge/README.md)** — two deployments of one codebase.
  `re/` serves the fleet over SSE behind IAM + OIDC with mutating tools enabled; `ge/` serves
  Gemini Enterprise over streamable-HTTP behind a shared secret, read-only. Every file under
  `src/tools/` and `src/utils/` is byte-identical between them; only `src/main.py` differs.
- **[`services/sharedmcp/`](../../services/sharedmcp/Readme.md)** — the tool server for the
  transport orchestrator.
- **[`domains/service/services/mcp/`](../../domains/service/services/mcp/Readme.md)** — a thin proxy
  exposing the per-domain TMF intent (TMF921) and topology (TMF639) REST APIs as MCP tools. It has
  tools for all three of transport, core and radio, but only the transport intent and topology
  servers are deployed, so the core and radio tools have nothing to call. `install.sh` deploys
  domain services for transport only, so this server is not brought up by a default install.

## Related

- [Security model](../SECURITY.md) — the auth boundary at each hop
- [IAM strategy](../IAM_STRATEGY.md) — service accounts and role grants
- [Gitea](../../services/gitea/Readme.md) and
  [the trust review loop](../../services/gitea-webhook/Readme.md)
- [Dashboard v2](../../dashboard-v2/README.md) — how the console reaches the supervisor
