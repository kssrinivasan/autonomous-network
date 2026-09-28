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

// Models for VyOSL3VPN and TrafficTest Kubernetes CRDs as returned by
// the supervisor REST API (/vpns and /traffictests endpoints).

// ---------------------------------------------------------------------------
// VPN helper models
// ---------------------------------------------------------------------------

/// Per-router VRF entry with RD / RT routing policy info.
class VpnRouterVrf {
  final String router;
  final String vrf;
  final String? rd;
  final List<String> rtExport;
  final List<String> rtImport;
  final String? description;

  const VpnRouterVrf({
    required this.router,
    required this.vrf,
    this.rd,
    required this.rtExport,
    required this.rtImport,
    this.description,
  });

  factory VpnRouterVrf.fromJson(Map<String, dynamic> json) {
    List<String> toStrings(dynamic v) =>
        v is List ? List<String>.from(v.whereType<String>()) : [];
    return VpnRouterVrf(
      router: json['router'] as String? ?? '',
      vrf: json['vrf'] as String? ?? '',
      rd: json['rd'] as String?,
      rtExport: toStrings(json['rt_export']),
      rtImport: toStrings(json['rt_import']),
      description: json['description'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'router': router,
        'vrf': vrf,
        'rd': rd,
        'rt_export': rtExport,
        'rt_import': rtImport,
        'description': description,
      };
}

/// Service-level VPN metadata (topology type, service-scoped RD/RT, description).
class VpnService {
  final String name;
  final String? type;
  final String? topology;
  final String? rd;
  final String? rtExport;
  final String? rtImport;
  final String? description;

  const VpnService({
    required this.name,
    this.type,
    this.topology,
    this.rd,
    this.rtExport,
    this.rtImport,
    this.description,
  });

  factory VpnService.fromJson(Map<String, dynamic> json) => VpnService(
        name: json['name'] as String? ?? '',
        type: json['type'] as String?,
        topology: json['topology'] as String?,
        rd: json['rd'] as String?,
        rtExport: json['rt_export'] as String?,
        rtImport: json['rt_import'] as String?,
        description: json['description'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'name': name,
        'type': type,
        'topology': topology,
        'rd': rd,
        'rt_export': rtExport,
        'rt_import': rtImport,
        'description': description,
      };
}

// ---------------------------------------------------------------------------
// VpnInfo
// ---------------------------------------------------------------------------

class VpnInfo {
  final String name;
  final String phase;
  final String message;

  /// Names of routers involved in this VPN (PE + CE).
  final List<String> routers;
  final String? underlayRef;

  /// Per-router VRF entries with RD / RT info (PE routers only).
  final List<VpnRouterVrf> routerVrfs;

  /// Service-level metadata from spec.services[].
  final List<VpnService> services;

  const VpnInfo({
    required this.name,
    required this.phase,
    required this.message,
    required this.routers,
    this.underlayRef,
    this.routerVrfs = const [],
    this.services = const [],
  });

  factory VpnInfo.fromJson(Map<String, dynamic> json) {
    final routersList = json['routers'];
    final vrfsList = json['router_vrfs'];
    final svcList = json['services'];
    return VpnInfo(
      name: json['name'] as String? ?? 'unknown',
      phase: json['phase'] as String? ?? 'Unknown',
      message: json['message'] as String? ?? '',
      routers: routersList is List
          ? List<String>.from(routersList.whereType<String>())
          : [],
      underlayRef: json['underlay_ref'] as String?,
      routerVrfs: vrfsList is List
          ? vrfsList
              .whereType<Map<String, dynamic>>()
              .map(VpnRouterVrf.fromJson)
              .toList()
          : [],
      services: svcList is List
          ? svcList
              .whereType<Map<String, dynamic>>()
              .map(VpnService.fromJson)
              .toList()
          : [],
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'phase': phase,
        'message': message,
        'routers': routers,
        'underlay_ref': underlayRef,
        'router_vrfs': routerVrfs.map((v) => v.toJson()).toList(),
        'services': services.map((s) => s.toJson()).toList(),
      };

  /// True if there is any RD/RT routing info available to display.
  bool get hasRouteInfo => routerVrfs.isNotEmpty;
}

// ---------------------------------------------------------------------------
// TrafficTestInfo
// ---------------------------------------------------------------------------

class TrafficTestInfo {
  final String name;
  final String phase;
  final String message;
  final String? vpnRef;
  final List<String> sourceDevices;
  final String? destinationDevice;

  /// Network protocol: TCP or UDP (from spec.protocol).
  final String? protocol;

  /// Target bandwidth string, e.g. "100Mbps" (from spec.bandwidth).
  final String? bandwidth;

  /// Traffic pattern type, e.g. "constant", "burst", "multi_sine" (from spec.pattern_type).
  final String? patternType;

  final int duration;
  final bool bidirectional;
  final int sourceCount;
  final String? startTime;
  final String? endTime;
  final List<int> allocatedPorts;

  const TrafficTestInfo({
    required this.name,
    required this.phase,
    required this.message,
    this.vpnRef,
    required this.sourceDevices,
    this.destinationDevice,
    this.protocol,
    this.bandwidth,
    this.patternType,
    required this.duration,
    this.bidirectional = false,
    required this.sourceCount,
    this.startTime,
    this.endTime,
    required this.allocatedPorts,
  });

  factory TrafficTestInfo.fromJson(Map<String, dynamic> json) {
    final srcDevices = json['source_devices'];
    final ports = json['allocated_ports'];
    return TrafficTestInfo(
      name: json['name'] as String? ?? 'unknown',
      phase: json['phase'] as String? ?? 'Unknown',
      message: json['message'] as String? ?? '',
      vpnRef: json['vpn_ref'] as String?,
      sourceDevices: srcDevices is List
          ? List<String>.from(srcDevices.whereType<String>())
          : [],
      destinationDevice: json['destination_device'] as String?,
      protocol: json['protocol'] as String?,
      bandwidth: json['bandwidth'] as String?,
      patternType: json['pattern_type'] as String?,
      duration: (json['duration'] as num?)?.toInt() ?? 60,
      bidirectional: json['bidirectional'] as bool? ?? false,
      sourceCount: (json['source_count'] as num?)?.toInt() ?? 0,
      startTime: json['start_time'] as String?,
      endTime: json['end_time'] as String?,
      allocatedPorts: ports is List
          ? List<int>.from(ports.whereType<num>().map((n) => n.toInt()))
          : [],
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'phase': phase,
        'message': message,
        'vpn_ref': vpnRef,
        'source_devices': sourceDevices,
        'destination_device': destinationDevice,
        'protocol': protocol,
        'bandwidth': bandwidth,
        'pattern_type': patternType,
        'duration': duration,
        'bidirectional': bidirectional,
        'source_count': sourceCount,
        'start_time': startTime,
        'end_time': endTime,
        'allocated_ports': allocatedPorts,
      };

  /// All device names associated with this test (sources + destination).
  List<String> get allDeviceNames {
    final devices = List<String>.from(sourceDevices);
    if (destinationDevice != null) {
      devices.add(destinationDevice!);
    }
    return devices;
  }

  /// Whether the test is currently active (running or deploying).
  bool get isActive =>
      phase == 'Running' || phase == 'Deploying' || phase == 'Pending';

  /// Compact summary of traffic parameters for display: e.g. "TCP • 100Mbps • constant"
  String get trafficSummary {
    final parts = <String>[];
    if (protocol != null) parts.add(protocol!);
    if (bandwidth != null) parts.add(bandwidth!);
    if (patternType != null) parts.add(patternType!);
    return parts.join(' • ');
  }
}

// ---------------------------------------------------------------------------
// TrafficFlowMetrics — latest traffic-agent measurements for one flow
// ---------------------------------------------------------------------------

/// Latest traffic-agent metrics for a single flow (one entry per flow_id).
///
/// Returned by  GET /traffictests/{name}/metrics  and stored in Spanner's
/// NetworkMetrics table with  kind = 'TRAFFIC'.
class TrafficFlowMetrics {
  final String flowId;
  final String device;
  final String role;
  final String protocol;

  /// Bidirectional throughput in bits-per-second (sent + received, gauge).
  /// Kept for backward compatibility — prefer [throughputSentBps] or
  /// [throughputRecvBps] for unidirectional accounting.
  final double? throughputBps;

  /// Outbound throughput in bits-per-second (bytes sent only, gauge).
  /// Use this for source-role flows to avoid double-counting bidirectional traffic.
  final double? throughputSentBps;

  /// Inbound throughput in bits-per-second (bytes received only, gauge).
  /// Use this for destination-role flows.
  final double? throughputRecvBps;

  /// One-way latency in milliseconds (gauge).
  final double? latencyMs;

  /// Jitter in milliseconds (gauge).
  final double? jitterMs;

  /// Packet-loss percentage 0–100 (gauge).
  final double? packetLossPct;

  /// Number of active TCP/UDP sessions (gauge).
  final double? activeSessions;

  /// Cumulative bytes sent (counter, reported as a rate by the collector).
  final double? bytesSentTotal;

  /// Cumulative bytes received (counter, reported as a rate by the collector).
  final double? bytesReceivedTotal;

  /// 1 = flow is running, 0 = stopped (gauge).
  final double? flowRunning;

  /// ISO-8601 timestamp of the most recent data point.
  final String? timestamp;

  const TrafficFlowMetrics({
    required this.flowId,
    required this.device,
    required this.role,
    required this.protocol,
    this.throughputBps,
    this.throughputSentBps,
    this.throughputRecvBps,
    this.latencyMs,
    this.jitterMs,
    this.packetLossPct,
    this.activeSessions,
    this.bytesSentTotal,
    this.bytesReceivedTotal,
    this.flowRunning,
    this.timestamp,
  });

  factory TrafficFlowMetrics.fromJson(Map<String, dynamic> json) {
    double? _d(dynamic v) =>
        v == null ? null : (v as num).toDouble();
    return TrafficFlowMetrics(
      flowId:             json['flow_id']    as String? ?? '',
      device:             json['device']     as String? ?? '',
      role:               json['role']       as String? ?? '',
      protocol:           json['protocol']   as String? ?? '',
      throughputBps:      _d(json['throughput_bps']),
      throughputSentBps:  _d(json['throughput_sent_bps']),
      throughputRecvBps:  _d(json['throughput_recv_bps']),
      latencyMs:          _d(json['latency_ms']),
      jitterMs:           _d(json['jitter_ms']),
      packetLossPct:      _d(json['packet_loss_pct']),
      activeSessions:     _d(json['active_sessions']),
      bytesSentTotal:     _d(json['bytes_sent_total']),
      bytesReceivedTotal: _d(json['bytes_received_total']),
      flowRunning:        _d(json['flow_running']),
      timestamp:          json['timestamp']  as String?,
    );
  }

  /// True when the flow_running gauge is 1.
  bool get isRunning => (flowRunning ?? 0) >= 0.5;

  /// Throughput formatted for display (e.g. "82.3 Mbps", "1.2 Gbps").
  String get throughputLabel {
    if (throughputBps == null) return '—';
    final bps = throughputBps!;
    if (bps >= 1e9) return '${(bps / 1e9).toStringAsFixed(1)} Gbps';
    if (bps >= 1e6) return '${(bps / 1e6).toStringAsFixed(1)} Mbps';
    if (bps >= 1e3) return '${(bps / 1e3).toStringAsFixed(0)} Kbps';
    return '${bps.toStringAsFixed(0)} bps';
  }
}
