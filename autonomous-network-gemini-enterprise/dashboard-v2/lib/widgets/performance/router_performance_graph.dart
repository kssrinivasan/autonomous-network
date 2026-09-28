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
import '../../models/network_node.dart';
import '../../utils/APIService.dart';
import 'flow_performance_graph.dart' show TimePeriod, TimePeriodExt;
import 'multi_select_dropdown.dart';
import 'tree_select_dropdown.dart';

// ---------------------------------------------------------------------------
// Available router metrics
// ---------------------------------------------------------------------------

enum RouterMetric {
  txBytes,
  rxBytes,
  totalThroughput,
}

extension RouterMetricExt on RouterMetric {
  String get label {
    switch (this) {
      case RouterMetric.txBytes:
        return 'TX Throughput (B/s)';
      case RouterMetric.rxBytes:
        return 'RX Throughput (B/s)';
      case RouterMetric.totalThroughput:
        return 'Total Throughput (B/s)';
    }
  }

  String get shortLabel {
    switch (this) {
      case RouterMetric.txBytes:
        return '↑ TX';
      case RouterMetric.rxBytes:
        return '↓ RX';
      case RouterMetric.totalThroughput:
        return 'Total';
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

const _kRouterColors = [
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
// RouterPerformanceGraph widget
// ---------------------------------------------------------------------------

class RouterPerformanceGraph extends StatefulWidget {
  /// When provided by a parent (e.g. DualPerformanceWidget), the period is
  /// controlled externally. [onPeriodChanged] is called when the user changes
  /// the period so the parent can update its shared state.
  final TimePeriod? period;
  final ValueChanged<TimePeriod>? onPeriodChanged;

  const RouterPerformanceGraph({
    super.key,
    this.period,
    this.onPeriodChanged,
  });

  @override
  State<RouterPerformanceGraph> createState() => _RouterPerformanceGraphState();
}

class _RouterPerformanceGraphState extends State<RouterPerformanceGraph> {
  final APIService _api = APIService();
  Timer? _timer;

  // Controls
  final Set<String> _selectedRouters = {};
  bool _allRouters = true;
  TimePeriod _period = TimePeriod.last15m;
  final Set<RouterMetric> _selectedMetrics = {
    RouterMetric.txBytes,
    RouterMetric.rxBytes,
  };

  // Data: router_name → list of { timestamp, metrics: { interfaces, cpu } }
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
  void didUpdateWidget(RouterPerformanceGraph oldWidget) {
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

    final now = DateTime.now().toUtc();
    final start = now.subtract(_effectivePeriod.duration);

    // Always fetch ALL routers so the dropdown list stays complete.
    // Filtering to selected routers is done client-side in _visibleRouterIds.
    try {
      final raw = await _api.fetchRouterMetricsRange(start, now, nodeNames: null);
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

  List<String> get _allRouterIds => _data.keys.toList()..sort();

  List<String> get _visibleRouterIds {
    if (_allRouters || _selectedRouters.isEmpty) return _allRouterIds;
    return _allRouterIds.where((id) => _selectedRouters.contains(id)).toList();
  }

  /// Aggregate all interface TX bytes for a snapshot point.
  // The metrics collector stores interface counters as ALIGN_RATE (bytes/s).
  // The raw metric names after stripping "node_network_" prefix are:
  //   transmit_bytes_total → stored as "transmit_bytes_total" (rate = bytes/s)
  //   receive_bytes_total  → stored as "receive_bytes_total"  (rate = bytes/s)
  double _extractTx(Map<String, dynamic> point) {
    final metrics = point['metrics'] as Map<String, dynamic>?;
    if (metrics == null) return 0;
    final interfaces = metrics['interfaces'] as Map<String, dynamic>?;
    if (interfaces == null) return 0;
    double total = 0;
    for (final iface in interfaces.values) {
      if (iface is Map) {
        // Try both possible key names
        final v = (iface['transmit_bytes_total'] as num?)?.toDouble()
            ?? (iface['byte_sent_throughput'] as num?)?.toDouble()
            ?? 0.0;
        total += v;
      }
    }
    return total;
  }

  double _extractRx(Map<String, dynamic> point) {
    final metrics = point['metrics'] as Map<String, dynamic>?;
    if (metrics == null) return 0;
    final interfaces = metrics['interfaces'] as Map<String, dynamic>?;
    if (interfaces == null) return 0;
    double total = 0;
    for (final iface in interfaces.values) {
      if (iface is Map) {
        final v = (iface['receive_bytes_total'] as num?)?.toDouble()
            ?? (iface['byte_recv_throughput'] as num?)?.toDouble()
            ?? 0.0;
        total += v;
      }
    }
    return total;
  }

  double _extractMetric(Map<String, dynamic> point, RouterMetric metric) {
    switch (metric) {
      case RouterMetric.txBytes:
        return _extractTx(point);
      case RouterMetric.rxBytes:
        return _extractRx(point);
      case RouterMetric.totalThroughput:
        return _extractTx(point) + _extractRx(point);
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
        color: Color(0xFFE8F5E9),
        borderRadius: BorderRadius.all(Radius.circular(8)),
      ),
      margin: const EdgeInsets.all(8),
      child: Row(
        mainAxisSize: MainAxisSize.max,
        children: [
          const Icon(Icons.router, color: Color(0xFF1B5E20), size: 16),
          const SizedBox(width: 4),
          // Router selector
          _buildRouterDropdown(),
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

  /// Build a NodeType-grouped tree of routers using appState.topology.nodes.
  List<TreeGroup<String>> _buildRouterGroups() {
    final appState = Provider.of<Appstate>(context, listen: false);
    final nodes = appState.topology.nodes;

    // Map NodeType.name → [routerName, …]
    final Map<String, List<String>> byType = {};
    for (final node in nodes) {
      final typeName = node.type.name; // 'P', 'PE', 'CE', 'RR', 'Device'
      byType.putIfAbsent(typeName, () => []).add(node.name);
    }

    // Only include routers that have data
    final allIds = _allRouterIds.toSet();
    final groups = <TreeGroup<String>>[];

    // Preferred order for display
    const typeOrder = ['P', 'PE', 'CE', 'RR', 'Device'];
    final orderedTypes = [
      ...typeOrder.where((t) => byType.containsKey(t)),
      ...byType.keys.where((t) => !typeOrder.contains(t)),
    ];

    for (final typeName in orderedTypes) {
      final children = (byType[typeName] ?? [])
          .where((n) => allIds.contains(n))
          .map((n) => (n, n))
          .toList();
      if (children.isNotEmpty) {
        groups.add(TreeGroup(label: typeName, children: children));
      }
    }

    // Any routers not in the topology go into Ungrouped
    final covered = byType.values.expand((v) => v).toSet();
    final uncovered = allIds.difference(covered).toList()..sort();
    if (uncovered.isNotEmpty) {
      groups.add(TreeGroup(
        label: 'Ungrouped',
        children: uncovered.map((n) => (n, n)).toList(),
      ));
    }

    return groups;
  }

  Widget _buildRouterDropdown() {
    final label = _allRouters
        ? 'All Routers'
        : _selectedRouters.isEmpty
            ? 'No Routers'
            : _selectedRouters.length == 1
                ? _selectedRouters.first
                : '${_selectedRouters.length} Routers';

    final groups = _buildRouterGroups();

    return TreeSelectDropdown<String>(
      label: label,
      icon: Icons.router,
      groups: groups,
      selected: _allRouters ? null : _selectedRouters,
      onAllToggled: () {
        setState(() {
          _allRouters = true;
          _selectedRouters.clear();
        });
      },
      onItemToggled: (id) {
        setState(() {
          // When coming from "all selected" mode, seed _selectedRouters with
          // ALL routers first so toggling one item immediately reflects the
          // correct state (all others remain selected, clicked one deselects).
          if (_allRouters) {
            _selectedRouters.addAll(_allRouterIds);
          }
          _allRouters = false;
          if (_selectedRouters.contains(id)) {
            _selectedRouters.remove(id);
          } else {
            _selectedRouters.add(id);
          }
          if (_selectedRouters.isEmpty) _allRouters = true;
        });
      },
      onGroupToggled: (group, select) {
        setState(() {
          // When coming from "all selected" mode, seed _selectedRouters with
          // ALL routers first, then add/remove the group's children so that
          // clicking a group checkbox selects/deselects only that group.
          if (_allRouters) {
            _selectedRouters.addAll(_allRouterIds);
          }
          _allRouters = false;
          for (final (item, _) in group.children) {
            if (select) {
              _selectedRouters.add(item);
            } else {
              _selectedRouters.remove(item);
            }
          }
          if (_selectedRouters.isEmpty) _allRouters = true;
        });
      },
    );
  }

  Widget _buildPeriodDropdown() {
    return DropdownButton<TimePeriod>(
      value: _period,
      isDense: true,
      style: const TextStyle(fontSize: 12, color: Color(0xFF1B5E20)),
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

    return MultiSelectDropdown<RouterMetric>(
      label: label,
      icon: Icons.bar_chart,
      items: RouterMetric.values,
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

    final visibleIds = _visibleRouterIds;
    if (visibleIds.isEmpty || _data.isEmpty) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.info_outline, color: Colors.grey, size: 40),
            SizedBox(height: 8),
            Text('No router data available',
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

  Widget _buildChartWithTooltip(List<String> routerIds) {
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
              _buildChart(routerIds),
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

  Widget _buildChart(List<String> routerIds) {
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

    // Build line series: one per (router, metric) combination.
    // Keep a parallel label list so the tooltip can show "router • metric".
    final bars = <LineChartBarData>[];
    // Each entry maps barIndex → (routerId, metric)
    final barLabels = <(String, RouterMetric)>[];
    int colorIdx = 0;

    double maxY = 0;

    for (final routerId in routerIds) {
      final points = _data[routerId] ?? [];
      if (points.isEmpty) continue;

      for (final metric in _selectedMetrics) {
        final spots = <FlSpot>[];
        for (final pt in points) {
          final ts = (pt['timestamp'] as num?)?.toDouble();
          if (ts == null || ts < windowStart) continue; // skip out-of-window points
          final val = _extractMetric(pt, metric);
          spots.add(FlSpot(ts, val));
          if (val > maxY) maxY = val;
        }
        if (spots.isEmpty) continue;

        final color = _kRouterColors[colorIdx % _kRouterColors.length];
        colorIdx++;

        bars.add(LineChartBarData(
          spots: spots,
          isCurved: false,
          color: color,
          barWidth: 1.8,
          dotData: const FlDotData(show: false),
          belowBarData: BarAreaData(show: true, color: color.withOpacity(0.06)),
        ));

        barLabels.add((routerId, metric));
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

            // Group metrics by router so each router gets one tooltip line.
            final Map<String, List<(LineBarSpot, RouterMetric)>> grouped = {};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx < barLabels.length) {
                final (routerId, metric) = barLabels[idx];
                grouped.putIfAbsent(routerId, () => []).add((s, metric));
              }
            }

            final entries = <({Color color, String text})>[];
            final seen = <String>{};
            for (final s in spots) {
              final idx = s.barIndex;
              if (idx >= barLabels.length) continue;
              final (routerId, metric) = barLabels[idx];
              if (seen.contains(routerId)) continue;
              seen.add(routerId);
              final routerEntries = grouped[routerId]!;
              final metricParts = routerEntries
                  .map((e) => '${e.$2.shortLabel}: ${e.$2.formatValue(e.$1.y)}')
                  .join('  ');
              entries.add((color: s.bar.color ?? Colors.blue, text: '$routerId  $metricParts'));
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
