#!/usr/bin/env bash

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -e

echo "📋 Master FastMCP Dual-Service Rollout Orchestrator"
echo "==================================================="

# Deploy Gemini Enterprise Data Engine microservice
echo -e "\n🚀 [1/2] Deploying Gemini Enterprise Data Engine (ge/)..."
cd ge && bash deploy.sh
cd ..

# Deploy Vertex AI Reasoning Engine microservice
echo -e "\n🚀 [2/2] Deploying Vertex AI Reasoning Engine SSE microservice (re/)..."
cd re && bash deploy.sh
cd ..

echo -e "\n🎉 Both dedicated FastMCP microservices successfully deployed and routed in Cloud Run!"
