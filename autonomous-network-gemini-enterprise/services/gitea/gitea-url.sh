#!/bin/bash
#
# gitea-url.sh — print the base URL of the Gitea instance, or nothing.
#
# Gitea runs on GKE behind an L4 load balancer pinned to the reserved regional
# address `gitea-ip` (services/gitea/deployment.tpl.yaml). There is no hostname:
# the address is the identity. Every deploy script needs this same lookup, and
# there used to be six independent copies of the Cloud Run equivalent —
# `gcloud run services describe gitea --format='value(status.url)'` — which is
# exactly the kind of duplication that rots. One copy, here.
#
# Reads GOOGLE_PROJECT and GOOGLE_REGION from the environment; callers source
# config.sh first. Prints nothing and exits 0 when Gitea is not deployed yet,
# because several callers legitimately run before it exists.

set -euo pipefail

ADDRESS_NAME="${GITEA_ADDRESS_NAME:-gitea-ip}"

ip=$(gcloud compute addresses describe "$ADDRESS_NAME" \
  --region="${GOOGLE_REGION}" \
  --project="${GOOGLE_PROJECT}" \
  --format='value(address)' 2>/dev/null || true)

[ -n "$ip" ] && echo "https://${ip}"
exit 0
