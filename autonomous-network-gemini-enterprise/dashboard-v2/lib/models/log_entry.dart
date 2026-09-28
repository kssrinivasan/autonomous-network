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

class LogEntry {
  final String timestamp;
  final String severity;
  final String message;
  final String source;
  final Map<String, dynamic> details;

  LogEntry({
    required this.timestamp,
    required this.severity,
    required this.message,
    required this.source,
    this.details = const {},
  });

  factory LogEntry.fromJson(Map<String, dynamic> json) {
    return LogEntry(
      timestamp: json['timestamp'] ?? DateTime.now().toIso8601String(),
      // Use 'severity' but fall back to 'level' for backward compatibility.
      severity: json['severity'] ?? json['level'] ?? 'UNKNOWN',
      message: json['message'] ?? '',
      source: json['source'] ?? 'Unknown',
      details: json['details'] ?? {},
    );
  }
}
