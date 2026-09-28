#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# seed.sh — push local gitea/repos/* to a running Gitea instance.
# Run this after the Gitea pod is ready (install.sh does it automatically).
#
# Safe to re-run against a populated Gitea: repo creation tolerates 409, and the
# initial-commit push is rejected as a non-fast-forward, which is warned about
# and skipped. That matters now that /data is a persistent disk — before, every
# install started from an empty filesystem and this was the only way repos existed.
#
# Required env vars:
#   GITEA_URL      — e.g. https://34.156.121.236
#   WEBAPPS_LOGIN  — Gitea admin username
#   WEBAPPS_PWD    — Gitea admin password
# ---------------------------------------------------------------------------

GITEA_URL="${GITEA_URL:-}"
WEBAPPS_LOGIN="${WEBAPPS_LOGIN:-}"
WEBAPPS_PWD="${WEBAPPS_PWD:-}"
GOOGLE_PROJECT="${GOOGLE_PROJECT:-}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
REPOS_DIR="$(cd "$(dirname "$0")/repos" && pwd)"

if [ -z "$GITEA_URL" ]; then
  echo "Error: GITEA_URL is required" >&2
  exit 1
fi
if [ -z "$WEBAPPS_LOGIN" ]; then
  echo "Error: WEBAPPS_LOGIN is required" >&2
  exit 1
fi
if [ -z "$WEBAPPS_PWD" ]; then
  read -rsp "Gitea admin password: " WEBAPPS_PWD
  echo ""
fi

# Strip trailing slash
GITEA_URL="${GITEA_URL%/}"

# Derive protocol and host separately for git push URLs
GITEA_PROTO="https"
[[ "$GITEA_URL" == http://* ]] && GITEA_PROTO="http"
GITEA_HOST="${GITEA_URL#https://}"
GITEA_HOST="${GITEA_HOST#http://}"

# Use HTTP Basic auth header so special chars in passwords are safe
BASIC_AUTH=$(printf '%s:%s' "$WEBAPPS_LOGIN" "$WEBAPPS_PWD" | base64 | tr -d '\n')

# Gitea terminates TLS with a self-signed certificate (there is no domain name
# to get a publicly-trusted one for — see deployment.tpl.yaml), so both curl and
# git have to skip verification. Every curl below carries -k; this covers the
# pushes. Matches the noSSLVerify the RootSync already uses.
export GIT_SSL_NO_VERIFY=1

echo "==> Waiting for Gitea to be ready at ${GITEA_URL}..."
i=0
until curl -skf "${GITEA_URL}/api/healthz" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ $i -ge 60 ]; then
    echo "Error: Gitea did not become ready after 60s" >&2
    exit 1
  fi
  sleep 2
done
echo "  Gitea is up."

echo "==> Seeding repositories from ${REPOS_DIR}..."

for repo_dir in "${REPOS_DIR}"/*/; do
  [ -d "$repo_dir" ] || continue
  repo_name=$(basename "$repo_dir")
  echo "  -> ${repo_name}"

  # Create repo (ignore 409 Conflict if it already exists)
  HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "${GITEA_URL}/api/v1/user/repos" \
    -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${repo_name}\",\"private\":false,\"auto_init\":false,\"default_branch\":\"main\"}")
  if [ "$HTTP_STATUS" != "201" ] && [ "$HTTP_STATUS" != "409" ]; then
    echo "     Warning: unexpected status ${HTTP_STATUS} creating '${repo_name}' — skipping"
    continue
  fi

  TMP_DIR=$(mktemp -d)

  if [ -n "$(ls -A "$repo_dir" 2>/dev/null)" ]; then
    cp -r "${repo_dir}/." "$TMP_DIR/"

    # Template substitution for root-repo
    if [ "$repo_name" = "root-repo" ]; then
      echo "     Processing templates..."
      # Substitute GITEA_URL and WEBAPPS_LOGIN in all files
      find "$TMP_DIR" -type f -name "*.template" | while read -r template; do
        output="${template%.template}"
        sed -e "s|\${GITEA_URL}|${GITEA_URL}|g" \
            -e "s|\${WEBAPPS_LOGIN}|${WEBAPPS_LOGIN}|g" \
            -e "s|\${GOOGLE_PROJECT}|${GOOGLE_PROJECT:-}|g" \
            "$template" > "$output"
        rm "$template"
      done
      # Also substitute in .yaml files
      find "$TMP_DIR" -type f -name "*.yaml" -exec \
        sed -i "s|\${GOOGLE_PROJECT}|${GOOGLE_PROJECT:-}|g" {} \;
    fi
  else
    echo "# ${repo_name}" > "${TMP_DIR}/README.md"
  fi

  (
    cd "$TMP_DIR"
    git init -q
    git symbolic-ref HEAD refs/heads/main
    git config user.email 'seed@gitea'
    git config user.name 'Gitea Seed'
    git add -A
    git commit -q -m 'Initial commit'
    git -c "http.extraHeader=Authorization: Basic ${BASIC_AUTH}" \
      push -q --force "${GITEA_PROTO}://${GITEA_HOST}/${WEBAPPS_LOGIN}/${repo_name}.git" HEAD:main
  ) && echo "     pushed" || echo "     Warning: push failed (repo may already have commits)"

  # Create the review branch on the network repo. The designer agent writes CRs
  # here rather than to main; the push hook that sends those commits to the trust
  # agent is registered later by services/gitea-webhook/install.sh, which is the
  # first point at which the webhook URL and the trust engine id both exist.
  if [ "$repo_name" = "network" ]; then
    echo "     Creating update branch..."
    curl -sk -X POST "${GITEA_URL}/api/v1/repos/${WEBAPPS_LOGIN}/${repo_name}/branches" \
      -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
      -H "Content-Type: application/json" \
      -d "{\"new_branch_name\":\"update\",\"old_branch_name\":\"main\"}" > /dev/null || true
  fi

  rm -rf "$TMP_DIR"
done

# Seed the design-document repository
#
# Readme.md and install.sh both advertise a 'networkdesign' repo, and every
# design-doc reader in the tree targets it -- DESIGN_REPO in the designer and
# chaos agents and in both mcp_server_for_ge git_helpers. Nothing created it, so
# getDesignDoc() and getFailureAnalysis() returned "not found" on a fresh
# install. The documents are not duplicated into repos/: they are assembled here
# from their canonical homes so there is one copy to maintain.
echo "==> Seeding design document repository..."
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Flat layout -- the agents read by bare filename, not by path.
DESIGN_DOCS=(
  "${REPO_ROOT}/domains/transport/knowledge/designdoc.md"
  "${REPO_ROOT}/docs/gnn/failure-analysis.md"
  "${REPO_ROOT}/docs/gnn/rca.md"
)

echo "  -> networkdesign"
HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "${GITEA_URL}/api/v1/user/repos" \
  -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"networkdesign\",\"private\":false,\"auto_init\":false,\"default_branch\":\"main\"}")
if [ "$HTTP_STATUS" != "201" ] && [ "$HTTP_STATUS" != "409" ]; then
  echo "     Warning: unexpected status ${HTTP_STATUS} creating 'networkdesign' — skipping"
else
  TMP_DIR=$(mktemp -d)
  for doc in "${DESIGN_DOCS[@]}"; do
    if [ -f "$doc" ]; then
      cp "$doc" "${TMP_DIR}/"
    else
      echo "     Warning: ${doc#"${REPO_ROOT}/"} not found — omitting"
    fi
  done

  (
    cd "$TMP_DIR"
    git init -q
    git symbolic-ref HEAD refs/heads/main
    git config user.email 'seed@gitea'
    git config user.name 'Gitea Seed'
    git add -A
    git commit -q -m 'Initial commit'
    git -c "http.extraHeader=Authorization: Basic ${BASIC_AUTH}" \
      push -q --force "${GITEA_PROTO}://${GITEA_HOST}/${WEBAPPS_LOGIN}/networkdesign.git" HEAD:main
  ) && echo "     pushed" || echo "     Warning: push failed (repo may already have commits)"

  rm -rf "$TMP_DIR"
fi

# Seed domain knowledge repositories
echo "==> Seeding domain knowledge repositories..."
DOMAINS_DIR="$(cd "$(dirname "$0")/../../domains" && pwd)"

for domain in transport core radio service; do
  knowledge_dir="${DOMAINS_DIR}/${domain}/knowledge"

  if [ ! -d "$knowledge_dir" ]; then
    echo "  -> ${domain}: no knowledge directory, skipping"
    continue
  fi

  if [ -z "$(ls -A "$knowledge_dir" 2>/dev/null)" ]; then
    echo "  -> ${domain}: knowledge directory empty, skipping"
    continue
  fi

  repo_name="${domain}"
  echo "  -> ${repo_name}"

  # Create repo (ignore 409 Conflict if it already exists)
  HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "${GITEA_URL}/api/v1/user/repos" \
    -u "${WEBAPPS_LOGIN}:${WEBAPPS_PWD}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${repo_name}\",\"private\":false,\"auto_init\":false,\"default_branch\":\"main\"}")
  if [ "$HTTP_STATUS" != "201" ] && [ "$HTTP_STATUS" != "409" ]; then
    echo "     Warning: unexpected status ${HTTP_STATUS} creating '${repo_name}' — skipping"
    continue
  fi

  TMP_DIR=$(mktemp -d)
  mkdir -p "${TMP_DIR}/knowledge"

  # Copy knowledge files
  cp -r "${knowledge_dir}/"* "${TMP_DIR}/knowledge/"

  # Create README
  cat > "${TMP_DIR}/README.md" <<EOF
# ${domain^} Domain

This repository contains knowledge base and documentation for the ${domain} domain.

## Knowledge Base

See the [\`knowledge/\`](./knowledge) directory for domain-specific documentation.
EOF

  (
    cd "$TMP_DIR"
    git init -q
    git symbolic-ref HEAD refs/heads/main
    git config user.email 'seed@gitea'
    git config user.name 'Gitea Seed'
    git add -A
    git commit -q -m 'Initial commit'
    git -c "http.extraHeader=Authorization: Basic ${BASIC_AUTH}" \
      push -q --force "${GITEA_PROTO}://${GITEA_HOST}/${WEBAPPS_LOGIN}/${repo_name}.git" HEAD:main
  ) && echo "     pushed" || echo "     Warning: push failed (repo may already have commits)"

  rm -rf "$TMP_DIR"
done

echo "==> Seeding complete."
