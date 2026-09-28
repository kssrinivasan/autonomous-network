// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import 'package:flutter/foundation.dart';

// Environment configuration
class EnvironmentConfig {
  // Default values that can be overridden
  static String gcpProject = const String.fromEnvironment('GCP_PROJECT', defaultValue: 'unknown-project');
  static String giteaUrl = const String.fromEnvironment('GITEA_URL', defaultValue: 'https://gitea.example.com');
  static String get agentUrl {
    const rawUrl = const String.fromEnvironment('NETWORKAGENT_URL', defaultValue: 'http://127.0.0.1:9000');
    if (kIsWeb) {
      if (rawUrl.isEmpty || rawUrl.startsWith('http://127.0.0.1') || rawUrl.startsWith('http://localhost')) {
        final origin = Uri.base.origin;
        return origin.endsWith('/') ? origin.substring(0, origin.length - 1) : origin;
      }
    }
    return rawUrl;
  }

  static String trainGNNUrl = const String.fromEnvironment('TRAIN_GNN_URI', defaultValue: 'http://127.0.0.1:8081');
  static String serveGNNUrl = const String.fromEnvironment('SERVE_GNN_URI', defaultValue: 'http://127.0.0.1:8082');
  // There is deliberately no username/password here. Access control is IAP in
  // front of the Cloud Run service; a credential compiled into the web bundle
  // is readable by anyone who can fetch the bundle and skippable by anyone who
  // can open a debugger, so it added no security and leaked the password.
  static String reasoningEngineId = const String.fromEnvironment(
      'REASONING_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_SUPERVISOR_ENGINE_ID');
  static String detectiveEngineId = const String.fromEnvironment(
      'DETECTIVE_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_DETECTIVE_ENGINE_ID');
  static String impactEngineId = const String.fromEnvironment(
      'IMPACT_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_IMPACT_ENGINE_ID');
  static String designerEngineId = const String.fromEnvironment(
      'DESIGNER_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_DESIGNER_ENGINE_ID');
  static String testerEngineId = const String.fromEnvironment(
      'TESTER_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_TESTER_ENGINE_ID');
  static String chaosEngineId = const String.fromEnvironment(
      'CHAOS_ENGINE_ID',
      defaultValue: 'projects/YOUR_PROJECT_ID/locations/YOUR_REGION/reasoningEngines/YOUR_CHAOS_ENGINE_ID');
  static String aiPlatformApiKey = const String.fromEnvironment('AI_PLATFORM_API_KEY', defaultValue: '');
}
