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

class Agent {
  final String id;
  final String name;
  final String description;
  final String url;

  Agent({
    required this.id,
    this.name = '',
    required this.description,
    required this.url,
  });

  // Create a copy of this agent with updated fields
  Agent copyWith({
    String? id,
    String? name,
    String? description,
    String? url,
  }) {
    return Agent(
      id: id ?? this.id,
      name: name ?? this.name,
      description: description ?? this.description,
      url: url ?? this.url,
    );
  }

  // Convert Agent to JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'description': description,
      'url': url,
    };
  }

  // Create Agent from JSON
  factory Agent.fromJson(Map<String, dynamic> json) {
    return Agent(
      id: json['id'] as String,
      name: json['name'] as String? ?? '',
      description: json['description'] as String,
      url: json['url'] as String,
    );
  }
}
