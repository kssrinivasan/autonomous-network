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
import '../../utils/APIService.dart';
import 'flow_performance_graph.dart' show TimePeriod, TimePeriodExt;
import 'multi_select_dropdown.dart';

// ---------------------------------------------------------------------------
// Available interface metrics
// ---------------------------------------------------------------------------

enum InterfaceMetric {
  txBytes,
  rxBytes,
}

extension InterfaceMetricExt on InterfaceMetric {
  String get label {
    switch (this) {
      case InterfaceMetric.txBytes:
        return 'TX Throughput (B/s)';
      case InterfaceMetric.rxBytes:
        return 'RX Throughput (B/s)';
    }
  }

  String get shortLabel {
    switch (this) {
      case InterfaceMetric.txBytes:
        return '↑ TX';
      case InterfaceMetric.rxBytes:
        return '↓ RX';
    }
  }

  String formatValue(double v) {
    if (v >= 1e9) return '${(v / 1e9).toStringAsFixed(1)}G';
    if (v >= 1e6) return '${(v / 1e6).toStringAsFixed(1)}M';
    if (v >= 1e3) return '${(v / 1e3).toStringAsFixed(0)}K';
    return v.toStringAsFixed(0);
  }
}

// ---------------------------------------------------------------------------
// Colour palette
// ---------------------------------------------------------------------------

const _kInterfaceColors = [
  Color(0xFF1565C0),
  Color(0xFF2E7D32),
  Color(0xFFC62828),
  Color(0xFF6A1B9A),
  Color(0xFFE65100),
  Color(0xFF00838F),
  Color(0xFF558B2F),
  Color(0xFF4527A0),
];

// ---------------------------------------------------------------------------
// RouterInterfacePerformanceCard widget
// ---------------------------------------------------------------------------

class RouterInterfacePerformanceCard extends StatefulWidget {
  final String nodeId;
  final String nodeName;

  const RouterInterfacePerformanceCard({
    super.key,
    required this.nodeId,
    required this.nodeName,
  });

  @override
  State<RouterInterfacePerformanceCard> createState() =>
      _RouterInterfacePerformanceCardState();
}

class _RouterInterfacePerformanceCardState
    extends State<RouterInterfacePerformanceCard> {
  final APIService _api = APIService();
  Timer? _timer;

  // Controls
  final Set<String> _selectedInterfaces = {};
  bool _allInterfaces = true;
  TimePeriod _period = TimePeriod.last15m;
  final Set<InterfaceMetric> _selectedMetrics = {
    InterfaceMetric.txBytes,
    InterfaceMetric.rxBytes,
  };

  // Data: list of { timestamp, metrics: { interfaces, cpu } }
  List<Map<String, dynamic>> _data = [];
  bool _fetching = false;
  String? _error;

  // Tooltip overlay state
  Offset? _tooltipOffset;
  List<({Color color, String text})> _tooltipEntries = [];
  Timer? _tooltipHideTimer;

  @override
  void initState() {
    super.initState();
    _fetchData();
    _timer = Timer.periodic(const Duration(seconds: 20), (_) => _fetchData());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _tooltipHideTimer?.cancel();
    super.dispose();
  }

  // ── Data fetching ──────────────────────────────────────────────────────────

  Future<void> _fetchData() async {
    if (!mounted) return;
    setState(() {
      _fetching = true;
    });

    final now = DateTime.now().toUtc();
    final start = now.subtract(_period.duration);

    try {
      final raw = await _api.fetchRouterMetricsRange(start, now,
          nodeNames: [widget.nodeName]);
      if (mounted) {
        // Extract data for this specific router
        final routerData = raw[widget.nodeName];
        if (routerData != null && routerData is List) {
          final points = routerData.cast<Map<String, dynamic>>();
          setState(() {
            _data = points;
            _fetching = false;
            _error = null;
          });
        } else {
          setState(() {
            _data = [];
            _fetching = false;
            _error = null;
          });
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _fetching = false;
          _error = e.toString();
        });
      }
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  /// Get all interface names from the data
  List<String> get _allInterfaceNames {
    final Set<String> interfaces = {};
    for (final point in _data) {
      final metrics = point['metrics'] as Map<String, dynamic>?;
      if (metrics != null) {
        final ifaceMap = metrics['interfaces'] as Map<String, dynamic>?;
        if (ifaceMap != null) {
          interfaces.addAll(ifaceMap.keys);
        }
      }
    }
    final list = interfaces.toList()..sort();
    return list;
  }

  List<String> get _visibleInterfaces {
    if (_allInterfaces || _selectedInterfaces.isEmpty) {
      return _allInterfaceNames;
    }
    return _allInterfaceNames
        .where((iface) => _selectedInterfaces.contains(iface))
        .toList();
  }

  /// Extract TX bytes for a specific interface from a data point
  double _extractTx(Map<String, dynamic> point, String interfaceName) {
    final metrics = point['metrics'] as Map<String, dynamic>?;
    if (metrics == null) return 0;
    final interfaces = metrics['interfaces'] as Map<String, dynamic>?;
    if (interfaces == null) return 0;
    final iface = interfaces[interfaceName] as Map<String, dynamic>?;
    if (iface == null) return 0;
    return (iface['transmit_bytes_total'] as num?)?.toDouble() ??
        (iface['byte_sent_throughput'] as num?)?.toDouble() ??
        0.0;
  }

  /// Extract RX bytes for a specific interface from a data point
  double _extractRx(Map<String, dynamic> point, String interfaceName) {
    final metrics = point['metrics'] as Map<String, dynamic>?;
    if (metrics == null) return 0;
    final interfaces = metrics['interfaces'] as Map<String, dynamic>?;
    if (interfaces == null) return 0;
    final iface = interfaces[interfaceName] as Map<String, dynamic>?;
    if (iface == null) return 0;
    return (iface['receive_bytes_total'] as num?)?.toDouble() ??
        (iface['byte_recv_throughput'] as num?)?.toDouble() ??
        0.0;
  }

  double _extractMetric(
      Map<String, dynamic> point, String interfaceName, InterfaceMetric metric) {
    switch (metric) {
      case InterfaceMetric.txBytes:
        return _extractTx(point, interfaceName);
      case InterfaceMetric.rxBytes:
        return _extractRx(point, interfaceName);
    }
  }

  String _formatAxisTime(double tsMs) {
    final dt = DateTime.fromMillisecondsSinceEpoch(tsMs.toInt());
    final h = dt.hour.toString().padLeft(2, '0');
    final m = dt.minute.toString().padLeft(2, '0');
    final s = dt.second.toString().padLeft(2, '0');
    return '$h:$m:$s';
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 2,
      margin: const EdgeInsets.all(4),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: const BorderSide(color: Color(0xFF1565C0), width: 1),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            _buildHeader(),
            const Divider(height: 12),
            SizedBox(
              height: 250, // Fixed height for the chart area
              child: _buildBody(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Title row
        Row(
          children: [
            const Icon(Icons.show_chart,
                color: Color(0xFF1565C0), size: 16),
            const SizedBox(width: 6),
            const Text(
              'Interface Performance',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: Color(0xFF1565C0),
              ),
            ),
            const Spacer(),
            // Refresh indicator
            Tooltip(
              message: 'Auto-refreshes every 20 seconds',
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                decoration: BoxDecoration(
                  color: Colors.green.withOpacity(0.2),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.green),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.refresh, size: 11, color: Colors.green[700]),
                    const SizedBox(width: 2),
                    Text('20s',
                        style: TextStyle(
                            fontSize: 9,
                            color: Colors.green[700],
                            fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        const Text(
          'Historical RX/TX throughput per interface over the selected time period.',
          style: TextStyle(
            fontSize: 10,
            color: Colors.black54,
            fontStyle: FontStyle.italic,
          ),
        ),
        const SizedBox(height: 8),
        // Controls row
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            _buildInterfaceDropdown(),
            _buildPeriodDropdown(),
            _buildMetricDropdown(),
          ],
        ),
      ],
    );
  }

  Widget _buildInterfaceDropdown() {
    final allIfaces = _allInterfaceNames;
    if (allIfaces.isEmpty) {
      return const SizedBox.shrink();
    }

    final label = _allInterfaces
        ? 'All Interfaces'
        : _selectedInterfaces.isEmpty
            ? 'No Interfaces'
            : _selectedInterfaces.length == 1
                ? _selectedInterfaces.first
                : '${_selectedInterfaces.length} Interfaces';

    return MultiSelectDropdown<String>(
      label: label,
      icon: Icons.settings_ethernet,
      items: allIfaces,
      selected: _allInterfaces ? allIfaces.toSet() : _selectedInterfaces,
      itemLabel: (iface) => iface,
      onItemToggled: (iface) {
        setState(() {
          if (_allInterfaces) {
            _selectedInterfaces.addAll(allIfaces);
          }
          _allInterfaces = false;
          if (_selectedInterfaces.contains(iface)) {
            if (_selectedInterfaces.length > 1) {
              _selectedInterfaces.remove(iface);
            }
          } else {
            _selectedInterfaces.add(iface);
          }
          if (_selectedInterfaces.isEmpty) _allInterfaces = true;
        });
      },
    );
  }

  Widget _buildPeriodDropdown() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: const Color(0xFFE3F2FD),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: const Color(0xFF1565C0).withOpacity(0.3)),
      ),
      child: DropdownButton<TimePeriod>(
        value: _period,
        isDense: true,
        style: const TextStyle(fontSize: 11, color: Color(0xFF1565C0)),
        underline: const SizedBox.shrink(),
        items: [
          TimePeriod.last5m,
          TimePeriod.last15m,
          TimePeriod.last30m,
          TimePeriod.last1h,
          TimePeriod.last2h,
          TimePeriod.last3h,
        ]
            .map((TimePeriod p) =>
                DropdownMenuItem<TimePeriod>(value: p, child: Text(p.label)))
            .toList(),
        onChanged: (TimePeriod? p) {
          if (p != null) {
            setState(() => _period = p);
            _fetchData();
          }
        },
      ),
    );
  }

  Widget _buildMetricDropdown() {
    final label = _selectedMetrics.isEmpty
        ? 'No Metrics'
        : _selectedMetrics.length == 1
            ? _selectedMetrics.first.shortLabel
            : '${_selectedMetrics.length} Metrics';

    return MultiSelectDropdown<InterfaceMetric>(
      label: label,
      icon: Icons.bar_chart,
      items: InterfaceMetric.values,
      selected: _selectedMetrics,
      itemLabel: (m) => m.label,
      onItemToggled: (m) {
        setState(() {
          if (_selectedMetrics.contains(m)) {
            if (_selectedMetrics.length > 1) _selectedMetrics.remove(m);
          } else {
            _selectedMetrics.add(m);
          }
        });
      },
    );
  }

  Widget _buildBody() {
    if (_fetching && _data.isEmpty && _error == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, color: Colors.red, size: 32),
            const SizedBox(height: 8),
            Text(_error!,
                style: const TextStyle(color: Colors.red, fontSize: 11)),
          ],
        ),
      );
    }

    final visibleIfaces = _visibleInterfaces;
    if (visibleIfaces.isEmpty || _data.isEmpty) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.info_outline, color: Colors.grey, size: 32),
            SizedBox(height: 8),
            Text('No interface data available',
                style:
                    TextStyle(color: Colors.grey, fontStyle: FontStyle.italic)),
          ],
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 0, 16, 8),
      child: _buildChartWithTooltip(visibleIfaces),
    );
  }

  Widget _buildChartWithTooltip(List<String> interfaceNames) {
    return MouseRegion(
      onHover: (event) {
        _tooltipHideTimer?.cancel();
      },
      onExit: (_) {
        _tooltipHideTimer?.cancel();
        _tooltipHideTimer = Timer(const Duration(milliseconds: 120), () {
          if (mounted) {
            setState(() {
              _tooltipOffset = null;
              _tooltipEntries = [];
            });
          }
        });
      },
      child: LayoutBuilder(
        builder: (context, constraints) {
          return Stack(
            clipBehavior: Clip.none,
            children: [
              _buildChart(interfaceNames),
              if (_tooltipOffset != null && _tooltipEntries.isNotEmpty)
                _buildTooltipOverlay(constraints),
            ],
          );
        },
      ),
    );
  }

  Widget _buildTooltipOverlay(BoxConstraints constraints) {
    const double maxHeight = 150;
    const double hPad = 8;
    const double vPad = 6;

    final overlay = Container(
      constraints: const BoxConstraints(maxHeight: maxHeight),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.grey.shade300),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.12),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: IntrinsicWidth(
        child: Scrollbar(
          thumbVisibility: true,
          child: SingleChildScrollView(
            padding:
                const EdgeInsets.symmetric(horizontal: hPad, vertical: vPad),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: _tooltipEntries
                  .map((e) => Padding(
                        padding: const EdgeInsets.symmetric(vertical: 1.5),
                        child: Text(
                          e.text,
                          style: TextStyle(
                            color: e.color,
                            fontSize: 9,
                            fontWeight: FontWeight.bold,
                          ),
                          softWrap: false,
                        ),
                      ))
                  .toList(),
            ),
          ),
        ),
      ),
    );

    const double tooltipW = 220;
    const double tooltipH = maxHeight;
    const double offsetX = 12;
    const double offsetY = -8;

    double left = (_tooltipOffset?.dx ?? 0) + offsetX;
    double top = (_tooltipOffset?.dy ?? 0) + offsetY;

    if (left + tooltipW > constraints.maxWidth) {
      left = (_tooltipOffset?.dx ?? 0) - tooltipW - offsetX;
    }
    if (left < 0) left = 0;

    if (top + tooltipH > constraints.maxHeight) {
      top = constraints.maxHeight - tooltipH;
    }
    if (top < 0) top = 0;

    return Positioned(
      left: left,
      top: top,
      child: overlay,
    );
  }

  Widget _buildChart(List<String> interfaceNames) {
    if (_selectedMetrics.isEmpty) {
      return const Center(child: Text('Select at least one metric'));
    }

    final nowMs = DateTime.now().toUtc().millisecondsSinceEpoch.toDouble();
    final periodMs = _period.duration.inMilliseconds.toDouble();
    final windowStart = nowMs - periodMs;

    final bars = <LineChartBarData>[];
    final barLabels = <(String, InterfaceMetric)>[];
    int colorIdx = 0;
    double maxY = 0;

    for (final ifaceName in interfaceNames) {
      for (final metric in _selectedMetrics) {
        final spots = <FlSpot>[];
        for (final pt in _data) {
          final ts = (pt['timestamp'] as num?)?.toDouble();
          if (ts == null || ts < windowStart) continue;
          final val = _extractMetric(pt, ifaceName, metric);
          spots.add(FlSpot(ts, val));
          if (val > maxY) maxY = val;
        }
        if (spots.isEmpty) continue;

        final color = _kInterfaceColors[colorIdx % _kInterfaceColors.length];
        colorIdx++;

        bars.add(LineChartBarData(
          spots: spots,
          isCurved: false,
          color: color,
          barWidth: 1.5,
          dotData: const FlDotData(show: false),
          belowBarData: BarAreaData(show: true, color: color.withOpacity(0.06)),
        ));

        barLabels.add((ifaceName, metric));
      }
    }

    if (bars.isEmpty) {
      return const Center(
        child: Text('No data points in selected time range',
            style: TextStyle(color: Colors.grey, fontStyle: FontStyle.italic)),
      );
    }

    double dataMinX = double.infinity;
    double dataMaxX = double.negativeInfinity;
    for (final bar in bars) {
      for (final spot in bar.spots) {
        if (spot.x < dataMinX) dataMinX = spot.x;
        if (spot.x > dataMaxX) dataMaxX = spot.x;
      }
    }

    final double minX = dataMinX.isFinite ? dataMinX : windowStart;
    final double maxX = dataMaxX.isFinite ? dataMaxX : nowMs;
    if (maxY == 0) maxY = 1;

    final primaryMetric = _selectedMetrics.first;

    return LineChart(
      duration: Duration.zero,
      LineChartData(
        gridData: FlGridData(
          show: true,
          drawVerticalLine: true,
          getDrawingHorizontalLine: (_) =>
              FlLine(color: Colors.grey.withOpacity(0.15), strokeWidth: 1),
          getDrawingVerticalLine: (_) =>
              FlLine(color: Colors.grey.withOpacity(0.1), strokeWidth: 1),
        ),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 48,
              getTitlesWidget: (v, _) => Text(
                primaryMetric.formatValue(v),
                style: const TextStyle(fontSize: 8),
              ),
            ),
          ),
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 24,
              interval: (maxX - minX) == 0 ? 1 : (maxX - minX) / 4,
              getTitlesWidget: (v, _) => Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  _formatAxisTime(v),
                  style: const TextStyle(fontSize: 7),
                ),
              ),
            ),
          ),
        ),
        borderData: FlBorderData(
          show: true,
          border: Border.all(color: Colors.grey.withOpacity(0.3)),
        ),
        minX: minX,
        maxX: maxX,
        minY: 0,
        maxY: maxY * 1.15,
        lineBarsData: bars,
        lineTouchData: LineTouchData(
          handleBuiltInTouches: true,
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (_) => Colors.transparent,
            tooltipBorder: BorderSide.none,
            getTooltipItems: (spots) => spots.map((_) => null).toList(),
          ),
          touchCallback: (event, response) {
            if (event is FlPointerExitEvent) return;

            final localPos = event.localPosition;
            if (localPos == null) return;

            _tooltipHideTimer?.cancel();

            if (response == null ||
                response.lineBarSpots == null ||
                response.lineBarSpots!.isEmpty) {
              return;
            }
            final spots = response.lineBarSpots!;

            final Map<String, List<(LineBarSpot, InterfaceMetric)>> grouped =
                {};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx < barLabels.length) {
                final (ifaceName, metric) = barLabels[idx];
                grouped.putIfAbsent(ifaceName, () => []).add((s, metric));
              }
            }

            final entries = <({Color color, String text})>[];
            final seen = <String>{};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx >= barLabels.length) continue;
              final (ifaceName, metric) = barLabels[idx];
              if (seen.contains(ifaceName)) continue;
              seen.add(ifaceName);
              final ifaceEntries = grouped[ifaceName]!;
              final metricParts = ifaceEntries
                  .map((e) => '${e.$2.shortLabel}: ${e.$2.formatValue(e.$1.y)}')
                  .join('  ');
              entries.add((
                color: s.bar.color ?? Colors.blue,
                text: '$ifaceName  $metricParts'
              ));
            }

            if (mounted) {
              setState(() {
                _tooltipOffset = localPos;
                _tooltipEntries = entries;
              });
            }
          },
        ),
      ),
    );
  }
}
