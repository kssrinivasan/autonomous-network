#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# create-rootsync.sh — Create RootSync CR pointing to gitea root-repo
#
# Required env vars:
#   GITEA_URL      — e.g. https://34.156.121.236 (the reserved 'gitea-ip' address;
#                    `services/gitea/gitea-url.sh` prints it)
#   WEBAPPS_LOGIN  — Gitea admin username
#   WEBAPPS_PWD    — Gitea admin password
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../config.sh
. "$SCRIPT_DIR/../../config.sh"

GITEA_URL="${GITEA_URL:?GITEA_URL is required}"
: "${WEBAPPS_LOGIN:?WEBAPPS_LOGIN is required}"
: "${WEBAPPS_PWD:?WEBAPPS_PWD is required}"

section "Gitea — Creating git credentials secret"

# Create or update git-creds secret for ConfigSync authentication
kubectl create secret generic git-creds \
  --namespace=config-management-system \
  --from-literal=username="${WEBAPPS_LOGIN}" \
  --from-literal=token="${WEBAPPS_PWD}" \
  --dry-run=client -o yaml | kubectl apply -f -

if [ $? -eq 0 ]; then
  ok "Secret 'git-creds' created/updated in config-management-system"
else
  error "Failed to create git-creds secret"
  exit 1
fi

section "Gitea — Creating RootSync CR"

# Create RootSync CR pointing to root-repo
cat <<EOF | kubectl apply -f -
# Copyright 2024-2028 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

apiVersion: configsync.gke.io/v1beta1
kind: RootSync
metadata:
  name: networkagent-root
  namespace: config-management-system
spec:
  sourceType: git
  sourceFormat: unstructured
  git:
    repo: ${GITEA_URL}/${WEBAPPS_LOGIN}/root-repo
    branch: main
    auth: token
    secretRef:
      name: git-creds
    noSSLVerify: true
EOF

if [ $? -eq 0 ]; then
  ok "RootSync 'networkagent-root' created"
else
  error "Failed to create RootSync"
  exit 1
fi

# Wait for RootSync to be synced (with timeout)
echo "  Waiting for RootSync to sync..."
for i in {1..30}; do
  STATUS=$(kubectl get rootsync networkagent-root -n config-management-system \
    -o jsonpath='{.status.conditions[?(@.type=="Syncing")].status}' 2>/dev/null || echo "")

  if [ "$STATUS" = "False" ]; then
    ok "RootSync synced successfully"
    break
  fi

  if [ $i -eq 30 ]; then
    warn "RootSync sync timeout — check with: kubectl get rootsync -n config-management-system"
  fi

  sleep 2
done

section "Gitea — Creating git credentials for namespace sync"

# Create git-creds secret in network namespace for RepoSync
kubectl create secret generic git-creds \
  --namespace=network \
  --from-literal=username="${WEBAPPS_LOGIN}" \
  --from-literal=token="${WEBAPPS_PWD}" \
  --dry-run=client -o yaml | kubectl apply -f -

if [ $? -eq 0 ]; then
  ok "Secret 'git-creds' created/updated in network namespace"
else
  warn "Failed to create git-creds in network namespace — RepoSync will fail until this is fixed"
fi
