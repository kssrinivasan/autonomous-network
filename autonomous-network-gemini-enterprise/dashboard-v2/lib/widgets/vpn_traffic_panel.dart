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
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/vpn_info.dart';
import '../utils/APIService.dart';

// ── Phase → colour mapping ───────────────────────────────────────────────────

Color _phaseColor(String phase) {
  switch (phase.toLowerCase()) {
    case 'ready':
    case 'running':
    case 'operational':
      return Colors.green;
    case 'deploying':
    case 'pending':
    case 'processing':
    case 'waiting':
      return Colors.orange;
    case 'failed':
    case 'error':
      return Colors.red;
    case 'deleting':
      return Colors.deepOrange;
    default:
      return Colors.grey;
  }
}

// ── Shared badge widget ──────────────────────────────────────────────────────

class _PhaseBadge extends StatelessWidget {
  final String phase;
  const _PhaseBadge({required this.phase});

  @override
  Widget build(BuildContext context) {
    final color = _phaseColor(phase);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.6)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 5),
          Text(
            phase,
            style: TextStyle(
              color: color,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

// ── Main panel widget ────────────────────────────────────────────────────────

/// Panel that shows VyOSL3VPN resources grouped with their related TrafficTests.
/// Each VPN card contains:
///   • VPN header (name, phase, routers, underlay)
///   • Collapsible "Route Info" sub-section with per-router VRF RD/RT table
///   • Collapsible "Traffic Tests" sub-section with related test cards
///
/// Traffic tests not linked to any VPN appear at the bottom in a separate section.
class VpnTrafficPanel extends StatefulWidget {
  const VpnTrafficPanel({super.key});

  @override
  State<VpnTrafficPanel> createState() => _VpnTrafficPanelState();
}

class _VpnTrafficPanelState extends State<VpnTrafficPanel> {
  String? _selectedVpnName;
  String? _selectedTestName;

  /// VPN names whose "Route Info" sub-section is expanded.
  final Set<String> _expandedRouteInfo = {};

  /// VPN names whose "Traffic Tests" sub-section is expanded.
  final Set<String> _expandedTests = {};

  // ── Performance section state ──────────────────────────────────────────

  /// VPN names whose "Performance" sub-section is expanded.
  final Set<String> _expandedPerf = {};

  /// VPN names currently fetching flow metrics (spinner shown in toggle).
  final Set<String> _loadingPerf = {};

  /// Latest flow metrics keyed by VPN name (all linked tests combined).
  final Map<String, List<TrafficFlowMetrics>> _perfMetrics = {};

  /// Cache of linked tests per VPN updated each build — lets the poll timer
  /// re-fetch without needing a BuildContext.
  Map<String, List<TrafficTestInfo>> _currentLinkedTests = {};

  /// 20-second poll timer; active only while ≥1 perf section is expanded.
  Timer? _perfTimer;

  /// Per-VPN horizontal scroll controllers for the Route Info table.
  final Map<String, ScrollController> _routeScrollControllers = {};

  /// Per-VPN horizontal scroll controllers for the Flow Performance table.
  final Map<String, ScrollController> _flowPerfScrollControllers = {};

  ScrollController _routeScrollController(String vpnName) =>
      _routeScrollControllers.putIfAbsent(
        vpnName,
        () => ScrollController(),
      );

  ScrollController _flowPerfScrollController(String vpnName) =>
      _flowPerfScrollControllers.putIfAbsent(
        vpnName,
        () => ScrollController(),
      );

  @override
  void dispose() {
    _perfTimer?.cancel();
    for (final c in _routeScrollControllers.values) {
      c.dispose();
    }
    for (final c in _flowPerfScrollControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  // ── Text styles ─────────────────────────────────────────────────────────

  static const _sectionHeaderStyle = TextStyle(
    fontWeight: FontWeight.bold,
    fontSize: 12,
    color: Color(0xFF0D47A1),
  );

  static const _labelStyle = TextStyle(fontSize: 10, color: Colors.black54);
  static const _valueStyle = TextStyle(fontSize: 11, color: Colors.black87);
  static const _nameStyle = TextStyle(
    fontWeight: FontWeight.w600,
    fontSize: 12,
    color: Colors.black87,
  );

  // ── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, _) {
        final linkedTests = <String, List<TrafficTestInfo>>{};
        final unlinkedTests = <TrafficTestInfo>[];

        for (final test in appState.trafficTests) {
          if (test.vpnRef != null && test.vpnRef!.isNotEmpty) {
            linkedTests.putIfAbsent(test.vpnRef!, () => []).add(test);
          } else {
            unlinkedTests.add(test);
          }
        }

        // Keep the linked-tests cache in sync so the poll timer can re-fetch.
        _currentLinkedTests = linkedTests;

        final deletingVpn = appState.deletingVpnName;

        return Container(
          color: const Color(0xFFF5F7FA),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _buildPanelHeader(appState),
              // VPN delete progress banner
              if (deletingVpn != null) _buildVpnDeletingBanner(deletingVpn),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.only(bottom: 16),
                  children: [
                    if (appState.vpns.isEmpty && appState.trafficTests.isEmpty)
                      _buildEmptyState()
                    else ...[
                      // One card per VPN
                      for (final vpn in appState.vpns)
                        _buildVpnCard(
                          vpn,
                          linkedTests[vpn.name] ?? [],
                          appState,
                        ),
                      // Unlinked tests section
                      if (unlinkedTests.isNotEmpty)
                        _buildUnlinkedTestsSection(unlinkedTests, appState),
                    ],
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  // ── VPN delete progress banner ────────────────────────────────────────────

  Widget _buildVpnDeletingBanner(String vpnName) {
    return Container(
      color: Colors.deepOrange.shade50,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        children: [
          const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: Colors.deepOrange,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Deleting VPN "$vpnName" and linked tests…',
              style: TextStyle(
                fontSize: 11,
                color: Colors.deepOrange.shade800,
                fontWeight: FontWeight.w500,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  // ── Panel title bar ──────────────────────────────────────────────────────

  Widget _buildPanelHeader(Appstate appState) {
    final hasHighlight = appState.highlightedNodeIds.isNotEmpty;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      color: const Color(0xFFE3F2FD),
      child: Row(
        children: [
          const Icon(Icons.hub_outlined, color: Color(0xFF0D47A1), size: 18),
          const SizedBox(width: 8),
          const Expanded(
            child: Text(
              'VPNs & Traffic Tests',
              style: TextStyle(
                color: Color(0xFF0D47A1),
                fontWeight: FontWeight.bold,
                fontSize: 13,
              ),
            ),
          ),
          if (hasHighlight)
            Tooltip(
              message: 'Clear topology highlight',
              child: IconButton(
                icon: const Icon(Icons.highlight_off,
                    color: Color(0xFF0D47A1), size: 18),
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  setState(() {
                    _selectedVpnName = null;
                    _selectedTestName = null;
                  });
                  appState.clearHighlight();
                },
              ),
            ),
          Tooltip(
            message: 'Refresh',
            child: IconButton(
              icon: const Icon(Icons.refresh, color: Color(0xFF0D47A1), size: 18),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              onPressed: () => appState.refreshVpnsAndTests(),
            ),
          ),
        ],
      ),
    );
  }

  // ── VPN card ─────────────────────────────────────────────────────────────

  Widget _buildVpnCard(
    VpnInfo vpn,
    List<TrafficTestInfo> relatedTests,
    Appstate appState,
  ) {
    final isSelected = _selectedVpnName == vpn.name;
    final routeExpanded = _expandedRouteInfo.contains(vpn.name);
    final testsExpanded = _expandedTests.contains(vpn.name);

    return AnimatedContainer(
      duration: const Duration(milliseconds: 150),
      margin: const EdgeInsets.fromLTRB(8, 8, 8, 0),
      decoration: BoxDecoration(
        color: isSelected ? Colors.blue.shade50 : Colors.white,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: isSelected ? Colors.blue.shade300 : Colors.grey.shade200,
          width: isSelected ? 1.5 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: isSelected
                ? Colors.blue.withValues(alpha: 0.10)
                : Colors.black.withValues(alpha: 0.04),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── VPN header (selectable) ──────────────────────────────────────
          InkWell(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(10)),
            onTap: () {
              setState(() {
                if (isSelected) {
                  _selectedVpnName = null;
                  appState.clearHighlight();
                } else {
                  _selectedVpnName = vpn.name;
                  _selectedTestName = null;
                  appState.highlightVpnNodes(vpn);
                }
              });
            },
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Name + phase + delete button
                  Row(
                    children: [
                      const Icon(Icons.vpn_lock,
                          size: 14, color: Color(0xFF0D47A1)),
                      const SizedBox(width: 5),
                      Expanded(
                        child: Text(
                          vpn.name,
                          style: _nameStyle,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      _PhaseBadge(phase: vpn.phase),
                      const SizedBox(width: 4),
                      // VPN delete button — disabled while any delete is in progress
                      _buildVpnDeleteButton(vpn, appState),
                    ],
                  ),
                  // Underlay ref
                  if (vpn.underlayRef != null) ...[
                    const SizedBox(height: 3),
                    Row(
                      children: [
                        const SizedBox(width: 19),
                        Text('Underlay: ', style: _labelStyle),
                        Expanded(
                          child: Text(
                            vpn.underlayRef!,
                            style: const TextStyle(
                                fontSize: 10, color: Colors.black54),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ],
                  // Router chips
                  if (vpn.routers.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 4,
                      runSpacing: 2,
                      children: vpn.routers
                          .map(
                            (r) => _RouterChip(name: r),
                          )
                          .toList(),
                    ),
                  ],
                  // Status message
                  if (vpn.message.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      vpn.message,
                      style: _labelStyle,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ],
              ),
            ),
          ),

          // ── Route Info sub-section ───────────────────────────────────────
          if (vpn.hasRouteInfo) ...[
            const Divider(height: 1, thickness: 1, indent: 10, endIndent: 10),
            _buildSubSectionToggle(
              label: 'Route Info',
              icon: Icons.alt_route,
              expanded: routeExpanded,
              onToggle: () => setState(() {
                if (routeExpanded) {
                  _expandedRouteInfo.remove(vpn.name);
                } else {
                  _expandedRouteInfo.add(vpn.name);
                }
              }),
            ),
            if (routeExpanded) _buildRouteInfoTable(vpn),
          ],

          // ── Traffic Tests sub-section ─────────────────────────────────────
          const Divider(height: 1, thickness: 1, indent: 10, endIndent: 10),
          _buildSubSectionToggle(
            label: 'Traffic Tests',
            icon: Icons.speed,
            count: relatedTests.length,
            expanded: testsExpanded,
            onToggle: () => setState(() {
              if (testsExpanded) {
                _expandedTests.remove(vpn.name);
              } else {
                _expandedTests.add(vpn.name);
              }
            }),
          ),
          if (testsExpanded) ...[
            if (relatedTests.isEmpty)
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                child: Text(
                  'No traffic tests linked to this VPN',
                  style: _labelStyle.copyWith(fontStyle: FontStyle.italic),
                ),
              )
            else
              for (final test in relatedTests)
                _buildTestCard(test, appState, indent: true),
            const SizedBox(height: 4),
          ],

          // ── Performance sub-section ──────────────────────────────────────
          if (relatedTests.isNotEmpty) ...[
            const Divider(height: 1, thickness: 1, indent: 10, endIndent: 10),
            _buildPerfToggle(vpn.name, relatedTests),
            if (_expandedPerf.contains(vpn.name))
              _buildPerfSection(vpn.name, relatedTests),
          ],
        ],
      ),
    );
  }

  // ── Sub-section toggle row ────────────────────────────────────────────────

  Widget _buildSubSectionToggle({
    required String label,
    required IconData icon,
    required bool expanded,
    required VoidCallback onToggle,
    int? count,
  }) {
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            Icon(icon, size: 13, color: const Color(0xFF0D47A1)),
            const SizedBox(width: 5),
            Text(label, style: _sectionHeaderStyle),
            if (count != null) ...[
              const SizedBox(width: 5),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: const Color(0xFF0D47A1).withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '$count',
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0D47A1),
                  ),
                ),
              ),
            ],
            const Spacer(),
            Icon(
              expanded ? Icons.expand_less : Icons.expand_more,
              size: 16,
              color: Colors.black38,
            ),
          ],
        ),
      ),
    );
  }

  // ── Route info table ──────────────────────────────────────────────────────

  Widget _buildRouteInfoTable(VpnInfo vpn) {
    const headerStyle = TextStyle(
      fontSize: 10,
      fontWeight: FontWeight.bold,
      color: Colors.black54,
    );
    const cellStyle = TextStyle(fontSize: 10, color: Colors.black87);
    const monoStyle = TextStyle(
      fontSize: 10,
      color: Color(0xFF1A237E),
      fontFamily: 'monospace',
    );

    final scrollCtrl = _routeScrollController(vpn.name);

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xFFF0F4FF),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: Colors.blue.shade100),
        ),
        child: Scrollbar(
          controller: scrollCtrl,
          thumbVisibility: true,
          child: SingleChildScrollView(
            controller: scrollCtrl,
            scrollDirection: Axis.horizontal,
            child: DataTable(
            headingRowHeight: 28,
            dataRowMinHeight: 26,
            dataRowMaxHeight: 32,
            horizontalMargin: 10,
            columnSpacing: 12,
            headingRowColor: WidgetStateProperty.resolveWith<Color?>(
                (states) => Colors.blue.shade50),
            columns: const [
              DataColumn(label: Text('Router', style: headerStyle)),
              DataColumn(label: Text('VRF', style: headerStyle)),
              DataColumn(label: Text('RD', style: headerStyle)),
              DataColumn(label: Text('RT Export', style: headerStyle)),
              DataColumn(label: Text('RT Import', style: headerStyle)),
            ],
            rows: vpn.routerVrfs.map((vrf) {
              String joinRts(List<String> v) =>
                  v.isEmpty ? '—' : v.join(', ');
              return DataRow(cells: [
                DataCell(Text(vrf.router, style: cellStyle)),
                DataCell(Text(vrf.vrf, style: cellStyle)),
                DataCell(Text(vrf.rd ?? '—', style: monoStyle)),
                DataCell(Text(joinRts(vrf.rtExport), style: monoStyle)),
                DataCell(Text(joinRts(vrf.rtImport), style: monoStyle)),
              ]);
            }).toList(),
            ),
          ),
        ),
      ),
    );
  }

  // ── Traffic test card ─────────────────────────────────────────────────────

  Widget _buildTestCard(
    TrafficTestInfo test,
    Appstate appState, {
    bool indent = false,
  }) {
    final isSelected = _selectedTestName == test.name;

    return Padding(
      padding: EdgeInsets.fromLTRB(indent ? 14 : 8, 3, indent ? 14 : 8, 3),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () {
          setState(() {
            if (isSelected) {
              _selectedTestName = null;
              appState.clearHighlight();
            } else {
              _selectedTestName = test.name;
              _selectedVpnName = null;
              appState.highlightTrafficTestNodes(test);
            }
          });
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.all(9),
          decoration: BoxDecoration(
            color: isSelected ? Colors.teal.shade50 : Colors.grey.shade50,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color:
                  isSelected ? Colors.teal.shade300 : Colors.grey.shade200,
              width: isSelected ? 1.5 : 1,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Name + phase + delete button
              Row(
                children: [
                  const Icon(Icons.speed, size: 12, color: Colors.teal),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      test.name,
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 11,
                        color: Colors.black87,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  _PhaseBadge(phase: test.phase),
                  const SizedBox(width: 2),
                  _buildTestDeleteButton(test, appState),
                ],
              ),

              const SizedBox(height: 4),

              // Traffic params line: Protocol • Bandwidth • Pattern • Ns [↔]
              _buildTrafficParamsLine(test),

              const SizedBox(height: 4),

              // Source(s) → Destination
              _buildFlowLine(test),

              // Timing
              if (test.startTime != null) ...[
                const SizedBox(height: 3),
                Row(
                  children: [
                    const Icon(Icons.schedule,
                        size: 10, color: Colors.black38),
                    const SizedBox(width: 3),
                    Text(
                      'Started: ${_formatTime(test.startTime!)}',
                      style: _labelStyle,
                    ),
                    if (test.endTime != null) ...[
                      const Text('  →  ', style: TextStyle(fontSize: 10)),
                      Text(_formatTime(test.endTime!), style: _labelStyle),
                    ],
                  ],
                ),
              ],

              // Status message
              if (test.message.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  test.message,
                  style: _labelStyle,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// One-line compact summary: Protocol • Bandwidth • Pattern • Ns [↔]
  Widget _buildTrafficParamsLine(TrafficTestInfo test) {
    final parts = <String>[];
    if (test.protocol != null) parts.add(test.protocol!);
    if (test.bandwidth != null) parts.add(test.bandwidth!);
    if (test.patternType != null) parts.add(test.patternType!);
    parts.add('${test.duration}s');

    return Row(
      children: [
        Expanded(
          child: Text(
            parts.join(' • '),
            style: const TextStyle(
              fontSize: 10,
              color: Color(0xFF00695C),
              fontWeight: FontWeight.w500,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (test.bidirectional)
          const Tooltip(
            message: 'Bidirectional',
            child: Icon(Icons.swap_horiz, size: 14, color: Colors.teal),
          ),
      ],
    );
  }

  /// Source device(s) → Destination line.
  Widget _buildFlowLine(TrafficTestInfo test) {
    final srcs = test.sourceDevices;
    final dest = test.destinationDevice;
    if (srcs.isEmpty && dest == null) return const SizedBox.shrink();

    final srcText = srcs.isEmpty
        ? '?'
        : srcs.length == 1
            ? srcs.first
            : '${srcs.first} +${srcs.length - 1}';

    return Row(
      children: [
        const Icon(Icons.device_hub, size: 10, color: Colors.black38),
        const SizedBox(width: 3),
        Flexible(
          child: Text(
            srcText,
            style: _valueStyle.copyWith(fontSize: 10),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 4),
          child: Icon(Icons.arrow_forward, size: 10, color: Colors.black38),
        ),
        Flexible(
          child: Text(
            dest ?? '?',
            style: _valueStyle.copyWith(fontSize: 10),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        // Show all sources tooltip if there are multiple
        if (srcs.length > 1)
          Tooltip(
            message: srcs.join('\n'),
            child: const Icon(Icons.info_outline,
                size: 12, color: Colors.black38),
          ),
      ],
    );
  }

  // ── Unlinked tests section ────────────────────────────────────────────────

  Widget _buildUnlinkedTestsSection(
    List<TrafficTestInfo> tests,
    Appstate appState,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Section header
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 10, 8, 4),
          child: Row(
            children: [
              const Icon(Icons.link_off, size: 13, color: Colors.black45),
              const SizedBox(width: 5),
              const Text(
                'Unlinked Traffic Tests',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                  color: Colors.black54,
                ),
              ),
              const SizedBox(width: 5),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.black12,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '${tests.length}',
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Colors.black54,
                  ),
                ),
              ),
            ],
          ),
        ),
        for (final test in tests) _buildTestCard(test, appState),
      ],
    );
  }

  // ── Delete buttons ────────────────────────────────────────────────────────

  /// Small trash-icon button for a VPN card.  Disabled while any VPN delete
  /// is in progress.
  Widget _buildVpnDeleteButton(VpnInfo vpn, Appstate appState) {
    final isDeleting = appState.deletingVpnName != null;
    return Tooltip(
      message: isDeleting ? 'Delete in progress…' : 'Delete VPN and linked tests',
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: isDeleting
            ? null
            : () => _confirmDeleteVpn(
                  vpn,
                  appState.trafficTests
                      .where((t) => t.vpnRef == vpn.name)
                      .length,
                  appState,
                ),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(
            Icons.delete_outline,
            size: 15,
            color: isDeleting ? Colors.black26 : Colors.red.shade400,
          ),
        ),
      ),
    );
  }

  /// Small trash-icon button for a test card.
  /// Shows a spinner and is disabled while the test's delete is in flight.
  Widget _buildTestDeleteButton(TrafficTestInfo test, Appstate appState) {
    final isDeleting = appState.deletingTestNames.contains(test.name);
    if (isDeleting) {
      return const Padding(
        padding: EdgeInsets.all(4),
        child: SizedBox(
          width: 13,
          height: 13,
          child: CircularProgressIndicator(
            strokeWidth: 1.5,
            color: Colors.red,
          ),
        ),
      );
    }
    return Tooltip(
      message: 'Delete traffic test',
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: () => _confirmDeleteTest(test, appState),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(
            Icons.delete_outline,
            size: 13,
            color: Colors.red.shade300,
          ),
        ),
      ),
    );
  }

  // ── Confirmation dialogs ──────────────────────────────────────────────────

  Future<void> _confirmDeleteVpn(
    VpnInfo vpn,
    int linkedTestCount,
    Appstate appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.deepOrange),
            SizedBox(width: 8),
            Text('Delete VPN', style: TextStyle(fontSize: 16)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            RichText(
              text: TextSpan(
                style: const TextStyle(fontSize: 13, color: Colors.black87),
                children: [
                  const TextSpan(text: 'Delete VPN '),
                  TextSpan(
                    text: vpn.name,
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const TextSpan(text: '?'),
                ],
              ),
            ),
            if (linkedTestCount > 0) ...[
              const SizedBox(height: 8),
              Text(
                '$linkedTestCount linked traffic test${linkedTestCount == 1 ? '' : 's'} will also be deleted.',
                style: const TextStyle(
                    fontSize: 12, color: Colors.deepOrange),
              ),
            ],
            const SizedBox(height: 8),
            const Text(
              'This action cannot be undone.',
              style: TextStyle(fontSize: 11, color: Colors.black54),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: Colors.deepOrange),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      final accepted = await appState.deleteVpn(vpn.name);
      if (!accepted && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('A VPN delete is already in progress. Please wait.'),
            backgroundColor: Colors.deepOrange,
          ),
        );
      }
    }
  }

  Future<void> _confirmDeleteTest(
    TrafficTestInfo test,
    Appstate appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.orange),
            SizedBox(width: 8),
            Text('Delete Test', style: TextStyle(fontSize: 16)),
          ],
        ),
        content: RichText(
          text: TextSpan(
            style: const TextStyle(fontSize: 13, color: Colors.black87),
            children: [
              const TextSpan(text: 'Delete traffic test '),
              TextSpan(
                text: test.name,
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              const TextSpan(text: '?'),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      await appState.deleteTrafficTest(test.name);
    }
  }

  // ── Empty state ───────────────────────────────────────────────────────────

  Widget _buildEmptyState() {
    return const Padding(
      padding: EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      child: Column(
        children: [
          Icon(Icons.hub_outlined, size: 32, color: Colors.black26),
          SizedBox(height: 8),
          Text(
            'No VPNs or traffic tests provisioned',
            style: TextStyle(color: Colors.black38, fontSize: 12),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  // ── Performance section ───────────────────────────────────────────────────

  /// Toggle row for the Performance sub-section.
  Widget _buildPerfToggle(String vpnName, List<TrafficTestInfo> tests) {
    final expanded = _expandedPerf.contains(vpnName);
    final isLoading = _loadingPerf.contains(vpnName);
    final flows = _perfMetrics[vpnName] ?? [];
    final runningCount = flows.where((f) => f.isRunning).length;

    return InkWell(
      onTap: () => _togglePerfSection(vpnName, tests),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            Icon(Icons.analytics_outlined,
                size: 13, color: const Color(0xFF0D47A1)),
            const SizedBox(width: 5),
            Text('Performance', style: _sectionHeaderStyle),
            if (isLoading) ...[
              const SizedBox(width: 6),
              const SizedBox(
                width: 10,
                height: 10,
                child: CircularProgressIndicator(
                  strokeWidth: 1.5,
                  color: Color(0xFF0D47A1),
                ),
              ),
            ] else if (runningCount > 0) ...[
              const SizedBox(width: 5),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.green.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '$runningCount active',
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Colors.green,
                  ),
                ),
              ),
            ],
            const Spacer(),
            Icon(
              expanded ? Icons.expand_less : Icons.expand_more,
              size: 16,
              color: Colors.black38,
            ),
          ],
        ),
      ),
    );
  }

  /// Body of the Performance sub-section.
  Widget _buildPerfSection(String vpnName, List<TrafficTestInfo> tests) {
    final flows = _perfMetrics[vpnName] ?? [];
    final isLoading = _loadingPerf.contains(vpnName);

    if (isLoading && flows.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Center(
          child: SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }

    if (flows.isEmpty) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(14, 4, 14, 10),
        child: Text(
          'No flow metrics yet — tests may still be starting.',
          style: _labelStyle.copyWith(fontStyle: FontStyle.italic),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Aggregate stat bar
          _buildPerfAggregateBar(flows),
          const SizedBox(height: 6),
          // Flow performance table (horizontally scrollable)
          _buildFlowPerfTable(vpnName, tests, flows),
        ],
      ),
    );
  }

  /// Aggregate stat bar across all flows for a VPN.
  ///
  /// Throughput is split into ↑ Sent (forward source flows) and ↓ Recv
  /// (reverse source flows, i.e. _rev suffix) so that a bidirectional 90 Mbps
  /// test shows "↑ 90 Mbps  ↓ 90 Mbps" rather than a misleading "180 Mbps".
  Widget _buildPerfAggregateBar(List<TrafficFlowMetrics> flows) {
    // Forward source flows: role=source, flowId does NOT end with '_rev'.
    // These represent the configured forward direction (d1 → d2).
    final fwdSrcFlows = flows
        .where((f) => f.role == 'source' && !f.flowId.endsWith('_rev'))
        .toList();
    // Reverse source flows: role=source, flowId ends with '_rev'.
    // These represent the reverse direction (d2 → d1) of a bidirectional test.
    final revSrcFlows = flows
        .where((f) => f.role == 'source' && f.flowId.endsWith('_rev'))
        .toList();
    // Destination-role flows for latency / jitter / loss.
    final dstFlows = flows.where((f) => f.role == 'destination').toList();

    // ↑ Sent: sum of forward source throughput
    double? sumSent;
    for (final f in fwdSrcFlows) {
      final tp = f.throughputSentBps ?? f.throughputBps;
      if (tp != null) sumSent = (sumSent ?? 0) + tp;
    }
    // Fallback: if we only have destination metrics for forward flows, use throughputRecvBps
    if (sumSent == null) {
      final fwdDstFlows2 = dstFlows.where((f) => !f.flowId.endsWith('_rev')).toList();
      for (final f in fwdDstFlows2) {
        final tp = f.throughputRecvBps;
        if (tp != null) sumSent = (sumSent ?? 0) + tp;
      }
    }

    // ↓ Recv: sum of reverse source throughput (what the far end is sending back).
    //
    // Two patterns exist depending on which device the metrics are scraped from:
    //   Pattern A: _rev flow has role=source  → use throughputSentBps
    //              (e.g. red VPN: d1-red is both fwd-dst and rev-src)
    //   Pattern B: _rev flow has role=destination → use throughputRecvBps
    //              (e.g. blue VPN: spoke is fwd-src and rev-dst)
    double? sumRecv;
    for (final f in revSrcFlows) {
      final tp = f.throughputSentBps ?? f.throughputBps;
      if (tp != null) sumRecv = (sumRecv ?? 0) + tp;
    }
    // Fallback to Pattern B: _rev destination-role flows' inbound throughput.
    if (sumRecv == null) {
      final revDstFlows = flows
          .where((f) => f.role == 'destination' && f.flowId.endsWith('_rev'))
          .toList();
      for (final f in revDstFlows) {
        final tp = f.throughputRecvBps;
        if (tp != null) sumRecv = (sumRecv ?? 0) + tp;
      }
    }

    double? avgLatency;
    List<double> latVals =
        dstFlows.map((f) => f.latencyMs).whereType<double>().toList();
    if (latVals.isEmpty) latVals = flows.map((f) => f.latencyMs).whereType<double>().toList();
    if (latVals.isNotEmpty) {
      avgLatency = latVals.reduce((a, b) => a + b) / latVals.length;
    }

    double? avgLoss;
    List<double> lossVals =
        dstFlows.map((f) => f.packetLossPct).whereType<double>().toList();
    if (lossVals.isEmpty) lossVals = flows.map((f) => f.packetLossPct).whereType<double>().toList();
    if (lossVals.isNotEmpty) {
      avgLoss = lossVals.reduce((a, b) => a + b) / lossVals.length;
    }

    double? avgJitter;
    List<double> jitterVals =
        dstFlows.map((f) => f.jitterMs).whereType<double>().toList();
    if (jitterVals.isEmpty) jitterVals = flows.map((f) => f.jitterMs).whereType<double>().toList();
    if (jitterVals.isNotEmpty) {
      avgJitter = jitterVals.reduce((a, b) => a + b) / jitterVals.length;
    }

    // Throughput label helper
    String throughputLabel(double? bps) {
      if (bps == null) return '—';
      if (bps >= 1e9) return '${(bps / 1e9).toStringAsFixed(1)} Gbps';
      if (bps >= 1e6) return '${(bps / 1e6).toStringAsFixed(1)} Mbps';
      if (bps >= 1e3) return '${(bps / 1e3).toStringAsFixed(0)} Kbps';
      return '${bps.toStringAsFixed(0)} bps';
    }

    Color latencyColor(double? ms) {
      if (ms == null) return Colors.black54;
      if (ms < 10) return Colors.green.shade700;
      if (ms < 50) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    Color lossColor(double? pct) {
      if (pct == null) return Colors.black54;
      if (pct < 0.5) return Colors.green.shade700;
      if (pct < 2) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    Color jitterColor(double? ms) {
      if (ms == null) return Colors.black54;
      if (ms < 5) return Colors.green.shade700;
      if (ms < 20) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    Widget statCell(String label, String value, Color valueColor) {
      return Expanded(
        child: Column(
          children: [
            Text(
              value,
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: valueColor,
              ),
            ),
            Text(label, style: _labelStyle),
          ],
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(vertical: 7, horizontal: 6),
      decoration: BoxDecoration(
        color: const Color(0xFFF0F4FF),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.blue.shade100),
      ),
      child: Row(
        children: [
          statCell(
            '↑ Sent',
            throughputLabel(sumSent),
            const Color(0xFF0D47A1),
          ),
          statCell(
            '↓ Recv',
            throughputLabel(sumRecv),
            const Color(0xFF1565C0),
          ),
          statCell(
            'Latency',
            avgLatency != null
                ? '${avgLatency.toStringAsFixed(1)} ms'
                : '—',
            latencyColor(avgLatency),
          ),
          statCell(
            'Pkt Loss',
            avgLoss != null ? '${avgLoss.toStringAsFixed(2)}%' : '—',
            lossColor(avgLoss),
          ),
          statCell(
            'Jitter',
            avgJitter != null
                ? '${avgJitter.toStringAsFixed(1)} ms'
                : '—',
            jitterColor(avgJitter),
          ),
        ],
      ),
    );
  }

  // ── Flow performance table (horizontally scrollable) ─────────────────────

  /// Horizontally scrollable DataTable showing per-flow performance metrics.
  Widget _buildFlowPerfTable(
    String vpnName,
    List<TrafficTestInfo> tests,
    List<TrafficFlowMetrics> flows,
  ) {
    const headerStyle = TextStyle(
      fontSize: 10,
      fontWeight: FontWeight.bold,
      color: Colors.black54,
    );

    // Helper: format throughput
    String tpLabel(double? bps) {
      if (bps == null) return '—';
      if (bps >= 1e9) return '${(bps / 1e9).toStringAsFixed(1)} Gbps';
      if (bps >= 1e6) return '${(bps / 1e6).toStringAsFixed(1)} Mbps';
      if (bps >= 1e3) return '${(bps / 1e3).toStringAsFixed(0)} Kbps';
      return '${bps.toStringAsFixed(0)} bps';
    }

    Color latencyColor(double? ms) {
      if (ms == null) return Colors.black54;
      if (ms < 10) return Colors.green.shade700;
      if (ms < 50) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    Color lossColor(double? pct) {
      if (pct == null) return Colors.black54;
      if (pct < 0.5) return Colors.green.shade700;
      if (pct < 2) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    Color jitterColor(double? ms) {
      if (ms == null) return Colors.black54;
      if (ms < 5) return Colors.green.shade700;
      if (ms < 20) return Colors.orange.shade700;
      return Colors.red.shade700;
    }

    // Build one DataRow per flow
    final rows = flows.map((flow) {
      // Determine throughput to display based on role
      final sentBps = flow.throughputSentBps ?? flow.throughputBps;
      final recvBps = flow.throughputRecvBps;

      // Throughput cell: show ↑ sent for source, ↓ recv for destination
      final String tpText;
      final Color tpColor;
      if (flow.role == 'source') {
        tpText = '↑ ${tpLabel(sentBps)}';
        tpColor = const Color(0xFF0D47A1);
      } else {
        tpText = '↓ ${tpLabel(recvBps)}';
        tpColor = const Color(0xFF1565C0);
      }

      final latMs = flow.latencyMs;
      final jitterMs = flow.jitterMs;
      final lossPct = flow.packetLossPct;
      final sessions = flow.activeSessions;

      final isRunning = flow.isRunning;
      final phaseColor = isRunning ? Colors.green : Colors.grey;

      return DataRow(cells: [
        // Flow ID
        DataCell(
          Tooltip(
            message: flow.flowId,
            child: Text(
              flow.flowId.length > 22
                  ? '${flow.flowId.substring(0, 20)}…'
                  : flow.flowId,
              style: const TextStyle(fontSize: 10, color: Colors.black87),
            ),
          ),
        ),
        // Device
        DataCell(Text(
          flow.device,
          style: const TextStyle(fontSize: 10, color: Colors.black87),
        )),
        // Role
        DataCell(Text(
          flow.role,
          style: TextStyle(
            fontSize: 10,
            color: flow.role == 'source'
                ? const Color(0xFF0D47A1)
                : Colors.teal.shade700,
            fontWeight: FontWeight.w500,
          ),
        )),
        // Protocol
        DataCell(Text(
          flow.protocol.toUpperCase(),
          style: const TextStyle(fontSize: 10, color: Colors.black54),
        )),
        // Throughput
        DataCell(Text(
          tpText,
          style: TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.w600,
            color: tpColor,
          ),
        )),
        // Latency
        DataCell(Text(
          latMs != null ? '${latMs.toStringAsFixed(1)} ms' : '—',
          style: TextStyle(
            fontSize: 10,
            color: latencyColor(latMs),
            fontWeight: FontWeight.w500,
          ),
        )),
        // Jitter
        DataCell(Text(
          jitterMs != null ? '${jitterMs.toStringAsFixed(1)} ms' : '—',
          style: TextStyle(
            fontSize: 10,
            color: jitterColor(jitterMs),
            fontWeight: FontWeight.w500,
          ),
        )),
        // Packet Loss
        DataCell(Text(
          lossPct != null ? '${lossPct.toStringAsFixed(2)}%' : '—',
          style: TextStyle(
            fontSize: 10,
            color: lossColor(lossPct),
            fontWeight: FontWeight.w500,
          ),
        )),
        // Active Sessions
        DataCell(Text(
          sessions != null ? sessions.toStringAsFixed(0) : '—',
          style: const TextStyle(fontSize: 10, color: Colors.black54),
        )),
        // Status
        DataCell(
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  color: phaseColor,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 4),
              Text(
                isRunning ? 'Running' : 'Stopped',
                style: TextStyle(
                  fontSize: 10,
                  color: phaseColor,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
      ]);
    }).toList();

    final scrollCtrl = _flowPerfScrollController(vpnName);

    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFFF0F4FF),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.blue.shade100),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Scrollbar(
            controller: scrollCtrl,
            thumbVisibility: true,
            child: SingleChildScrollView(
              controller: scrollCtrl,
              scrollDirection: Axis.horizontal,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Table header label (inside the scroll so it moves with the table)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(10, 6, 10, 2),
                    child: Row(
                      children: [
                        Icon(Icons.table_chart_outlined,
                            size: 12, color: Colors.blue.shade700),
                        const SizedBox(width: 4),
                        Text(
                          'Flow Performance',
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            color: Colors.blue.shade700,
                          ),
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '(${flows.length} flow${flows.length == 1 ? '' : 's'})',
                          style: const TextStyle(fontSize: 10, color: Colors.black38),
                        ),
                        const SizedBox(width: 8),
                        Icon(Icons.swap_horiz,
                            size: 12, color: Colors.black38),
                        const SizedBox(width: 2),
                        const Text(
                          'scroll',
                          style: TextStyle(fontSize: 9, color: Colors.black38),
                        ),
                      ],
                    ),
                  ),
                  DataTable(
                headingRowHeight: 28,
                dataRowMinHeight: 26,
                dataRowMaxHeight: 32,
                horizontalMargin: 10,
                columnSpacing: 14,
                columns: const [
                  DataColumn(label: Text('Flow ID', style: headerStyle)),
                  DataColumn(label: Text('Device', style: headerStyle)),
                  DataColumn(label: Text('Role', style: headerStyle)),
                  DataColumn(label: Text('Proto', style: headerStyle)),
                  DataColumn(label: Text('Throughput', style: headerStyle)),
                  DataColumn(label: Text('Latency', style: headerStyle)),
                  DataColumn(label: Text('Jitter', style: headerStyle)),
                  DataColumn(label: Text('Pkt Loss', style: headerStyle)),
                  DataColumn(label: Text('Sessions', style: headerStyle)),
                  DataColumn(label: Text('Status', style: headerStyle)),
                ],
                rows: rows,
              ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ── Performance polling logic ─────────────────────────────────────────────

  void _togglePerfSection(String vpnName, List<TrafficTestInfo> tests) {
    setState(() {
      if (_expandedPerf.contains(vpnName)) {
        _expandedPerf.remove(vpnName);
        if (_expandedPerf.isEmpty) {
          _perfTimer?.cancel();
          _perfTimer = null;
        }
      } else {
        _expandedPerf.add(vpnName);
        _fetchPerfForVpn(vpnName, tests);
        // Start the 20s timer if it's not already running.
        if (_perfTimer == null || !_perfTimer!.isActive) {
          _perfTimer = Timer.periodic(
            const Duration(seconds: 20),
            (_) => _refreshExpandedPerf(),
          );
        }
      }
    });
  }

  Future<void> _fetchPerfForVpn(
    String vpnName,
    List<TrafficTestInfo> tests,
  ) async {
    if (!mounted) return;
    setState(() => _loadingPerf.add(vpnName));

    final api = APIService();
    final allFlows = <TrafficFlowMetrics>[];

    for (final test in tests) {
      final flows = await api.fetchTrafficTestMetrics(test.name);
      allFlows.addAll(flows);
    }

    if (mounted) {
      setState(() {
        _perfMetrics[vpnName] = allFlows;
        _loadingPerf.remove(vpnName);
      });
    }
  }

  void _refreshExpandedPerf() {
    for (final vpnName in _expandedPerf.toList()) {
      final tests = _currentLinkedTests[vpnName] ?? [];
      _fetchPerfForVpn(vpnName, tests);
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  String _formatTime(String isoTime) {
    try {
      final dt = DateTime.parse(isoTime).toLocal();
      final h = dt.hour.toString().padLeft(2, '0');
      final m = dt.minute.toString().padLeft(2, '0');
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-'
          '${dt.day.toString().padLeft(2, '0')} $h:$m';
    } catch (_) {
      return isoTime;
    }
  }
}

// ── Router chip ──────────────────────────────────────────────────────────────

class _RouterChip extends StatelessWidget {
  final String name;
  const _RouterChip({required this.name});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.blue.shade50,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.blue.shade200),
      ),
      child: Text(
        name,
        style: TextStyle(fontSize: 10, color: Colors.blue.shade700),
      ),
    );
  }
}
