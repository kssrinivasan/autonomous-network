# NetworkAgent Shared Library

Common functionality shared by the agents, the MCP tool servers and the dashboard backend.

| Module | Contents |
|---|---|
| `credentials/creds.py` | GCP credentials (`get_credentials`) |
| `credentials/k8s.py` | Kubernetes client for the GKE cluster (`get_client`) |
| `gitea_utils.py` | The Gitea client — URL discovery, `py-gitea` patches, and the file CRUD helpers |
| `trace/` | Agent tracing |
| `a2a_helpers.py` | Agent-to-agent call helpers |

Only the first three are imported by `__init__.py`. `trace` and `a2a_helpers` pull in ADK and
Spanner, so importing `agent_library` does **not** cost a consumer those dependencies — which is
what makes it safe to vendor into the MCP servers.

## Gitea

`gitea_utils` is the single source of truth for talking to Gitea. It used to be copy-pasted into
seven places, and the same defect then had to be fixed in four or five of them; four of those
copies are now facades over this module.

```python
from agent_library import (
    get_gitea_url, get_repo, git_file_exists,
    get_git_file, list_git_files,
    commit_git_file, delete_git_file, delete_all_git_files,
    USER, PWD, MASTER_BRANCH,
)
```

Two things to know before using it:

- **Everything is branch-aware.** `MASTER_BRANCH` defaults to `main`, overridable with
  `GITEA_BASE_BRANCH`. A seeded Gitea has no `master` ref at all. `commit_git_file` and
  `delete_git_file` check for the file *on the branch they are writing to*, so a repeat write to a
  long-lived review branch updates rather than failing to create; `_ensure_branch` cuts a missing
  branch from `MASTER_BRANCH` on first write.
- **`list_git_files` is root-only.** The Gitea contents API is not recursive here. It returns `[]`
  on any failure, including a missing branch, so callers can fall back to a computed name instead
  of catching.

Credentials come from `WEBAPPS_LOGIN` / `WEBAPPS_PWD`. The URL comes from `GITEA_URL` if set, and
otherwise from the Cloud Run Admin API.

## How consumers get this library

Two different mechanisms, because the build contexts differ:

- **pip install** — `dashboard-v2` and anything whose Dockerfile can see `lib/`.
- **Vendored copy** — the agents (`deploy_agents.py` does a `copytree` into `app/agent_library`)
  and the MCP servers (`mcp_server_for_ge/{re,ge}/deploy.sh` copies into `src/agent_library`),
  because `lib/` sits outside their `--source` build context.

The vendored copies are gitignored and remade on every deploy. **Never edit or commit one** — edit
`lib/` and redeploy.

Two consequences worth internalising:

1. `lib/pyproject.toml`'s dependency list is **inert** for vendored consumers — nothing installs
   it. Any new import added here must be declared in every consuming component's own
   `pyproject.toml` / `requirements.txt`, or it resolves only by luck of a transitive.
2. Each vendoring consumer needs a `.gcloudignore`. Without one, gcloud synthesizes it from
   `.gitignore`, which excludes the vendored copy — the upload silently omits the library and the
   container dies at import with `ModuleNotFoundError`.

Two copies deliberately remain un-consolidated: `controller/src/utils/git.py` (pins
`py-gitea==0.3.2` against `0.2.10` here, and this module monkeypatches SDK internals) and
`services/sharedmcp/src/utils/gitea_utils.py` (builds with `uv sync --frozen`).
