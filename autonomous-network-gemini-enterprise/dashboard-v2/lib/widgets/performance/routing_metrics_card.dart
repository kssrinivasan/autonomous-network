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

/// Card that displays underlay (Layer 2) routing-protocol metrics for a P/PE
/// router node.
///
/// The [data] map is the JSON response from  GET /metrics/routing/{node_id}
/// and contains sections for OSPF, BGP peers, routing table, FRR collector
/// health, and BFD.
class UnderlayMetricsCard extends StatelessWidget {
  final Map<String, dynamic> data;

  const UnderlayMetricsCard({super.key, required this.data});

  // ── colour palette ──────────────────────────────────────────────────────

  static const Color _headerColor  = Color(0xFF1565C0); // dark blue
  static const Color _okColor      = Color(0xFF2E7D32); // green
  static const Color _warnColor    = Color(0xFFE65100); // orange
  static const Color _errorColor   = Color(0xFFC62828); // red
  static const Color _greyColor    = Colors.black45;
  static const Color _tableHeaderBg = Color(0xFFE3F2FD); // light blue

  // ── text styles ─────────────────────────────────────────────────────────

  static const _titleStyle = TextStyle(
    fontSize: 14,
    fontWeight: FontWeight.bold,
    color: _headerColor,
  );
  static const _sectionStyle = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.bold,
    color: _headerColor,
  );
  static const _introStyle = TextStyle(
    fontSize: 10,
    color: Colors.black54,
    fontStyle: FontStyle.italic,
  );
  static const _labelStyle = TextStyle(fontSize: 10, color: Colors.black54);
  static const _valueStyle = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w600,
    color: Colors.black87,
  );
  static const _tableHeaderStyle = TextStyle(
    fontSize: 9,
    fontWeight: FontWeight.bold,
    color: _headerColor,
  );
  static const _tableCellStyle = TextStyle(
    fontSize: 10,
    color: Colors.black87,
  );

  // ── helpers ─────────────────────────────────────────────────────────────

  /// Format BGP uptime seconds → "Xd Xh Xm" (or "down" if 0 / null).
  String _formatUptime(double? seconds) {
    if (seconds == null) return '—';
    if (seconds <= 0) return 'down';
    final total = seconds.toInt();
    final d = total ~/ 86400;
    final h = (total % 86400) ~/ 3600;
    final m = (total % 3600) ~/ 60;
    if (d > 0) return '${d}d ${h}h';
    if (h > 0) return '${h}h ${m}m';
    return '${m}m';
  }

  Color _uptimeColor(double? seconds) {
    if (seconds == null) return _greyColor;
    if (seconds <= 0) return _errorColor;
    return _okColor;
  }

  Color _routeColor(int? total, int? fib) {
    if (total == null || fib == null) return _greyColor;
    if (total == 0) return _greyColor;
    if (fib < total) return _warnColor;
    return _okColor;
  }

  Color _collectorColor(int val) => val == 1 ? _okColor : _errorColor;

  // ── build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final ospf       = data['ospf']       as Map<String, dynamic>? ?? {};
    final bgpPeers   = (data['bgp_peers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final routes     = (data['routes']    as List?)?.cast<Map<String, dynamic>>() ?? [];
    final collectors = (data['collectors'] as Map<String, dynamic>?) ?? {};
    final bfdPeers   = data['bfd_peers']  as int?;
    final timestamp  = data['timestamp']  as String?;

    // Skip rendering entirely if there is no data (e.g. device node).
    final hasData = ospf.isNotEmpty ||
        bgpPeers.isNotEmpty ||
        routes.isNotEmpty ||
        collectors.isNotEmpty ||
        bfdPeers != null;

    if (!hasData) return const SizedBox.shrink();

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
            // ── Header ──────────────────────────────────────────────────
            Row(
              children: [
                const Icon(Icons.account_tree_outlined,
                    color: _headerColor, size: 16),
                const SizedBox(width: 6),
                const Text('Routing', style: _titleStyle),
                const Spacer(),
                if (timestamp != null)
                  Text(
                    _fmtTimestamp(timestamp),
                    style: _labelStyle.copyWith(fontSize: 9),
                  ),
              ],
            ),
            const Divider(height: 12),

            // ── OSPF ────────────────────────────────────────────────────
            if (ospf.isNotEmpty) ...[
              _sectionHeader('OSPF', Icons.hub_outlined),
              const SizedBox(height: 2),
              const Text(
                'Link-state adjacencies formed with directly connected routers '
                'in the underlay network.',
                style: _introStyle,
              ),
              const SizedBox(height: 6),
              _buildOspfTable(ospf),
              const SizedBox(height: 10),
            ],

            // ── BGP Peers ────────────────────────────────────────────────
            if (bgpPeers.isNotEmpty) ...[
              _sectionHeader('iBGP Peers', Icons.swap_horiz),
              const SizedBox(height: 2),
              const Text(
                'BGP sessions to route reflectors and CE peers. '
                'Uptime shows how long the session has been established; '
                'Pfx Adv is the number of prefixes advertised to each peer.',
                style: _introStyle,
              ),
              const SizedBox(height: 6),
              _buildBgpTable(bgpPeers),
              const SizedBox(height: 10),
            ],

            // ── Routing Table ───────────────────────────────────────────
            if (routes.isNotEmpty) ...[
              _sectionHeader('Routing Table', Icons.route),
              const SizedBox(height: 2),
              const Text(
                'Route counts per address family and VRF. '
                'Total includes all RIB entries; FIB shows routes '
                'successfully programmed into the forwarding plane.',
                style: _introStyle,
              ),
              const SizedBox(height: 6),
              _buildRoutesTable(routes),
              const SizedBox(height: 10),
            ],

            // ── BFD ──────────────────────────────────────────────────────
            if (bfdPeers != null) ...[
              Row(
                children: [
                  _sectionHeader('BFD', Icons.timer_outlined),
                  const SizedBox(width: 8),
                  Text(
                    '$bfdPeers peer${bfdPeers == 1 ? '' : 's'}',
                    style: _valueStyle.copyWith(
                      color: bfdPeers > 0 ? _okColor : _greyColor,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 2),
              const Text(
                'Bidirectional Forwarding Detection sessions for fast link-failure detection.',
                style: _introStyle,
              ),
              const SizedBox(height: 10),
            ],

            // ── FRR Collector Health ─────────────────────────────────────
            if (collectors.isNotEmpty) ...[
              _sectionHeader('FRR Collectors', Icons.monitor_heart_outlined),
              const SizedBox(height: 2),
              const Text(
                'Health of each FRR exporter collector. '
                'A green dot means the last scrape succeeded.',
                style: _introStyle,
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 4,
                children: collectors.entries.map((e) {
                  final up = (e.value as num?)?.toInt() ?? 0;
                  final color = _collectorColor(up);
                  return Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        up == 1
                            ? Icons.circle
                            : Icons.circle_outlined,
                        size: 8,
                        color: color,
                      ),
                      const SizedBox(width: 3),
                      Text(e.key, style: _labelStyle.copyWith(color: color)),
                    ],
                  );
                }).toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }

  // ── Table builders ───────────────────────────────────────────────────────

  Widget _buildOspfTable(Map<String, dynamic> ospf) {
    final neighbors   = ospf['neighbors']   as int? ?? 0;
    final adjacencies = ospf['adjacencies'] as int? ?? 0;
    final interfaces  = (ospf['interfaces'] as List?)?.cast<Map<String, dynamic>>() ?? [];

    // If we have per-interface data, show that instead of just totals
    if (interfaces.isNotEmpty) {
      return _buildTable(
        headers: const ['Interface', 'Neighbors', 'Adjacencies'],
        columnWidths: const {
          0: FlexColumnWidth(2),
          1: FlexColumnWidth(1),
          2: FlexColumnWidth(1),
        },
        rows: interfaces.map((iface) {
          final ifaceName = iface['interface'] as String? ?? '?';
          final ifaceNeighbors = iface['neighbors'] as int? ?? 0;
          final ifaceAdjacencies = iface['adjacencies'] as int? ?? 0;

          return [
            _tableCell(ifaceName, mono: true),
            _tableCell(
              '$ifaceNeighbors',
              color: ifaceNeighbors > 0 ? _okColor : _greyColor,
              bold: true,
            ),
            _tableCell(
              '$ifaceAdjacencies',
              color: ifaceAdjacencies > 0 ? _okColor : _warnColor,
              bold: true,
            ),
          ];
        }).toList()
          // Add a summary row at the end
          ..add([
            _tableCell('Total', bold: true),
            _tableCell('$neighbors', bold: true),
            _tableCell('$adjacencies', bold: true),
          ]),
      );
    }

    // Fallback to old format if no per-interface data
    return _buildTable(
      headers: const ['Metric', 'Value'],
      columnWidths: const {0: FlexColumnWidth(2), 1: FlexColumnWidth(1)},
      rows: [
        [
          _tableCell('Neighbors'),
          _tableCell(
            '$neighbors',
            color: neighbors > 0 ? _okColor : _warnColor,
            bold: true,
          ),
        ],
        [
          _tableCell('Adjacencies'),
          _tableCell(
            '$adjacencies',
            color: adjacencies > 0 ? _okColor : _errorColor,
            bold: true,
          ),
        ],
      ],
    );
  }

  Widget _buildBgpTable(List<Map<String, dynamic>> peers) {
    return _buildTable(
      headers: const ['Neighbor', 'AS', 'AFI / VRF', 'Uptime', 'Pfx Adv'],
      columnWidths: const {
        0: FlexColumnWidth(2.5),
        1: FlexColumnWidth(1.5),
        2: FlexColumnWidth(2.5),
        3: FlexColumnWidth(1.5),
        4: FlexColumnWidth(1.2),
      },
      rows: peers.map((peer) {
        final neighbor = peer['neighbor'] as String? ?? '?';
        final afi      = peer['afi']      as String? ?? '';
        final vrf      = peer['vrf']      as String? ?? 'default';
        final safi     = peer['safi']     as String? ?? '';
        final peerAs   = peer['peer_as']  as String? ?? '';
        final uptime   = (peer['uptime_seconds'] as num?)?.toDouble();
        final prefixes = peer['prefixes_advertised'] as int?;

        String afiLabel = afi;
        if (safi.isNotEmpty && safi != 'unicast') afiLabel += '/$safi';
        if (vrf != 'default') afiLabel += '\n($vrf)';

        return [
          _tableCell(neighbor, mono: true),
          _tableCell(peerAs.isNotEmpty ? 'AS$peerAs' : '—'),
          _tableCell(afiLabel),
          _tableCell(_formatUptime(uptime), color: _uptimeColor(uptime), bold: true),
          _tableCell(prefixes != null ? '$prefixes' : '—', color: _okColor, bold: true),
        ];
      }).toList(),
    );
  }

  Widget _buildRoutesTable(List<Map<String, dynamic>> routes) {
    // Sort: ipv4 before ipv6, then by vrf name
    final sorted = List<Map<String, dynamic>>.from(routes)
      ..sort((a, b) {
        final afiCmp = (a['afi'] as String? ?? '').compareTo(b['afi'] as String? ?? '');
        if (afiCmp != 0) return afiCmp;
        return (a['vrf'] as String? ?? '').compareTo(b['vrf'] as String? ?? '');
      });

    return _buildTable(
      headers: const ['AFI', 'VRF', 'Total', 'FIB'],
      columnWidths: const {
        0: FlexColumnWidth(1.2),
        1: FlexColumnWidth(2),
        2: FlexColumnWidth(1),
        3: FlexColumnWidth(1),
      },
      rows: sorted.map((r) {
        final afi   = r['afi']   as String? ?? '';
        final vrf   = r['vrf']   as String? ?? 'default';
        final total = r['total'] as int?;
        final fib   = r['fib']   as int?;
        final color = _routeColor(total, fib);

        return [
          _tableCell(afi),
          _tableCell(vrf),
          _tableCell(total != null ? '$total' : '—', color: Colors.black54),
          _tableCell(fib != null ? '$fib' : '—', color: color, bold: true),
        ];
      }).toList(),
    );
  }

  /// Generic table builder with a styled header row and alternating row shading.
  Widget _buildTable({
    required List<String> headers,
    required Map<int, TableColumnWidth> columnWidths,
    required List<List<Widget>> rows,
  }) {
    return Table(
      columnWidths: columnWidths,
      border: TableBorder.all(
        color: const Color(0xFFBBDEFB),
        width: 0.5,
        borderRadius: BorderRadius.circular(4),
      ),
      defaultVerticalAlignment: TableCellVerticalAlignment.middle,
      children: [
        // Header row
        TableRow(
          decoration: const BoxDecoration(color: _tableHeaderBg),
          children: headers
              .map((h) => Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                    child: Text(h, style: _tableHeaderStyle),
                  ))
              .toList(),
        ),
        // Data rows
        for (int i = 0; i < rows.length; i++)
          TableRow(
            decoration: BoxDecoration(
              color: i.isOdd ? const Color(0xFFF5F5F5) : Colors.white,
            ),
            children: rows[i],
          ),
      ],
    );
  }

  Widget _tableCell(String text, {Color? color, bool bold = false, bool mono = false}) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
      child: Text(
        text,
        style: _tableCellStyle.copyWith(
          color: color,
          fontWeight: bold ? FontWeight.bold : FontWeight.normal,
          fontFamily: mono ? 'monospace' : null,
        ),
      ),
    );
  }

  // ── Helper builders ──────────────────────────────────────────────────────

  Widget _sectionHeader(String label, IconData icon) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 12, color: _headerColor),
        const SizedBox(width: 4),
        Text(label, style: _sectionStyle),
      ],
    );
  }

  String _fmtTimestamp(String iso) {
    try {
      final dt = DateTime.parse(iso).toLocal();
      final h  = dt.hour.toString().padLeft(2, '0');
      final m  = dt.minute.toString().padLeft(2, '0');
      return '${dt.day}/${dt.month} $h:$m';
    } catch (_) {
      return iso;
    }
  }
}
