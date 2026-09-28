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
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../utils/APIService.dart';
import 'multi_select_dropdown.dart';
import 'tree_select_dropdown.dart';

// ---------------------------------------------------------------------------
// Available flow metrics
// ---------------------------------------------------------------------------

enum FlowMetric {
  throughputSent,
  throughputRecv,
  latency,
  jitter,
  packetLoss,
  activeSessions,
}

extension FlowMetricExt on FlowMetric {
  String get label {
    switch (this) {
      case FlowMetric.throughputSent:
        return 'Throughput ↑ (bps)';
      case FlowMetric.throughputRecv:
        return 'Throughput ↓ (bps)';
      case FlowMetric.latency:
        return 'Latency (ms)';
      case FlowMetric.jitter:
        return 'Jitter (ms)';
      case FlowMetric.packetLoss:
        return 'Packet Loss (%)';
      case FlowMetric.activeSessions:
        return 'Active Sessions';
    }
  }

  String get shortLabel {
    switch (this) {
      case FlowMetric.throughputSent:
        return '↑ Tput';
      case FlowMetric.throughputRecv:
        return '↓ Tput';
      case FlowMetric.latency:
        return 'Latency';
      case FlowMetric.jitter:
        return 'Jitter';
      case FlowMetric.packetLoss:
        return 'Loss %';
      case FlowMetric.activeSessions:
        return 'Sessions';
    }
  }

  String get jsonKey {
    switch (this) {
      case FlowMetric.throughputSent:
        return 'throughput_sent_bps';
      case FlowMetric.throughputRecv:
        return 'throughput_recv_bps';
      case FlowMetric.latency:
        return 'latency_ms';
      case FlowMetric.jitter:
        return 'jitter_ms';
      case FlowMetric.packetLoss:
        return 'packet_loss_pct';
      case FlowMetric.activeSessions:
        return 'active_sessions';
    }
  }

  String formatValue(double v) {
    switch (this) {
      case FlowMetric.throughputSent:
      case FlowMetric.throughputRecv:
        if (v >= 1e9) return '${(v / 1e9).toStringAsFixed(1)}G';
        if (v >= 1e6) return '${(v / 1e6).toStringAsFixed(1)}M';
        if (v >= 1e3) return '${(v / 1e3).toStringAsFixed(0)}K';
        return v.toStringAsFixed(0);
      case FlowMetric.latency:
      case FlowMetric.jitter:
        return '${v.toStringAsFixed(1)}ms';
      case FlowMetric.packetLoss:
        return '${v.toStringAsFixed(1)}%';
      case FlowMetric.activeSessions:
        return v.toStringAsFixed(0);
    }
  }
}

// ---------------------------------------------------------------------------
// Time period options
// ---------------------------------------------------------------------------

enum TimePeriod { last5m, last15m, last30m, last1h, last2h, last3h }

extension TimePeriodExt on TimePeriod {
  String get label {
    switch (this) {
      case TimePeriod.last5m:
        return 'Last 5 min';
      case TimePeriod.last15m:
        return 'Last 15 min';
      case TimePeriod.last30m:
        return 'Last 30 min';
      case TimePeriod.last1h:
        return 'Last 1 hour';
      case TimePeriod.last2h:
        return 'Last 2 hours';
      case TimePeriod.last3h:
        return 'Last 3 hours';
    }
  }

  Duration get duration {
    switch (this) {
      case TimePeriod.last5m:
        return const Duration(minutes: 5);
      case TimePeriod.last15m:
        return const Duration(minutes: 15);
      case TimePeriod.last30m:
        return const Duration(minutes: 30);
      case TimePeriod.last1h:
        return const Duration(hours: 1);
      case TimePeriod.last2h:
        return const Duration(hours: 2);
      case TimePeriod.last3h:
        return const Duration(hours: 3);
    }
  }
}

// ---------------------------------------------------------------------------
// Colour palette for lines
// ---------------------------------------------------------------------------

const _kLineColors = [
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
// FlowPerformanceGraph widget
// ---------------------------------------------------------------------------

class FlowPerformanceGraph extends StatefulWidget {
  /// When provided by a parent (e.g. DualPerformanceWidget), the period is
  /// controlled externally. [onPeriodChanged] is called when the user changes
  /// the period so the parent can update its shared state.
  final TimePeriod? period;
  final ValueChanged<TimePeriod>? onPeriodChanged;

  const FlowPerformanceGraph({
    super.key,
    this.period,
    this.onPeriodChanged,
  });

  @override
  State<FlowPerformanceGraph> createState() => _FlowPerformanceGraphState();
}

class _FlowPerformanceGraphState extends State<FlowPerformanceGraph> {
  final APIService _api = APIService();
  Timer? _timer;

  // Controls
  /// null = "All Flows"
  final Set<String> _selectedFlows = {};
  bool _allFlows = true;
  TimePeriod _period = TimePeriod.last15m;
  final Set<FlowMetric> _selectedMetrics = {
    FlowMetric.throughputSent,
    FlowMetric.latency,
  };

  // Data: flow_id → list of time-series points
  Map<String, List<Map<String, dynamic>>> _data = {};
  bool _loading = false;  // true only while first fetch is in-flight with no data
  bool _fetching = false; // true during any active fetch
  String? _error;

  // Tooltip overlay state
  Offset? _tooltipOffset;
  List<({Color color, String text})> _tooltipEntries = [];
  // Debounce timer to prevent tooltip flicker
  Timer? _tooltipHideTimer;

  /// The effective period: prefer the externally-controlled value if provided.
  TimePeriod get _effectivePeriod => widget.period ?? _period;

  @override
  void initState() {
    super.initState();
    _fetchData();
    _timer = Timer.periodic(const Duration(seconds: 20), (_) => _fetchData());
  }

  @override
  void didUpdateWidget(FlowPerformanceGraph oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Re-fetch when the parent changes the period.
    if (widget.period != null && widget.period != oldWidget.period) {
      _fetchData();
    }
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
    setState(() { _fetching = true; });

    final appState = Provider.of<Appstate>(context, listen: false);
    final tests = appState.trafficTests;
    if (tests.isEmpty) {
      if (mounted) setState(() { _fetching = false; _loading = false; _data = {}; });
      return;
    }

    final now = DateTime.now().toUtc();
    final start = now.subtract(_effectivePeriod.duration);
    final testNames = tests.map((t) => t.name).toList();

    try {
      final raw = await _api.fetchTrafficMetricsRange(testNames, start, now);
      if (mounted) {
        setState(() {
          _data = raw.map((k, v) {
            final points = (v as List).cast<Map<String, dynamic>>();
            return MapEntry(k, points);
          });
          _fetching = false;
          _loading = false;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() { _fetching = false; _loading = false; _error = e.toString(); });
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  List<String> get _allFlowIds => _data.keys.toList()..sort();

  List<String> get _visibleFlowIds {
    if (_allFlows || _selectedFlows.isEmpty) return _allFlowIds;
    return _allFlowIds.where((id) => _selectedFlows.contains(id)).toList();
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
    return Column(
      children: [
        _buildHeader(),
        Expanded(child: _buildBody()),
      ],
    );
  }

  Widget _buildHeader() {
    // When period is controlled externally (tabbed mode), hide the period
    // dropdown from this header — it lives in the parent tab bar instead.
    final bool showPeriod = widget.period == null;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: const BoxDecoration(
        color: Color(0xFFE3F2FD),
        borderRadius: BorderRadius.all(Radius.circular(8)),
      ),
      margin: const EdgeInsets.all(8),
      child: Row(
        mainAxisSize: MainAxisSize.max,
        children: [
          const Icon(Icons.show_chart, color: Color(0xFF0D47A1), size: 16),
          const SizedBox(width: 4),
          // Flow selector
          _buildFlowDropdown(),
          const SizedBox(width: 4),
          // Time period selector — only shown in standalone mode
          if (showPeriod) ...[
            _buildPeriodDropdown(),
            const SizedBox(width: 4),
          ],
          // Metric selector
          _buildMetricDropdown(),
          const SizedBox(width: 4),
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
    );
  }

  /// Build a VPN-grouped tree of flows using appState.trafficTests for grouping.
  ///
  /// Flow IDs in the metrics data follow the pattern "{testName}_{deviceName}"
  /// or "{testName}_{deviceName}_rev", so we match by prefix rather than exact
  /// equality against the TrafficTest name.
  List<TreeGroup<String>> _buildFlowGroups() {
    final appState = Provider.of<Appstate>(context, listen: false);
    final tests = appState.trafficTests;
    final allIds = _allFlowIds.toSet();

    // Map vpnRef → [flowId, …], matching flow IDs that start with the test name.
    final Map<String, List<String>> byVpn = {};
    final Set<String> covered = {};

    for (final t in tests) {
      final vpn = (t.vpnRef?.isNotEmpty == true) ? t.vpnRef! : 'Ungrouped';
      // A flow ID belongs to this test if it equals the test name or starts
      // with "{testName}_" (e.g. "d1-hub-bidir-tcp_d1-blue").
      final matchingFlows = allIds
          .where((id) => id == t.name || id.startsWith('${t.name}_'))
          .toList()
        ..sort();
      for (final flowId in matchingFlows) {
        byVpn.putIfAbsent(vpn, () => []).add(flowId);
        covered.add(flowId);
      }
    }

    final groups = <TreeGroup<String>>[];
    for (final entry in byVpn.entries) {
      if (entry.value.isNotEmpty) {
        groups.add(TreeGroup(
          label: entry.key,
          children: entry.value.map((n) => (n, n)).toList(),
        ));
      }
    }

    // Any flows not matched by any traffic test go into Ungrouped.
    final uncovered = allIds.difference(covered).toList()..sort();
    if (uncovered.isNotEmpty) {
      groups.add(TreeGroup(
        label: 'Ungrouped',
        children: uncovered.map((n) => (n, n)).toList(),
      ));
    }

    return groups;
  }

  Widget _buildFlowDropdown() {
    final label = _allFlows
        ? 'All Flows'
        : _selectedFlows.isEmpty
            ? 'No Flows'
            : _selectedFlows.length == 1
                ? _selectedFlows.first
                : '${_selectedFlows.length} Flows';

    final groups = _buildFlowGroups();

    return TreeSelectDropdown<String>(
      label: label,
      icon: Icons.swap_horiz,
      groups: groups,
      selected: _allFlows ? null : _selectedFlows,
      onAllToggled: () {
        setState(() {
          _allFlows = true;
          _selectedFlows.clear();
        });
      },
      onItemToggled: (id) {
        setState(() {
          // When coming from "all selected" mode, seed _selectedFlows with
          // ALL flows first so toggling one item immediately reflects the
          // correct state (all others remain selected, clicked one deselects).
          if (_allFlows) {
            _selectedFlows.addAll(_allFlowIds);
          }
          _allFlows = false;
          if (_selectedFlows.contains(id)) {
            _selectedFlows.remove(id);
          } else {
            _selectedFlows.add(id);
          }
          if (_selectedFlows.isEmpty) _allFlows = true;
        });
      },
      onGroupToggled: (group, select) {
        setState(() {
          // When coming from "all selected" mode, seed _selectedFlows with
          // ALL flows first, then add/remove the group's children so that
          // clicking a group checkbox selects/deselects only that group.
          if (_allFlows) {
            _selectedFlows.addAll(_allFlowIds);
          }
          _allFlows = false;
          for (final (item, _) in group.children) {
            if (select) {
              _selectedFlows.add(item);
            } else {
              _selectedFlows.remove(item);
            }
          }
          if (_selectedFlows.isEmpty) _allFlows = true;
        });
      },
    );
  }

  Widget _buildPeriodDropdown() {
    return DropdownButton<TimePeriod>(
      value: _period,
      isDense: true,
      style: const TextStyle(fontSize: 12, color: Color(0xFF0D47A1)),
      underline: const SizedBox.shrink(),
      items: TimePeriod.values
          .map((TimePeriod p) =>
              DropdownMenuItem<TimePeriod>(value: p, child: Text(p.label)))
          .toList(),
      onChanged: (TimePeriod? p) {
        if (p != null) {
          setState(() => _period = p);
          _fetchData();
        }
      },
    );
  }

  Widget _buildMetricDropdown() {
    final label = _selectedMetrics.isEmpty
        ? 'No Metrics'
        : _selectedMetrics.length == 1
            ? _selectedMetrics.first.shortLabel
            : '${_selectedMetrics.length} Metrics';

    return MultiSelectDropdown<FlowMetric>(
      label: label,
      icon: Icons.bar_chart,
      items: FlowMetric.values,
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
    // Show spinner only on first load when there's no data yet.
    if (_fetching && _data.isEmpty && _error == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, color: Colors.red, size: 40),
            const SizedBox(height: 8),
            Text(_error!, style: const TextStyle(color: Colors.red)),
          ],
        ),
      );
    }

    final visibleIds = _visibleFlowIds;
    if (visibleIds.isEmpty || _data.isEmpty) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.info_outline, color: Colors.grey, size: 40),
            SizedBox(height: 8),
            Text('No flow data available',
                style: TextStyle(color: Colors.grey, fontStyle: FontStyle.italic)),
          ],
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 0, 16, 8),
      child: _buildChartWithTooltip(visibleIds),
    );
  }

  Widget _buildChartWithTooltip(List<String> flowIds) {
    return MouseRegion(
      onHover: (event) {
        // Keep the hide timer cancelled while the cursor is inside the chart.
        _tooltipHideTimer?.cancel();
      },
      onExit: (_) {
        _tooltipHideTimer?.cancel();
        _tooltipHideTimer = Timer(const Duration(milliseconds: 120), () {
          if (mounted) setState(() { _tooltipOffset = null; _tooltipEntries = []; });
        });
      },
      child: LayoutBuilder(
        builder: (context, constraints) {
          return Stack(
            clipBehavior: Clip.none,
            children: [
              _buildChart(flowIds),
              if (_tooltipOffset != null && _tooltipEntries.isNotEmpty)
                _buildTooltipOverlay(constraints),
            ],
          );
        },
      ),
    );
  }

  Widget _buildTooltipOverlay(BoxConstraints constraints) {
    const double maxHeight = 200;
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
            padding: const EdgeInsets.symmetric(horizontal: hPad, vertical: vPad),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: _tooltipEntries.map((e) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 1.5),
                child: Text(
                  e.text,
                  style: TextStyle(
                    color: e.color,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                  softWrap: false,
                ),
              )).toList(),
            ),
          ),
        ),
      ),
    );

    // Position the tooltip so it stays inside the chart area.
    const double tooltipW = 220; // estimated; IntrinsicWidth may be wider
    const double tooltipH = maxHeight;
    const double offsetX = 12;
    const double offsetY = -8;

    double left = (_tooltipOffset?.dx ?? 0) + offsetX;
    double top = (_tooltipOffset?.dy ?? 0) + offsetY;

    // Clamp horizontally
    if (left + tooltipW > constraints.maxWidth) {
      left = (_tooltipOffset?.dx ?? 0) - tooltipW - offsetX;
    }
    if (left < 0) left = 0;

    // Clamp vertically
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

  Widget _buildChart(List<String> flowIds) {
    if (_selectedMetrics.isEmpty) {
      return const Center(child: Text('Select at least one metric'));
    }

    // Compute the window boundaries up front so we can clip data points to
    // the selected period before building bars. This prevents stale points
    // from a previously wider period from overflowing the left edge of the
    // chart when the user switches to a shorter period.
    final nowMs = DateTime.now().toUtc().millisecondsSinceEpoch.toDouble();
    final periodMs = _effectivePeriod.duration.inMilliseconds.toDouble();
    final windowStart = nowMs - periodMs;

    // Build line series: one per (flow, metric) combination.
    // Also keep a parallel label list so the tooltip can show "flow • metric".
    final bars = <LineChartBarData>[];
    // Each entry maps barIndex → (flowId, metric)
    final barLabels = <(String, FlowMetric)>[];
    int colorIdx = 0;

    double maxY = 0;

    for (final flowId in flowIds) {
      final points = _data[flowId] ?? [];
      if (points.isEmpty) continue;

      for (final metric in _selectedMetrics) {
        final spots = <FlSpot>[];
        for (final pt in points) {
          final ts = (pt['timestamp'] as num?)?.toDouble();
          final val = (pt[metric.jsonKey] as num?)?.toDouble();
          if (ts != null && val != null && ts >= windowStart) {
            spots.add(FlSpot(ts, val));
            if (val > maxY) maxY = val;
          }
        }
        if (spots.isEmpty) continue;

        final color = _kLineColors[colorIdx % _kLineColors.length];
        colorIdx++;

        bars.add(LineChartBarData(
          spots: spots,
          isCurved: false,
          color: color,
          barWidth: 1.8,
          dotData: const FlDotData(show: false),
          belowBarData: BarAreaData(show: true, color: color.withOpacity(0.06)),
        ));

        barLabels.add((flowId, metric));
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

    // Left edge: earliest data point (no blank space on the left).
    final double minX = dataMinX.isFinite ? dataMinX : windowStart;

    // Right edge: latest data point (no blank space on the right).
    final double maxX = dataMaxX.isFinite ? dataMaxX : nowMs;
    if (maxY == 0) maxY = 1;

    // Use the first selected metric for Y-axis formatting
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
              reservedSize: 52,
              getTitlesWidget: (v, _) => Text(
                primaryMetric.formatValue(v),
                style: const TextStyle(fontSize: 9),
              ),
            ),
          ),
          rightTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false)),
          topTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 28,
              interval: (maxX - minX) == 0 ? 1 : (maxX - minX) / 5,
              getTitlesWidget: (v, _) => Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  _formatAxisTime(v),
                  style: const TextStyle(fontSize: 8),
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
            // Ignore exit events here — MouseRegion.onExit handles hiding.
            if (event is FlPointerExitEvent) return;

            // Only process events that carry a position.
            final localPos = event.localPosition;
            if (localPos == null) return;

            // Cancel any pending hide.
            _tooltipHideTimer?.cancel();

            if (response == null || response.lineBarSpots == null || response.lineBarSpots!.isEmpty) {
              // No spots near cursor — keep existing tooltip, don't update.
              return;
            }
            final spots = response.lineBarSpots!;

            // Group metrics by flow so each flow gets one tooltip line.
            final Map<String, List<(LineBarSpot, FlowMetric)>> grouped = {};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx < barLabels.length) {
                final (flowId, metric) = barLabels[idx];
                grouped.putIfAbsent(flowId, () => []).add((s, metric));
              }
            }

            final entries = <({Color color, String text})>[];
            final seen = <String>{};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx >= barLabels.length) continue;
              final (flowId, metric) = barLabels[idx];
              if (seen.contains(flowId)) continue;
              seen.add(flowId);
              final flowEntries = grouped[flowId]!;
              final metricParts = flowEntries
                  .map((e) => '${e.$2.shortLabel}: ${e.$2.formatValue(e.$1.y)}')
                  .join('  ');
              entries.add((color: s.bar.color ?? Colors.blue, text: '$flowId  $metricParts'));
            }

            if (mounted) setState(() {
              _tooltipOffset = localPos;
              _tooltipEntries = entries;
            });
          },
        ),
      ),
    );
  }
}
