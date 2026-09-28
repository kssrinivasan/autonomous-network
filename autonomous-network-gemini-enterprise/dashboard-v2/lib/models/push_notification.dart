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

/// Represents a push notification from the supervisor agent
class PushNotification {
  /// Unique identifier for the notification
  final String id;
  
  /// Name of the notification
  final String name;
  
  /// State of the notification (e.g., 'input_required', 'completed', etc.)
  final String state;
  
  /// Task ID associated with the notification (optional)
  final String? taskId;
  
  /// Context ID associated with the notification (optional)
  final String? contextId;
  
  /// Content/message of the notification
  final String content;
  
  /// Timestamp when the notification was received
  final DateTime timestamp;
  
  /// Whether the notification has been read by the user
  final bool isRead;

  /// Additional data for notifications that require user input
  final Map<String, dynamic>? inputData;

  /// Creates a new push notification
  const PushNotification({
    required this.id,
    required this.name,
    required this.state,
    this.taskId,
    this.contextId,
    required this.content,
    required this.timestamp,
    this.isRead = false,
    this.inputData,
  });

  /// Creates a copy of this notification with the given fields replaced with new values
  PushNotification copyWith({
    String? id,
    String? name,
    String? state,
    String? taskId,
    String? contextId,
    String? content,
    DateTime? timestamp,
    bool? isRead,
    Map<String, dynamic>? inputData,
  }) {
    return PushNotification(
      id: id ?? this.id,
      name: name ?? this.name,
      state: state ?? this.state,
      taskId: taskId ?? this.taskId,
      contextId: contextId ?? this.contextId,
      content: content ?? this.content,
      timestamp: timestamp ?? this.timestamp,
      isRead: isRead ?? this.isRead,
      inputData: inputData ?? this.inputData,
    );
  }

  /// Creates a notification from a JSON object
  factory PushNotification.fromJson(Map<String, dynamic> json) {
    return PushNotification(
      id: json['id'] ?? DateTime.now().millisecondsSinceEpoch.toString(),
      name: json['name'] ?? 'Notification',
      state: json['state'] ?? 'unknown',
      taskId: json['task_id'],
      contextId: json['context_id'],
      content: json['content'] ?? '',
      timestamp: json['timestamp'] != null
          ? DateTime.parse(json['timestamp'])
          : DateTime.now(),
      isRead: json['isRead'] ?? false,
      inputData: json['input_data'] != null
          ? Map<String, dynamic>.from(json['input_data'])
          : null,
    );
  }

  /// Converts this notification to a JSON object
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'state': state,
      'task_id': taskId,
      'context_id': contextId,
      'content': content,
      'timestamp': timestamp.toIso8601String(),
      'isRead': isRead,
      'input_data': inputData,
    };
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is PushNotification &&
        other.id == id &&
        other.name == name &&
        other.state == state &&
        other.taskId == taskId &&
        other.contextId == contextId &&
        other.content == content &&
        other.timestamp == timestamp &&
        other.isRead == isRead &&
        mapEquals(other.inputData, inputData);
  }

  @override
  int get hashCode {
    return Object.hash(
      id,
      name,
      state,
      taskId,
      contextId,
      content,
      timestamp,
      isRead,
      inputData,
    );
  }

  @override
  String toString() {
    return 'PushNotification(id: $id, name: $name, state: $state, taskId: $taskId, contextId: $contextId, content: $content, timestamp: $timestamp, isRead: $isRead, inputData: $inputData)';
  }
}
