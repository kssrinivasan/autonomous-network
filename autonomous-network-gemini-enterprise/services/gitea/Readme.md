# Gitea Service

A self-hosted Gitea instance running on GKE that serves as the GitOps source of truth for the autonomous network lab. The Kubernetes controller and domain orchestrator agents read and write network design CRs through repositories hosted here. Config Sync watches the `root-repo` and applies changes to the cluster automatically.

## How It Works

```
Orchestrator Agent
      │  (deploys YAML descriptors)
      ▼
  Gitea (GKE Deployment, /data on a persistent disk)
      │  root-repo ──► Config Sync (RootSync) ──► GKE cluster
      │  network   ──► Config Sync (RepoSync) ──► network namespace
      │  <domain>  ──► domain knowledge base (transport, core, radio, service)
```

1. Orchestrator agents push Kubernetes CR YAML into the `network` repository (`update` branch)
2. Config Sync detects the change and applies the CRs to the cluster
3. The Kubernetes controller reconciles the CRs against the real network

## Directory Structure

```
gitea/
├── Dockerfile              # Extends gitea/gitea:latest; runs init.sh on startup
├── init.sh                 # Bootstraps Gitea config (app.ini), DB migration, and admin user
├── seed.sh                 # Creates and pushes seed content to all repositories post-deploy
├── create-rootsync.sh      # Creates git-creds secrets and the RootSync CR in the cluster
├── gitea-url.sh            # Single source of truth for Gitea's URL — every consumer calls this
├── install.sh              # Full deploy: address → cert → Cloud Build → GKE → seed → RootSync
├── uninstall.sh            # Teardown (keeps the disk unless --purge)
├── cloudbuild.tpl.yaml     # Cloud Build template (rendered via envsubst at deploy time)
├── deployment.tpl.yaml     # StorageClass + PVC + Deployment + LoadBalancer Service
└── repos/
    ├── network/            # Seed content for the network CR repository
    └── root-repo/          # Seed content for the Config Sync root repository
        ├── network-namespace.yaml
        ├── network-repo-sync.yaml.template   # Templated with GITEA_URL/WEBAPPS_LOGIN at seed time
        └── network-role.yaml
```

## Repositories

| Repository | Branch | Purpose |
|------------|--------|---------|
| `networkdesign` | `main` | Design documents read by the designer and chaos agents. Assembled at seed time from `domains/transport/knowledge/designdoc.md` and `docs/gnn/{failure-analysis,rca}.md` — flat, because the agents fetch by bare filename |
| `network` | `main` / `update` | Kubernetes CR YAML deployed by orchestrator agents; Config Sync watches `update` branch |
| `root-repo` | `main` | Config Sync root: namespace, RepoSync, and RBAC manifests |
| `transport` | `main` | Transport domain knowledge base (seeded from `domains/transport/knowledge/`) |
| `core` | `main` | Core domain knowledge base |
| `radio` | `main` | Radio domain knowledge base |
| `service` | `main` | Service domain knowledge base |

## Deployment

```bash
# Deploy from the repo root (recommended — runs as part of --agents)
./install.sh --agents

# Deploy standalone
cd services/gitea && ./install.sh
```

Required environment variables (from `config.sh`):

```bash
export GOOGLE_PROJECT=<project-id>
export GOOGLE_REGION=<region>
export WEBAPPS_LOGIN=<gitea-admin-username>
export WEBAPPS_PWD=<gitea-admin-password>
export GITEA_ADMIN_EMAIL=<admin-email>
export GOOGLE_NAMESPACE=<namespace>                 # where the Deployment lands
export GITEA_ADDRESS_NAME=gitea-ip                  # optional; this is the default
```

### What `install.sh` does

1. Reserves the regional static address `gitea-ip` if it does not exist, and exports
   `GITEA_LB_IP` / `SERVICE_URL` from it
2. Generates a self-signed TLS certificate whose SAN covers that IP plus the in-cluster
   DNS names, and stores it as the `gitea-tls` secret — reusing the existing secret if
   one is already present, so the certificate survives redeploys
3. Creates the `gitea-admin` secret from `WEBAPPS_LOGIN` / `WEBAPPS_PWD`
4. Renders `cloudbuild.tpl.yaml` → `cloudbuild.yaml` via `envsubst` and builds the image
5. Renders `deployment.tpl.yaml` → `deployment.yaml` and applies it, then waits for the
   rollout
6. Runs `seed.sh` to create repositories and push initial content
7. Runs `create-rootsync.sh` to wire Config Sync to the `root-repo`

`deployment.yaml` is gitignored — it is a render, not a source file. Edit the `.tpl.yaml`.

## Persistence

Gitea is filesystem-native: a SQLite `gitea.db` and bare git repositories, all under
`/data`. It ran on Cloud Run until migration plan §7.2, where `/data` was an in-memory
filesystem — so every revision, scale-to-zero and instance recycle silently destroyed
every repository, user, API token, webhook and pull request. gcsfuse is not an option
(no POSIX locking for SQLite, no atomic rename for git refs) and Filestore's 1 TiB
minimum costs ~$164/month for ~100 MB of repos. So Gitea moved to GKE.

| Piece | What it is | Why |
|-------|-----------|-----|
| `StorageClass gitea-retain` | `pd-balanced`, `reclaimPolicy: Retain`, `WaitForFirstConsumer` | `Retain` means deleting the PVC releases the disk instead of erasing it |
| `PersistentVolumeClaim gitea-data-pvc` | 20Gi, RWO | Mounted at `/data` |
| `Deployment gitea` | `replicas: 1`, `strategy: Recreate` | An RWO disk allows exactly one writer; a RollingUpdate would run two pods against one SQLite file |
| `Service gitea` | `type: LoadBalancer` on the reserved address, `:443 → :3000` | A stable public IP is the whole identity — there is no DNS name |

TLS is terminated by Gitea itself with a self-signed certificate. Google-managed
certificates validate domains, not IP addresses, and the alternative (a `nip.io`
hostname) was rejected: it puts a third-party wildcard DNS service on the critical path
of every GitOps write. Every client in the tree already skips verification —
`verify=False` in the Python helpers, `-k` in the shell scripts, `GIT_SSL_NO_VERIFY=1`
for git, and `noSSLVerify: true` in the RootSync and RepoSync.

Health probes hit `/api/healthz`, **not** `/api/v1/version`: Gitea is configured with
`REQUIRE_SIGNIN_VIEW = true`, under which `/api/v1/version` returns 403 to an
unauthenticated prober and the pod never becomes Ready. `/api/healthz` is exempt.

### Finding the URL

Never re-derive it. One script owns the lookup:

```bash
bash services/gitea/gitea-url.sh      # → https://34.156.121.236
```

It prints nothing and exits 0 when the address does not exist yet, so callers can use it
before Gitea is deployed. The Python consumers (`lib/src/agent_library/gitea_utils.py`,
`controller/src/utils/git.py`, `services/gitea-webhook/src/main.py`) read `GITEA_URL`
first and only fall back to the Compute API — they cannot shell out to `gcloud`, which
is absent from their runtime images.

### Recovery — re-binding the disk

`uninstall.sh` deliberately keeps the PVC, its PersistentVolume and the reserved address.
Re-running `install.sh` re-attaches all three and the repositories are still there.

If the PVC itself was deleted, the disk still exists (that is what `Retain` buys you) but
its PersistentVolume is `Released` and will not bind to a new claim until the stale
claimRef is cleared:

```bash
# 1. Find the released volume and the disk behind it
kubectl get pv -o custom-columns=\
NAME:.metadata.name,STATUS:.status.phase,DISK:.spec.csi.volumeHandle | grep Released

# 2. Drop the stale claimRef so the volume can bind again
kubectl patch pv <pv-name> -p '{"spec":{"claimRef":null}}'

# 3. Recreate the claim bound to that specific volume
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: gitea-data-pvc
  namespace: ${GOOGLE_NAMESPACE}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: gitea-retain
  volumeName: <pv-name>
  resources:
    requests:
      storage: 20Gi
EOF

# 4. Bring Gitea back up against it
./install.sh
```

If even the PV object is gone, the underlying PD survives — recreate the PV by hand with
`spec.csi.volumeHandle` set to `projects/<project>/zones/<zone>/disks/<disk-name>`
(`gcloud compute disks list --filter="name~gitea"` finds it), then follow step 3.

To take a backup before doing anything destructive, mirror every repository out:

```bash
GITEA_URL=$(bash services/gitea/gitea-url.sh)
GIT_SSL_NO_VERIFY=1 git clone --mirror \
  "https://${WEBAPPS_LOGIN}:${WEBAPPS_PWD}@${GITEA_URL#https://}/${WEBAPPS_LOGIN}/network"
```

A mirror clone captures every ref, so restoring is `git push --mirror` back at a freshly
seeded Gitea. Seeding first and then force-pushing the mirror is the tested path — the
seed content is a single initial commit that the real history replaces.

## Config Sync Integration

`create-rootsync.sh` sets up two secrets and a RootSync:

- `git-creds` in `config-management-system` — authenticates the RootSync to Gitea
- `git-creds` in `network` — authenticates the RepoSync (applied by root-repo) to Gitea
- `RootSync/networkagent-root` — points Config Sync at `root-repo/main`

The `root-repo` contains a `network-repo-sync.yaml` (rendered from the `.template` during seeding) that creates a `RepoSync` pointing at the `network` repository.

## Teardown

```bash
# Removes the Deployment, Service and secrets. Keeps the disk and the address —
# re-running install.sh brings the repositories back exactly as they were.
cd services/gitea && ./uninstall.sh

# Also deletes the PVC, the retained PersistentVolume and the reserved address.
# Every repository is lost. Take a mirror backup first (see Recovery above).
cd services/gitea && ./uninstall.sh --purge
```
