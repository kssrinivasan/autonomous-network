#!/bin/bash
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

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Source environment
if [ -f "$REPO_ROOT/config.sh" ]; then
    source "$REPO_ROOT/config.sh"
else
    echo "Error: config.sh not found"
    exit 1
fi

echo "========================================="
echo "Transport Intent API Server - Cleanup"
echo "========================================="
echo ""

# Delete Cloud Run service
echo "Deleting Cloud Run service..."
gcloud run services delete transport-intentserver \
    --region=$GOOGLE_REGION \
    --quiet || echo "Service not found or already deleted"

echo ""
echo "Transport Intent API Server uninstalled successfully!"
echo ""
