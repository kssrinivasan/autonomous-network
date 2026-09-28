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

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import '../../models/panel_type.dart';
import '../../screens/full_screen_panel_view.dart';
import '../../utils/APIService.dart';
import '../../models/metrics.dart';
import '../../models/metric_entry.dart';

class PerformanceGraphWidget extends StatefulWidget {
  final socket;
  final bool isLoading;
  final bool isFullScreen;

  const PerformanceGraphWidget({
    super.key,
    required this.socket,
    this.isLoading = false,
    this.isFullScreen = false,
  });

  @override
  State<PerformanceGraphWidget> createState() => _PerformanceGraphWidgetState();
}

class _PerformanceGraphWidgetState extends State<PerformanceGraphWidget> {
  final APIService _apiService = APIService();
  Timer? _pollingTimer;
  Metrics? _metrics;
  bool _isLoadingMetrics = true;
  String? _errorMessage;

  // Track which routers are expanded to show per-interface detail
  final Set<String> _expandedRouters = {};

  @override
  void initState() {
    super.initState();
    _fetchMetrics();
    // Auto-refresh every 20 seconds
    _pollingTimer = Timer.periodic(const Duration(seconds: 20), (_) {
      _fetchMetrics();
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchMetrics() async {
    try {
      // Use getAllMetrics() to get the full historical data, not just the latest snapshot
      final metrics = await _apiService.getAllMetrics();

      if (mounted) {
        setState(() {
          _metrics = metrics;
          _isLoadingMetrics = false;
          _errorMessage = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoadingMetrics = false;
          _errorMessage = e.toString();
        });
      }
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Data helpers
  // ──────────────────────────────────────────────────────────────────────────

  /// Returns throughput time-series for a router: list of (timestamp, upload, download).
  List<Map<String, dynamic>> _buildTimeSeries(List<MetricEntry> entries) {
    final series = <Map<String, dynamic>>[];
    for (final entry in entries) {
      double upload = 0.0;
      double download = 0.0;
      bool hasData = false;

      entry.interfaces.forEach((_, ifData) {
        if (ifData is Map) {
          final tx = (ifData['byte_sent_throughput'] as num?)?.toDouble();
          final rx = (ifData['byte_recv_throughput'] as num?)?.toDouble();
          if (tx != null) { upload += tx; hasData = true; }
          if (rx != null) { download += rx; hasData = true; }
        }
      });

      if (hasData || series.isEmpty) {
        series.add({
          'timestamp': entry.timestamp,
          'upload': upload,
          'download': download,
          'total': upload + download,
        });
      }
    }
    return series;
  }

  /// Aggregated stats from time-series: latest upload/download, peak, avg, hasIssues.
  Map<String, dynamic> _calcStats(List<Map<String, dynamic>> series) {
    if (series.isEmpty) {
      return {
        'latestUpload': null,
        'latestDownload': null,
        'peakTotal': 0.0,
        'avgTotal': 0.0,
        'hasIssues': false,
        'hasData': false,
        'timestamp': 0,
      };
    }

    final last = series.last;
    double peakTotal = 0.0;
    double sumTotal = 0.0;
    bool hasIssues = false;

    for (final point in series) {
      final total = point['total'] as double;
      if (total > peakTotal) peakTotal = total;
      sumTotal += total;
      if (total > 1e9) hasIssues = true; // > 1 GB/s
    }

    final latestUpload = last['upload'] as double;
    final latestDownload = last['download'] as double;

    return {
      'latestUpload': latestUpload,
      'latestDownload': latestDownload,
      'peakTotal': peakTotal,
      'avgTotal': sumTotal / series.length,
      'hasIssues': hasIssues,
      'hasData': latestUpload > 0 || latestDownload > 0,
      'timestamp': last['timestamp'] as int,
    };
  }

  Map<String, dynamic> _calculateNetworkSummary() {
    if (_metrics == null || _metrics!.data.isEmpty) {
      return {
        'totalRouters': 0,
        'totalUpload': 0.0,
        'totalDownload': 0.0,
        'totalInterfaces': 0,
        'activeInterfaces': 0,
        'routersWithIssues': 0,
      };
    }

    double totalUpload = 0.0;
    double totalDownload = 0.0;
    int totalInterfaces = 0;
    int activeInterfaces = 0;
    int routersWithIssues = 0;

    _metrics!.data.forEach((routerName, entries) {
      final series = _buildTimeSeries(entries);
      final stats = _calcStats(series);

      if (stats['latestUpload'] != null) totalUpload += stats['latestUpload'] as double;
      if (stats['latestDownload'] != null) totalDownload += stats['latestDownload'] as double;
      if (stats['hasIssues'] as bool) routersWithIssues++;

      // Interface count from latest entry
      if (entries.isNotEmpty) {
        final ifaces = entries.last.interfaces;
        totalInterfaces += ifaces.length;
        ifaces.forEach((_, ifData) {
          if (ifData is Map) {
            final rx = (ifData['byte_recv_throughput'] as num?)?.toDouble() ?? 0.0;
            final tx = (ifData['byte_sent_throughput'] as num?)?.toDouble() ?? 0.0;
            if (rx > 0 || tx > 0) activeInterfaces++;
          }
        });
      }
    });

    return {
      'totalRouters': _metrics!.data.length,
      'totalUpload': totalUpload,
      'totalDownload': totalDownload,
      'totalInterfaces': totalInterfaces,
      'activeInterfaces': activeInterfaces,
      'routersWithIssues': routersWithIssues,
    };
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Formatting helpers
  // ──────────────────────────────────────────────────────────────────────────

  String _formatSpeed(double? bps) {
    if (bps == null) return 'N/A';
    if (bps < 1024) return '${bps.toStringAsFixed(0)} B/s';
    if (bps < 1024 * 1024) return '${(bps / 1024).toStringAsFixed(1)} KB/s';
    if (bps < 1024 * 1024 * 1024) return '${(bps / (1024 * 1024)).toStringAsFixed(1)} MB/s';
    return '${(bps / (1024 * 1024 * 1024)).toStringAsFixed(2)} GB/s';
  }

  String _formatTimestamp(int timestamp) {
    final date = DateTime.fromMillisecondsSinceEpoch(timestamp);
    final now = DateTime.now();
    final diff = now.difference(date);
    if (diff.inSeconds < 60) return '${diff.inSeconds}s ago';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    return '${date.hour}:${date.minute.toString().padLeft(2, '0')}';
  }

  IconData _getRouterIcon(String routerName) {
    final n = routerName.toLowerCase();
    if (n.startsWith('p') && n.length >= 2 && int.tryParse(n[1]) != null) return Icons.hub;
    if (n.startsWith('pe')) return Icons.router;
    if (n.startsWith('ce')) return Icons.devices;
    if (n.startsWith('rr')) return Icons.settings_ethernet;
    return Icons.router_outlined;
  }

  _ActivityInfo _activityInfo(double total) {
    if (total == 0) return _ActivityInfo('Idle', Colors.grey);
    if (total < 1024 * 1024) return _ActivityInfo('Low', Colors.green);
    if (total < 100 * 1024 * 1024) return _ActivityInfo('Medium', Colors.blue);
    if (total < 1024 * 1024 * 1024) return _ActivityInfo('High', Colors.orange);
    return _ActivityInfo('Very High', Colors.red);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Build widgets
  // ──────────────────────────────────────────────────────────────────────────

  Widget _buildNetworkSummary() {
    final summary = _calculateNetworkSummary();
    final totalRouters = summary['totalRouters'] as int;
    final totalUpload = summary['totalUpload'] as double;
    final totalDownload = summary['totalDownload'] as double;
    final totalInterfaces = summary['totalInterfaces'] as int;
    final activeInterfaces = summary['activeInterfaces'] as int;
    final routersWithIssues = summary['routersWithIssues'] as int;

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 8.0),
      padding: const EdgeInsets.all(16.0),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF0D47A1), Color(0xFF1976D2)],
        ),
        borderRadius: BorderRadius.circular(12.0),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.2),
            blurRadius: 8,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.summarize, color: Colors.white, size: 20),
              const SizedBox(width: 8),
              const Text(
                'Network Summary',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              const Spacer(),
              if (routersWithIssues > 0)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.orange,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.warning_amber_rounded, color: Colors.white, size: 14),
                      const SizedBox(width: 4),
                      Text(
                        '$routersWithIssues Issue${routersWithIssues > 1 ? 's' : ''}',
                        style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
                      ),
                    ],
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(child: _buildSummaryMetric(Icons.router, 'Routers', '$totalRouters', Colors.white70)),
              Expanded(child: _buildSummaryMetric(Icons.upload, 'Total Upload', _formatSpeed(totalUpload), Colors.lightBlue[200]!)),
              Expanded(child: _buildSummaryMetric(Icons.download, 'Total Download', _formatSpeed(totalDownload), Colors.lightGreen[200]!)),
              Expanded(
                child: _buildSummaryMetric(
                  Icons.lan,
                  'Interfaces',
                  '$activeInterfaces / $totalInterfaces',
                  Colors.purple[200]!,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSummaryMetric(IconData icon, String label, String value, Color iconColor) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: iconColor, size: 20),
        const SizedBox(height: 6),
        Text(
          value,
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white),
          textAlign: TextAlign.center,
          overflow: TextOverflow.ellipsis,
        ),
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white70), textAlign: TextAlign.center),
      ],
    );
  }

  /// A compact sparkline row for a single router.
  Widget _buildRouterSparklineRow(String routerName, List<MetricEntry> entries) {
    final series = _buildTimeSeries(entries);
    final stats = _calcStats(series);

    final latestUpload = stats['latestUpload'] as double? ?? 0.0;
    final latestDownload = stats['latestDownload'] as double? ?? 0.0;
    final peakTotal = stats['peakTotal'] as double;
    final hasIssues = stats['hasIssues'] as bool;
    final hasData = stats['hasData'] as bool;
    final timestamp = stats['timestamp'] as int;

    final totalNow = latestUpload + latestDownload;
    final activity = _activityInfo(totalNow);

    final isExpanded = _expandedRouters.contains(routerName);

    return Card(
      elevation: 2,
      margin: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 4.0),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10.0),
        side: BorderSide(
          color: hasIssues ? Colors.orange : const Color(0xFF0D47A1).withOpacity(0.3),
          width: hasIssues ? 1.5 : 1,
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // ── Main row ──────────────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 8, 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                // Router icon + name
                Icon(
                  _getRouterIcon(routerName),
                  size: 20,
                  color: hasIssues ? Colors.orange : (hasData ? const Color(0xFF0D47A1) : Colors.grey),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: 70,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        routerName,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.bold,
                          color: hasIssues ? Colors.orange : const Color(0xFF0D47A1),
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (timestamp > 0)
                        Text(
                          _formatTimestamp(timestamp),
                          style: TextStyle(fontSize: 9, color: Colors.grey[500], fontStyle: FontStyle.italic),
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),

                // Activity chip
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: activity.color.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: activity.color.withOpacity(0.5)),
                  ),
                  child: Text(
                    activity.label,
                    style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: activity.color),
                  ),
                ),
                const SizedBox(width: 10),

                // Current speeds
                Expanded(
                  flex: 2,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.arrow_upward, size: 10, color: Colors.blue),
                          const SizedBox(width: 2),
                          Text(_formatSpeed(latestUpload), style: const TextStyle(fontSize: 10)),
                        ],
                      ),
                      Row(
                        children: [
                          const Icon(Icons.arrow_downward, size: 10, color: Colors.green),
                          const SizedBox(width: 2),
                          Text(_formatSpeed(latestDownload), style: const TextStyle(fontSize: 10)),
                        ],
                      ),
                      if (peakTotal > 0)
                        Row(
                          children: [
                            Icon(Icons.trending_up, size: 10, color: Colors.grey[500]),
                            const SizedBox(width: 2),
                            Text(
                              'Peak ${_formatSpeed(peakTotal)}',
                              style: TextStyle(fontSize: 9, color: Colors.grey[500]),
                            ),
                          ],
                        ),
                    ],
                  ),
                ),

                // Sparkline
                Expanded(
                  flex: 3,
                  child: SizedBox(
                    height: 56,
                    child: series.length >= 2
                        ? _buildSparkline(series, activity.color)
                        : Center(
                            child: Text(
                              series.length == 1 ? 'Collecting…' : 'No history',
                              style: TextStyle(fontSize: 9, color: Colors.grey[400], fontStyle: FontStyle.italic),
                            ),
                          ),
                  ),
                ),

                // Expand button
                IconButton(
                  icon: Icon(
                    isExpanded ? Icons.expand_less : Icons.expand_more,
                    size: 18,
                    color: Colors.grey[500],
                  ),
                  tooltip: isExpanded ? 'Hide interface detail' : 'Show interface detail',
                  onPressed: () {
                    setState(() {
                      if (isExpanded) {
                        _expandedRouters.remove(routerName);
                      } else {
                        _expandedRouters.add(routerName);
                      }
                    });
                  },
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                ),
              ],
            ),
          ),

          // ── Expanded per-interface detail ─────────────────────────────────
          if (isExpanded && entries.isNotEmpty)
            _buildInterfaceDetail(entries.last),
        ],
      ),
    );
  }

  /// Mini sparkline using fl_chart – shows upload (blue) and download (green) lines.
  Widget _buildSparkline(List<Map<String, dynamic>> series, Color accentColor) {
    // Normalise x to 0..n-1
    final uploadSpots = <FlSpot>[];
    final downloadSpots = <FlSpot>[];
    double maxY = 0;

    for (int i = 0; i < series.length; i++) {
      final up = series[i]['upload'] as double;
      final dn = series[i]['download'] as double;
      uploadSpots.add(FlSpot(i.toDouble(), up));
      downloadSpots.add(FlSpot(i.toDouble(), dn));
      if (up > maxY) maxY = up;
      if (dn > maxY) maxY = dn;
    }

    // Avoid maxY == 0 (flat line at bottom)
    if (maxY == 0) maxY = 1.0;

    final n = (series.length - 1).toDouble();

    return LineChart(
      LineChartData(
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(
          leftTitles: AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: AxisTitles(sideTitles: SideTitles(showTitles: false)),
          topTitles: AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: false),
        minX: 0,
        maxX: n,
        minY: 0,
        maxY: maxY * 1.15,
        lineBarsData: [
          // Upload line (blue, filled)
          LineChartBarData(
            spots: uploadSpots,
            isCurved: true,
            color: Colors.blue,
            barWidth: 1.5,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(show: true, color: Colors.blue.withOpacity(0.08)),
          ),
          // Download line (green, filled)
          LineChartBarData(
            spots: downloadSpots,
            isCurved: true,
            color: Colors.green,
            barWidth: 1.5,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(show: true, color: Colors.green.withOpacity(0.08)),
          ),
        ],
        lineTouchData: const LineTouchData(enabled: false),
      ),
    );
  }

  /// Per-interface detail panel shown when a router row is expanded.
  Widget _buildInterfaceDetail(MetricEntry entry) {
    final interfaces = entry.interfaces;
    if (interfaces.isEmpty) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
        child: Text(
          'No interface data',
          style: TextStyle(fontSize: 10, fontStyle: FontStyle.italic, color: Colors.grey[500]),
        ),
      );
    }

    return Container(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 10),
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.grey[50],
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.grey[200]!),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Interfaces',
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.grey[700]),
          ),
          const SizedBox(height: 4),
          ...interfaces.entries.map((e) {
            final ifName = e.key;
            final ifData = e.value;
            final tx = (ifData is Map ? (ifData['byte_sent_throughput'] as num?)?.toDouble() : null);
            final rx = (ifData is Map ? (ifData['byte_recv_throughput'] as num?)?.toDouble() : null);
            final isActive = (tx != null && tx > 0) || (rx != null && rx > 0);

            return Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                children: [
                  Container(
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      color: isActive ? Colors.green : Colors.grey[300],
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 3,
                    child: Text(ifName, style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis),
                  ),
                  Row(children: [
                    const Icon(Icons.arrow_upward, size: 9, color: Colors.blue),
                    const SizedBox(width: 2),
                    Text(_formatSpeed(tx), style: const TextStyle(fontSize: 9)),
                  ]),
                  const SizedBox(width: 10),
                  Row(children: [
                    const Icon(Icons.arrow_downward, size: 9, color: Colors.green),
                    const SizedBox(width: 2),
                    Text(_formatSpeed(rx), style: const TextStyle(fontSize: 9)),
                  ]),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Root build
  // ──────────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Header bar
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD),
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              Center(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.speed, color: Color(0xFF0D47A1), size: 18),
                    const SizedBox(width: 8),
                    Text(
                      'Router Performance History',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                            color: const Color(0xFF0D47A1),
                          ),
                    ),
                    const SizedBox(width: 8),
                    // Legend dots
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(width: 10, height: 2, color: Colors.blue),
                        const SizedBox(width: 2),
                        Text('↑', style: TextStyle(fontSize: 9, color: Colors.blue[700])),
                        const SizedBox(width: 6),
                        Container(width: 10, height: 2, color: Colors.green),
                        const SizedBox(width: 2),
                        Text('↓', style: TextStyle(fontSize: 9, color: Colors.green[700])),
                      ],
                    ),
                    const SizedBox(width: 8),
                    // Auto-refresh indicator
                    Tooltip(
                      message: 'Auto-refreshes every 20 seconds',
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.green.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: Colors.green, width: 1),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.refresh, size: 12, color: Colors.green[700]),
                            const SizedBox(width: 4),
                            Text(
                              '20s',
                              style: TextStyle(fontSize: 10, color: Colors.green[700], fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              // Expand/Collapse button
              Positioned(
                right: 0,
                child: IconButton(
                  icon: Icon(
                    widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                    color: const Color(0xFF0D47A1),
                  ),
                  tooltip: widget.isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                  onPressed: () {
                    if (widget.isFullScreen) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (context) => FullScreenPanelView(
                            panelType: PanelType.performance,
                            socket: widget.socket,
                            isLoading: widget.isLoading,
                          ),
                        ),
                      );
                    }
                  },
                ),
              ),
            ],
          ),
        ),

        // Content
        Expanded(
          child: _isLoadingMetrics && _metrics == null
              ? const Center(child: CircularProgressIndicator())
              : _errorMessage != null
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Icon(Icons.error_outline, color: Colors.red, size: 48),
                          const SizedBox(height: 16),
                          const Text(
                            'Error loading metrics',
                            style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                          ),
                          const SizedBox(height: 8),
                          Text(_errorMessage!, style: const TextStyle(color: Colors.red), textAlign: TextAlign.center),
                        ],
                      ),
                    )
                  : _metrics == null || _metrics!.data.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const Icon(Icons.info_outline, color: Colors.grey, size: 48),
                              const SizedBox(height: 16),
                              const Text(
                                'No router metrics available',
                                style: TextStyle(fontSize: 16, fontStyle: FontStyle.italic),
                              ),
                              const SizedBox(height: 8),
                              Text('Waiting for data…', style: TextStyle(fontSize: 12, color: Colors.grey[600])),
                            ],
                          ),
                        )
                      : RefreshIndicator(
                          onRefresh: _fetchMetrics,
                          child: _buildSparklineList(),
                        ),
        ),
      ],
    );
  }

  Widget _buildSparklineList() {
    // Sort routers: busiest (highest current total) first
    final keys = _metrics!.data.keys.toList();
    keys.sort((a, b) {
      final aEntries = _metrics!.data[a]!;
      final bEntries = _metrics!.data[b]!;
      final aSeries = _buildTimeSeries(aEntries);
      final bSeries = _buildTimeSeries(bEntries);
      final aTotal = aSeries.isNotEmpty ? (aSeries.last['total'] as double) : 0.0;
      final bTotal = bSeries.isNotEmpty ? (bSeries.last['total'] as double) : 0.0;
      return bTotal.compareTo(aTotal);
    });

    return CustomScrollView(
      slivers: [
        // Network Summary
        SliverToBoxAdapter(child: _buildNetworkSummary()),

        // Section label
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
            child: Row(
              children: [
                Icon(Icons.show_chart, size: 14, color: Colors.grey[600]),
                const SizedBox(width: 6),
                Text(
                  'Per-Router Throughput History  •  sorted by current activity',
                  style: TextStyle(fontSize: 11, color: Colors.grey[600], fontStyle: FontStyle.italic),
                ),
              ],
            ),
          ),
        ),

        // Sparkline rows
        SliverList(
          delegate: SliverChildBuilderDelegate(
            (context, index) {
              final routerName = keys[index];
              final entries = _metrics!.data[routerName]!;
              return _buildRouterSparklineRow(routerName, entries);
            },
            childCount: keys.length,
          ),
        ),

        // Bottom padding
        const SliverToBoxAdapter(child: SizedBox(height: 16)),
      ],
    );
  }
}

/// Simple value object for activity level + colour.
class _ActivityInfo {
  final String label;
  final Color color;
  const _ActivityInfo(this.label, this.color);
}
