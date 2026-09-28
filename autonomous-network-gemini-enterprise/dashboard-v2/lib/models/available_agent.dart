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

class AvailableAgent {
  final String name;
  final String url;

  AvailableAgent({
    required this.name,
    required this.url,
  });

  // Create AvailableAgent from JSON
  factory AvailableAgent.fromJson(Map<String, dynamic> json) {
    return AvailableAgent(
      name: json['name'] as String? ?? '',
      url: json['url'] as String,
    );
  }

  // Convert AvailableAgent to JSON
  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'url': url,
    };
  }
}