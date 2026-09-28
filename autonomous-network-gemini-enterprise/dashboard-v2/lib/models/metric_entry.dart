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

// generat a dart class called Metrics entry that receives
// a JSON like the one below and turns it into dart object
// with attribute to access it
//
// Example of a MetricEntry
// {
//   "hostname": "ljulliard1",
//   "interval": 5,
//   "cpu": {
//     "cpu_percent": 6.2
//   },
//   "interfaces": {
//     "wlp0s20f3": {
//       "byte_sent": 2023948021,
//       "byte_sent_delta": 0,
//       "byte_recv": 11340335143,
//       "byte_recv_delta": 0,
//       "byte_sent_throughput": 0.0,
//       "byte_recv_throughput": 0.0
//     },
//     "enx00e04c6845c8": {
//       "byte_sent": 3107597262,
//       "byte_sent_delta": 91950,
//       "byte_recv": 8613681786,
//       "byte_recv_delta": 79914,
//       "byte_sent_throughput": 18390.0,
//       "byte_recv_throughput": 15982.8
//     }
//   },
//   "timestamp": 1745436168
// }
class MetricEntry {
  final String hostname;
  final int interval;
  final Map<String, dynamic> cpu;
  final Map<String, dynamic> interfaces;
  final int timestamp;

  MetricEntry({
    required this.hostname,
    required this.interval,
    required this.cpu,
    required this.interfaces,
    required this.timestamp,
  });

  factory MetricEntry.fromJson(Map<String, dynamic> json) {
    // Normalize interface data to map backend field names to UI expectations
    Map<String, dynamic> normalizedInterfaces = {};
    final rawInterfaces = json['interfaces'] ?? {};
    
    if (rawInterfaces is Map) {
      rawInterfaces.forEach((interfaceName, interfaceData) {
        if (interfaceData is Map) {
          // Create a copy of the interface data
          final normalizedData = Map<String, dynamic>.from(interfaceData);
          
          // Map backend field names to UI field names
          // Backend provides: transmit_bytes_total, receive_bytes_total (already as rates)
          // UI expects: byte_sent_throughput, byte_recv_throughput
          if (normalizedData.containsKey('transmit_bytes_total')) {
            normalizedData['byte_sent_throughput'] = normalizedData['transmit_bytes_total'];
          }
          if (normalizedData.containsKey('receive_bytes_total')) {
            normalizedData['byte_recv_throughput'] = normalizedData['receive_bytes_total'];
          }
          
          normalizedInterfaces[interfaceName] = normalizedData;
        } else {
          normalizedInterfaces[interfaceName] = interfaceData;
        }
      });
    }
    
    return MetricEntry(
      hostname: json['hostname'] ?? '',
      interval: json['interval'] ?? 0,
      cpu: json['cpu'] ?? {},
      interfaces: normalizedInterfaces,
      timestamp: json['timestamp'] ?? 0,
    );
  }
}

