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

import 'package:flutter/material.dart';

/// Summary of a deployed VyOSUnderlay custom resource.
class VyosUnderlayInfo {
  final String name;
  final String phase;
  final String message;

  const VyosUnderlayInfo({
    required this.name,
    required this.phase,
    required this.message,
  });

  factory VyosUnderlayInfo.fromJson(Map<String, dynamic> json) {
    return VyosUnderlayInfo(
      name:    json['name']    as String? ?? '',
      phase:   json['phase']   as String? ?? 'Unknown',
      message: json['message'] as String? ?? '',
    );
  }

  /// Returns a color representing the current phase.
  Color get phaseColor {
    switch (phase.toLowerCase()) {
      case 'ready':
        return Colors.green;
      case 'processing':
      case 'waiting':
        return Colors.orange;
      case 'error':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }
}

/// Summary of a deployed VyosInfrastructure custom resource.
class VyosInfrastructureInfo {
  final String name;
  final String phase;
  final String message;
  final int routerCount;
  final int networkCount;
  final int deviceCount;
  final List<VyosUnderlayInfo> underlays;

  const VyosInfrastructureInfo({
    required this.name,
    required this.phase,
    required this.message,
    required this.routerCount,
    required this.networkCount,
    required this.deviceCount,
    this.underlays = const [],
  });

  factory VyosInfrastructureInfo.fromJson(Map<String, dynamic> json) {
    final underlaysList = (json['underlays'] as List<dynamic>? ?? [])
        .whereType<Map<String, dynamic>>()
        .map(VyosUnderlayInfo.fromJson)
        .toList();

    return VyosInfrastructureInfo(
      name:         json['name']          as String? ?? '',
      phase:        json['phase']         as String? ?? 'Unknown',
      message:      json['message']       as String? ?? '',
      routerCount:  json['router_count']  as int?    ?? 0,
      networkCount: json['network_count'] as int?    ?? 0,
      deviceCount:  json['device_count']  as int?    ?? 0,
      underlays:    underlaysList,
    );
  }

  /// Returns a color representing the current phase.
  Color get phaseColor {
    switch (phase.toLowerCase()) {
      case 'ready':
        return Colors.green;
      case 'creating':
      case 'processing':
      case 'validating':
        return Colors.orange;
      case 'error':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }

  /// A compact summary line, e.g. "3 routers · 2 networks · 1 device"
  String get resourceSummary {
    final parts = <String>[];
    if (routerCount > 0) {
      parts.add('$routerCount router${routerCount == 1 ? '' : 's'}');
    }
    if (networkCount > 0) {
      parts.add('$networkCount network${networkCount == 1 ? '' : 's'}');
    }
    if (deviceCount > 0) {
      parts.add('$deviceCount device${deviceCount == 1 ? '' : 's'}');
    }
    return parts.isEmpty ? 'No resources' : parts.join(' · ');
  }
}
