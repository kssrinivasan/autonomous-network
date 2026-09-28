#!/bin/bash
set -euo pipefail

WORK_DIR="${GITEA_WORK_DIR:-/data}"
CUSTOM_DIR="${GITEA_CUSTOM:-/data/gitea}"
CONF_DIR="${CUSTOM_DIR}/conf"
APP_INI="${CONF_DIR}/app.ini"

echo "==> Creating Gitea directories..."
mkdir -p "${CONF_DIR}"
mkdir -p "${WORK_DIR}/repositories"
mkdir -p "${WORK_DIR}/data/avatars"
mkdir -p "${WORK_DIR}/log"
chown -R git:git "${WORK_DIR}"

# Only emit the TLS and URL keys when they are actually set. Writing e.g.
# `ROOT_URL =` with an empty value is not the same as omitting it — Gitea takes
# the empty string literally instead of falling back to its computed default.
SERVER_EXTRA=""
[ -n "${GITEA_CERT_FILE:-}" ] && SERVER_EXTRA="${SERVER_EXTRA}CERT_FILE = ${GITEA_CERT_FILE}"$'\n'
[ -n "${GITEA_KEY_FILE:-}" ]  && SERVER_EXTRA="${SERVER_EXTRA}KEY_FILE  = ${GITEA_KEY_FILE}"$'\n'
[ -n "${GITEA_ROOT_URL:-}" ]  && SERVER_EXTRA="${SERVER_EXTRA}ROOT_URL  = ${GITEA_ROOT_URL}"$'\n'

echo "==> Writing app.ini..."
cat > "${APP_INI}" << EOF
[DEFAULT]
RUN_USER = git
RUN_MODE = prod

[server]
PROTOCOL         = ${GITEA_PROTOCOL:-http}
HTTP_PORT        = ${PORT:-8080}
DOMAIN           = ${GITEA_DOMAIN:-localhost}
${SERVER_EXTRA}DISABLE_SSH      = true
START_SSH_SERVER = false
OFFLINE_MODE     = false

[database]
DB_TYPE = sqlite3
PATH    = ${WORK_DIR}/gitea.db

[repository]
ROOT = ${WORK_DIR}/repositories

[security]
INSTALL_LOCK   = true
SECRET_KEY     = ${GITEA_SECRET_KEY:-please-change-me}
INTERNAL_TOKEN = ${GITEA_INTERNAL_TOKEN:-}

[service]
DISABLE_REGISTRATION              = true
REQUIRE_SIGNIN_VIEW               = true
REGISTER_EMAIL_CONFIRM            = false
ENABLE_NOTIFY_MAIL                = false
ALLOW_ONLY_EXTERNAL_REGISTRATION  = false
NO_REPLY_ADDRESS                  = noreply@${GITEA_DOMAIN:-localhost}

[log]
MODE  = console
LEVEL = Info

[session]
PROVIDER = memory

[cache]
ADAPTER = memory
EOF

chown git:git "${APP_INI}"

echo "==> Running Gitea DB migration..."
su -s /bin/sh git -c "gitea migrate --config ${APP_INI}"

if [ -n "${GITEA_ADMIN_USER:-}" ]; then
  echo "==> Creating admin user '${GITEA_ADMIN_USER}'..."
  su -s /bin/sh git -c "
    gitea admin user create \
      --config ${APP_INI} \
      --username '${GITEA_ADMIN_USER}' \
      --password '${GITEA_ADMIN_PASSWORD:-changeme}' \
      --email '${GITEA_ADMIN_EMAIL:-admin@example.com}' \
      --admin \
      --must-change-password=false
  " 2>&1 | grep -v "already exists" || true
fi

echo "==> Initialization complete. Starting Gitea..."
exec /usr/bin/entrypoint
