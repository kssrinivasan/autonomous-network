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

import 'metric_entry.dart';

/// A comprehensive class to store metrics data including node metrics and users.
// Example of the new Metrics structure:
// {
//   "node_metrics": {
//     "eac8bfd1-f7f5-4a23-98ee-791eaa0d1c80": [
//       List of MetricEntry here (last or all)
//     ],
//     "fe07d625-327c-4b07-bbaf-d635c4c5fee2": [
//       List of MetricEntry here (last or all)
//     ]
//   },
//   "users": {
//     "active_user_sessions": 5,
//     "time_window_seconds": 20,
//     "query_timestamp": timestamp,
//     "threshold_timestamp": timestamp
//   }
// }

class Metrics {
  /// The node metrics data, keyed by node ID.
  final Map<String, List<MetricEntry>> data;
  
  /// The users data, containing active user session information.
  final Map<String, dynamic> users;

  /// Creates a new Metrics instance with the provided data.
  Metrics(this.data, {this.users = const {}});

  /// Creates a new Metrics instance from the provided metrics data.
  factory Metrics.fromJson(dynamic metricsData) {
    Map<String, List<MetricEntry>> parsedData = {};
    Map<String, dynamic> users = {};
    
    if (metricsData is Map) {
      // Handle new structure with node_metrics and users
      if (metricsData.containsKey('node_metrics')) {
        final nodeMetrics = metricsData['node_metrics'];
        if (nodeMetrics is Map) {
          nodeMetrics.forEach((id, metricsList) {
            if (metricsList is List) {
              parsedData[id.toString()] = metricsList.map((metricItem) {
                if (metricItem is Map && metricItem.containsKey('metrics')) {
                  final metricsData = metricItem['metrics'];
                  if (metricsData is Map) {
                    final typedMetricsData = Map<String, dynamic>.from(metricsData);
                    typedMetricsData['timestamp'] = metricItem['timestamp'] ?? 0;
                    return MetricEntry.fromJson(typedMetricsData);
                  }
                }
                return MetricEntry(
                  hostname: '', 
                  interval: 0, 
                  cpu: {}, 
                  interfaces: {}, 
                  timestamp: metricItem is Map ? metricItem['timestamp'] ?? 0 : 0
                );
              }).toList();
            } else {
              parsedData[id.toString()] = [
                MetricEntry(hostname: '', interval: 0, cpu: {}, interfaces: {}, timestamp: 0)
              ];
            }
          });
        }
        
        // Parse users data
        if (metricsData.containsKey('users') && metricsData['users'] is Map) {
          users = Map<String, dynamic>.from(metricsData['users']);
        }
      } else {
        // Handle legacy structure for backward compatibility
        // This handles the case where the data is directly the node metrics without the 'node_metrics' wrapper
        metricsData.forEach((id, metricsList) {
          if (metricsList is List) {
            parsedData[id.toString()] = metricsList.map((metricItem) {
              if (metricItem is Map && metricItem.containsKey('metrics')) {
                final metricsData = metricItem['metrics'];
                if (metricsData is Map) {
                  final typedMetricsData = Map<String, dynamic>.from(metricsData);
                  typedMetricsData['timestamp'] = metricItem['timestamp'] ?? 0;
                  return MetricEntry.fromJson(typedMetricsData);
                }
              }
              return MetricEntry(
                hostname: '', 
                interval: 0, 
                cpu: {}, 
                interfaces: {}, 
                timestamp: metricItem is Map ? metricItem['timestamp'] ?? 0 : 0
              );
            }).toList();
          } else {
            parsedData[id.toString()] = [
              MetricEntry(hostname: '', interval: 0, cpu: {}, interfaces: {}, timestamp: 0)
            ];
          }
        });
      }
    }
    
    return Metrics(parsedData, users: users);
  }
  
  /// Get the number of active users
  int get activeUserCount => users['active_user_sessions'] ?? 0;
}
