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

class NetworkDescriptor {
  final String id;
  final String name;
  final String description;
  final Map<String, dynamic> labels;
  final String? updatedAt;

  const NetworkDescriptor({
    required this.id,
    required this.name,
    required this.description,
    required this.labels,
    this.updatedAt,
  });

  factory NetworkDescriptor.fromJson(Map<String, dynamic> json) {
    return NetworkDescriptor(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      description: json['description'] as String? ?? '',
      labels: json['labels'] is Map
          ? Map<String, dynamic>.from(json['labels'] as Map)
          : {},
      updatedAt: json['updated_at'] as String?,
    );
  }

  /// Human-readable last-updated string (e.g. "2026-04-23 11:59 UTC").
  String get formattedUpdatedAt {
    if (updatedAt == null) return '—';
    try {
      final dt = DateTime.parse(updatedAt!).toUtc();
      String pad(int n) => n.toString().padLeft(2, '0');
      return '${dt.year}-${pad(dt.month)}-${pad(dt.day)} '
          '${pad(dt.hour)}:${pad(dt.minute)} UTC';
    } catch (_) {
      return updatedAt!;
    }
  }
}
