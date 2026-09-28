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

import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/network_node.dart';
import '../../models/vyos_infrastructure_info.dart';
import '../../utils/node_visuals.dart';
import 'node_details_dialog.dart';

class LogicalTopologyWidget extends StatefulWidget {
  final NetworkTopology topology;
  /// Node IDs that should be highlighted (e.g. from a VPN or traffic test
  /// selection in the side panel).  An empty set means no highlighting.
  final Set<String> highlightedNodeIds;

  const LogicalTopologyWidget({
    Key? key,
    required this.topology,
    this.highlightedNodeIds = const {},
  }) : super(key: key);

  @override
  State<LogicalTopologyWidget> createState() => _LogicalTopologyWidgetState();
}

class _LogicalTopologyWidgetState extends State<LogicalTopologyWidget>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  final TransformationController _transformationController = TransformationController();
  Map<String, Offset> _positions = {};
  Map<String, Offset> _velocities = {};
  String? _draggedNodeId;
  bool _isSettled = false;

  /// Whether the per-link throughput overlay is shown on the canvas.
  bool _showMetricOverlay = true;

  /// The connection ID of the link currently being hovered, or null.
  String? _hoveredLinkId;

  /// Screen-space position of the cursor (used to place the tooltip).
  Offset _cursorPosition = Offset.zero;

  /// Latest link metrics — kept in state so the hover tooltip can read them
  /// without needing a BuildContext rebuild.
  Map<String, Map<String, double>> _lastLinkMetrics = {};
  
  // Physics parameters
  final double kRepulsion = 10000.0;
  final double kSpring = 0.05;
  final double kDamping = 0.85;
  final double restLength = 100.0;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
       vsync: this,
       duration: const Duration(milliseconds: 16), // ~60 FPS
    )..addListener(() {
      _applyForces();
    });
    
    _initializePositions();
    _precomputeLayout();
  }
  
  @override
  void didUpdateWidget(LogicalTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.topology != oldWidget.topology) {
       // Only update if nodes have been added or removed
       final currentNodeIds = widget.topology.nodes.map((n) => n.id).toSet();
       final oldNodeIds = oldWidget.topology.nodes.map((n) => n.id).toSet();
       
       if (!currentNodeIds.containsAll(oldNodeIds) || !oldNodeIds.containsAll(currentNodeIds)) {
         // Nodes have changed - update positions for new nodes only
         _updatePositions();
         // Only re-settle if topology structure actually changed
         setState(() {
           _isSettled = false;
         });
         _precomputeLayout();
       }
       // If only connection or property changes, keep existing positions
    }
  }

  void _initializePositions() {
    final random = Random();
    _positions = {};
    _velocities = {};
    // Center cluster initially
    for (var node in widget.topology.nodes) {
      _positions[node.id] = Offset(
        random.nextDouble() * 400 + 100,
        random.nextDouble() * 400 + 100,
      );
      _velocities[node.id] = Offset.zero;
    }
  }
  
  void _updatePositions() {
    final random = Random();
    final currentNodeIds = widget.topology.nodes.map((n) => n.id).toSet();
    
    // Remove positions for deleted nodes
    _positions.removeWhere((id, _) => !currentNodeIds.contains(id));
    _velocities.removeWhere((id, _) => !currentNodeIds.contains(id));
    
    // Add positions for new nodes only
    for (var node in widget.topology.nodes) {
      if (!_positions.containsKey(node.id)) {
        _positions[node.id] = Offset(
          random.nextDouble() * 400 + 100,
          random.nextDouble() * 400 + 100,
        );
        _velocities[node.id] = Offset.zero;
      }
    }
  }

  Future<void> _precomputeLayout() async {
    // Run physics simulation silently for a number of iterations
    for (int i = 0; i < 300; i++) {
      _applyForcesWithoutSetState();
    }
    
    // Now show the settled layout and start the animation controller
    if (mounted) {
      setState(() {
        _isSettled = true;
      });
      // Start the continuous physics simulation for interactive dragging
      _controller.repeat();
    }
  }

  void _applyForcesWithoutSetState() {
    if (widget.topology.nodes.isEmpty) return;
    
    // Calculate repulsive forces between all nodes
    Map<String, Offset> forces = {};
    for (var node in widget.topology.nodes) {
       forces[node.id] = Offset.zero;
       
       // Repulsion from other nodes
       for (var other in widget.topology.nodes) {
          if (node.id != other.id) {
             final dx = _positions[node.id]!.dx - _positions[other.id]!.dx;
             final dy = _positions[node.id]!.dy - _positions[other.id]!.dy;
             final distSq = dx * dx + dy * dy;
             if (distSq > 0.1) {
                final dist = sqrt(distSq);
                final forceMag = kRepulsion / distSq;
                forces[node.id] = forces[node.id]! + Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             }
          }
       }
       
       // Attraction to center to keep graph on screen
       final dxCenter = 300 - _positions[node.id]!.dx;
       final dyCenter = 300 - _positions[node.id]!.dy;
       forces[node.id] = forces[node.id]! + Offset(dxCenter * 0.01, dyCenter * 0.01);
    }
    
    // Calculate attractive forces for edges (springs)
    for (var conn in widget.topology.connections) {
       if (_positions.containsKey(conn.sourceId) && _positions.containsKey(conn.targetId)) {
          final dx = _positions[conn.targetId]!.dx - _positions[conn.sourceId]!.dx;
          final dy = _positions[conn.targetId]!.dy - _positions[conn.sourceId]!.dy;
          final distSq = dx * dx + dy * dy;
          if (distSq > 0) {
             final dist = sqrt(distSq);
             // Hooke's Law: F = k * x
             final forceMag = kSpring * (dist - restLength);
             
             final force = Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             forces[conn.sourceId] = forces[conn.sourceId]! + force;
             forces[conn.targetId] = forces[conn.targetId]! - force; // Equal and opposite
          }
       }
    }
    
    // Update velocities and positions WITHOUT setState
    for (var node in widget.topology.nodes) {
       if (node.id == _draggedNodeId) {
          _velocities[node.id] = Offset.zero;
          continue;
       }
       _velocities[node.id] = (_velocities[node.id]! + forces[node.id]!) * kDamping;
       _positions[node.id] = _positions[node.id]! + _velocities[node.id]!;
    }
  }

  void _applyForces() {
    if (widget.topology.nodes.isEmpty) return;
    
    // Calculate repulsive forces between all nodes
    Map<String, Offset> forces = {};
    for (var node in widget.topology.nodes) {
       forces[node.id] = Offset.zero;
       
       // Repulsion from other nodes
       for (var other in widget.topology.nodes) {
          if (node.id != other.id) {
             final dx = _positions[node.id]!.dx - _positions[other.id]!.dx;
             final dy = _positions[node.id]!.dy - _positions[other.id]!.dy;
             final distSq = dx * dx + dy * dy;
             if (distSq > 0.1) {
                final dist = sqrt(distSq);
                final forceMag = kRepulsion / distSq;
                forces[node.id] = forces[node.id]! + Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             }
          }
       }
       
       // Attraction to center to keep graph on screen
       final dxCenter = 300 - _positions[node.id]!.dx;
       final dyCenter = 300 - _positions[node.id]!.dy;
       forces[node.id] = forces[node.id]! + Offset(dxCenter * 0.01, dyCenter * 0.01);
    }
    
    // Calculate attractive forces for edges (springs)
    for (var conn in widget.topology.connections) {
       if (_positions.containsKey(conn.sourceId) && _positions.containsKey(conn.targetId)) {
          final dx = _positions[conn.targetId]!.dx - _positions[conn.sourceId]!.dx;
          final dy = _positions[conn.targetId]!.dy - _positions[conn.sourceId]!.dy;
          final distSq = dx * dx + dy * dy;
          if (distSq > 0) {
             final dist = sqrt(distSq);
             // Hooke's Law: F = k * x
             final forceMag = kSpring * (dist - restLength);
             
             final force = Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             forces[conn.sourceId] = forces[conn.sourceId]! + force;
             forces[conn.targetId] = forces[conn.targetId]! - force; // Equal and opposite
          }
       }
    }
    
    // Update velocities and positions
    setState(() {
      for (var node in widget.topology.nodes) {
         if (node.id == _draggedNodeId) {
            _velocities[node.id] = Offset.zero;
            continue;
         }
         _velocities[node.id] = (_velocities[node.id]! + forces[node.id]!) * kDamping;
         _positions[node.id] = _positions[node.id]! + _velocities[node.id]!;
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    _transformationController.dispose();
    super.dispose();
  }

  // ── Link-metric overlay helpers ────────────────────────────────────────────

  /// Builds a map from connection ID → {txBps, rxBps} (bytes/s) sourced from
  /// the per-interface VyOS system metrics held in [Appstate.metrics].
  /// Only router-to-router links whose [sourceInterface] property is non-empty
  /// are included; device connections are skipped.
  Map<String, Map<String, double>> _buildLinkMetrics(Appstate appState) {
    final result = <String, Map<String, double>>{};
    for (final conn in widget.topology.connections) {
      // Only overlay router-to-router links (device edges have non-empty type).
      if ((conn.properties['type'] as String? ?? '').isNotEmpty) continue;

      // Use the router NAME (e.g. "ce1-hub") as the metrics key, not the
      // graph node ID (e.g. "router:ce1-hub").  The NetworkMetrics table
      // stores node_name = router_name (no "router:" prefix).
      final routerName = conn.properties['sourceRouterName'] as String? ?? '';
      final metricsKey = routerName.isNotEmpty ? routerName : conn.sourceId;
      final nodeMetricsList = appState.metrics.data[metricsKey];
      if (nodeMetricsList == null || nodeMetricsList.isEmpty) continue;
      final entry = nodeMetricsList.last;

      final srcIface = conn.properties['sourceInterface'] as String? ?? '';
      if (srcIface.isNotEmpty) {
        // ── Exact lookup ──────────────────────────────────────────────────
        // Strip any VLAN subinterface suffix so we always read the physical
        // interface counter (eth1.301 → eth1).  node_network_*_total for the
        // physical interface already includes all VLAN traffic on that wire,
        // so using the subinterface counter would be both inaccurate and
        // potentially double-counted if parent and child are both present.
        final physIface = srcIface.contains('.')
            ? srcIface.split('.').first
            : srcIface;
        final ifaceData = entry.interfaces[physIface];
        if (ifaceData == null) continue;
        final tx = (ifaceData['byte_sent_throughput'] as num?)?.toDouble() ?? 0.0;
        final rx = (ifaceData['byte_recv_throughput'] as num?)?.toDouble() ?? 0.0;
        // Link capacity in bps from PhysicalLink.bandwidth (may be null for
        // unlimited or unknown links).
        final capacityBps =
            conn.properties['linkBandwidthBps'] as double?;
        result[conn.id] = {
          'txBps': tx,
          'rxBps': rx,
          if (capacityBps != null) 'capacityBps': capacityBps,
        };
      }
      // No sourceInterface → skip.  Summing all interfaces on the router and
      // assigning the total to every connection from that node is misleading
      // (all links from p1 would show identical numbers).  Gray is honest.
    }
    return result;
  }

  @override
  Widget build(BuildContext context) {
    // Show loading indicator while computing layout
    if (!_isSettled) {
      return Container(
        decoration: const BoxDecoration(
          color: Colors.white,
        ),
        child: const Center(
          child: CircularProgressIndicator(),
        ),
      );
    }
    
    return Container(
       decoration: const BoxDecoration(
         color: Colors.white, // White background
       ),
       clipBehavior: Clip.hardEdge, // Ensure content stays within bounds
       child: Stack(
         children: [
            // MouseRegion wraps the entire canvas to track cursor position and
            // detect hover over link midpoints for the detail tooltip.
            MouseRegion(
              onHover: (event) {
                if (!_showMetricOverlay || _lastLinkMetrics.isEmpty) return;
                final localPos = event.localPosition;

                // Transform screen position → canvas coordinates by inverting
                // the InteractiveViewer's current transform matrix.
                final matrix = _transformationController.value;
                final inverted = Matrix4.inverted(matrix);
                final canvasPos = MatrixUtils.transformPoint(inverted, localPos);

                // Hit-test: find the router link whose midpoint is within 20px
                // of the cursor (in canvas space).
                String? hitId;
                for (final conn in widget.topology.connections) {
                  if ((conn.properties['type'] as String? ?? '').isNotEmpty) continue;
                  if (!_lastLinkMetrics.containsKey(conn.id)) continue;
                  final p1 = _positions[conn.sourceId];
                  final p2 = _positions[conn.targetId];
                  if (p1 == null || p2 == null) continue;
                  final mid = Offset((p1.dx + p2.dx) / 2, (p1.dy + p2.dy) / 2);
                  if ((mid - canvasPos).distance < 20) {
                    hitId = conn.id;
                    break;
                  }
                }

                if (hitId != _hoveredLinkId) {
                  setState(() {
                    _hoveredLinkId = hitId;
                    _cursorPosition = localPos;
                  });
                } else if (hitId != null) {
                  // Update cursor position while staying on the same link.
                  setState(() => _cursorPosition = localPos);
                }
              },
              onExit: (_) {
                if (_hoveredLinkId != null) {
                  setState(() => _hoveredLinkId = null);
                }
              },
              child: InteractiveViewer(
              transformationController: _transformationController,
              constrained: false, // Important to allow panning effectively
              boundaryMargin: const EdgeInsets.all(4000), // Huge margins to let users drag far out
              minScale: 0.1,
              maxScale: 5.0,
              clipBehavior: Clip.hardEdge, // Clip content to prevent overflow
              child: SizedBox(
                   // Define a large canvas area
                   width: 5000, 
                   height: 5000,
                   child: Stack(
                      clipBehavior: Clip.none, // Also allow Stack children to draw freely if they slightly exceed
                      children: [
                        // Draw lines FIRST so they appear behind nodes.
                        // Consumer<Appstate> provides the latest metrics so the
                        // overlay repaints whenever metrics arrive (every ~20 s).
                        Positioned.fill(
                          child: Consumer<Appstate>(
                            builder: (context, appState, _) {
                              final metrics = _buildLinkMetrics(appState);
                              // Keep a copy so the hover tooltip can read it.
                              _lastLinkMetrics = metrics;
                              return CustomPaint(
                                painter: TopologyPainter(
                                  topology: widget.topology,
                                  positions: _positions,
                                  linkMetrics: metrics,
                                  overlayEnabled: _showMetricOverlay,
                                ),
                              );
                            },
                          ),
                        ),
                        // Then draw the nodes on top
                        ...widget.topology.nodes.map((node) {
                           final pos = _positions[node.id] ?? Offset.zero;
                           return Positioned(
                              left: pos.dx - 20, // Center icon (40x40 roughly)
                              top: pos.dy - 20,
                              child: GestureDetector(
                                 onPanStart: (details) {
                                    setState(() {
                                       _draggedNodeId = node.id;
                                       _velocities[node.id] = Offset.zero;
                                    });
                                 },
                                 onPanUpdate: (details) {
                                    setState(() {
                                       final scale = _transformationController.value.getMaxScaleOnAxis();
                                       _positions[node.id] = _positions[node.id]! + (details.delta / scale);
                                       _velocities[node.id] = Offset.zero;
                                    });
                                 },
                                 onPanEnd: (details) {
                                    setState(() {
                                       _draggedNodeId = null;
                                    });
                                 },
                                 onPanCancel: () {
                                    setState(() {
                                       _draggedNodeId = null;
                                    });
                                 },
                                 onTap: () {
                                    showDialog(
                                       context: context,
                                       builder: (context) => NodeDetailsDialog(node: node),
                                    );
                                 },
                                 child: Column(
                                    children: [
                                       Stack(
                                         clipBehavior: Clip.none,
                                         children: [
                                           // Highlight ring for selected VPN / traffic test nodes
                                           if (widget.highlightedNodeIds.isNotEmpty &&
                                               widget.highlightedNodeIds.contains(node.id))
                                             Positioned(
                                               left: -6,
                                               top: -6,
                                               child: Container(
                                                 width: 52,
                                                 height: 52,
                                                 decoration: BoxDecoration(
                                                   shape: BoxShape.circle,
                                                   border: Border.all(
                                                     color: Colors.blue,
                                                     width: 3,
                                                   ),
                                                   boxShadow: [
                                                     BoxShadow(
                                                       color: Colors.blue.withOpacity(0.5),
                                                       blurRadius: 10,
                                                       spreadRadius: 3,
                                                     ),
                                                   ],
                                                 ),
                                               ),
                                             ),
                                           Container(
                                             decoration: BoxDecoration(
                                                shape: BoxShape.circle,
                                                // Dim non-highlighted nodes when a selection is active
                                                color: (widget.highlightedNodeIds.isNotEmpty &&
                                                        !widget.highlightedNodeIds.contains(node.id))
                                                    ? Colors.black.withOpacity(0.08)
                                                    : null,
                                                boxShadow: node.isAnomaly
                                                  ? [
                                                      BoxShadow(
                                                        color: Colors.red.withOpacity(0.8),
                                                        blurRadius: 15,
                                                        spreadRadius: 5,
                                                      ),
                                                    ]
                                                  : [],
                                             ),
                                             child: Opacity(
                                               opacity: (widget.highlightedNodeIds.isNotEmpty &&
                                                         !widget.highlightedNodeIds.contains(node.id))
                                                   ? 0.4
                                                   : 1.0,
                                               child: _buildNodeIcon(node),
                                             ),
                                           ),
                                           Positioned(
                                             right: -2,
                                             bottom: -2,
                                             child: Container(
                                               width: 14,
                                               height: 14,
                                               decoration: BoxDecoration(
                                                 color: NetworkNode.getStatusColor(node.properties['status']?.toString()),
                                                 shape: BoxShape.circle,
                                                 border: Border.all(color: Colors.white, width: 2),
                                               ),
                                             ),
                                           ),
                                         ],
                                       ),
                                       const SizedBox(height: 4),
                                       RichText(
                                          textAlign: TextAlign.center,
                                          text: TextSpan(
                                             children: [
                                                TextSpan(
                                                   text: '${node.name}\n',
                                                   style: TextStyle(
                                                      color: node.isAnomaly ? Colors.red : Colors.black87,
                                                      fontSize: 11,
                                                      fontWeight: FontWeight.bold,
                                                   ),
                                                ),
                                                TextSpan(
                                                   text: '${node.properties['status'] ?? 'Unknown'}',
                                                   style: TextStyle(
                                                      color: node.isAnomaly ? Colors.red : Colors.black54,
                                                      fontSize: 9,
                                                      fontWeight: FontWeight.normal,
                                                   ),
                                                ),
                                             ],
                                          ),
                                       )
                                    ],
                                 ),
                              ),
                           );
                        }).toList(),
                      ],
                   ),
                ),
              ),
            ), // end MouseRegion
            // ── Hover tooltip ──────────────────────────────────────────────
            if (_hoveredLinkId != null && _showMetricOverlay)
              _buildLinkTooltip(_hoveredLinkId!, _cursorPosition),
            // Metric overlay toggle button — top-right corner of the canvas.
            Positioned(
              top: 12,
              right: 12,
              child: Tooltip(
                message: _showMetricOverlay
                    ? 'Hide link throughput overlay'
                    : 'Show link throughput overlay',
                child: Material(
                  color: _showMetricOverlay
                      ? const Color(0xFF0D47A1)
                      : Colors.white,
                  borderRadius: BorderRadius.circular(6),
                  elevation: 2,
                  child: InkWell(
                    borderRadius: BorderRadius.circular(6),
                    onTap: () =>
                        setState(() => _showMetricOverlay = !_showMetricOverlay),
                    child: Padding(
                      padding: const EdgeInsets.all(7),
                      child: Icon(
                        Icons.analytics_outlined,
                        size: 18,
                        color: _showMetricOverlay
                            ? Colors.white
                            : Colors.black54,
                      ),
                    ),
                  ),
                ),
              ),
            ),
            // Legend positioned in bottom-left corner
            Positioned(
              bottom: 16,
              left: 16,
              child: Consumer<Appstate>(
                builder: (context, appstate, _) {
                  final infras = appstate.vyosInfrastructures;
                  return Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(8),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withOpacity(0.2),
                          blurRadius: 4,
                          offset: const Offset(0, 2),
                        ),
                      ],
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Text(
                          'Infrastructure Status',
                          style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                          ),
                        ),
                        const SizedBox(height: 4),
                        if (infras.isEmpty)
                          const Text(
                            'None deployed',
                            style: TextStyle(
                              fontSize: 11,
                              color: Colors.black45,
                              fontStyle: FontStyle.italic,
                            ),
                          )
                        else
                          ...infras.map((infra) => _buildInfraItem(infra)),
                      ],
                    ),
                  );
                },
              ),
            ),
         ],
       ),
    );
  }
  /// Returns the appropriate icon widget for a given node type.
  /// P routers use the Cisco router SVG, PE routers use the provider edge SVG,
  /// CE routers use the customer edge SVG, and devices use a CircleAvatar.
  Widget _buildNodeIcon(NetworkNode node) {
    switch (node.type) {
      case NodeType.P:
        return SvgPicture.asset(
          'assets/images/cisco_router.svg',
          width: 40,
          height: 40,
        );
      case NodeType.PA:
        return SvgPicture.asset(
          'assets/images/cisco_router.svg',
          width: 40,
          height: 40,
        );
      case NodeType.PE:
        return SvgPicture.asset(
          'assets/images/provider_edge_router.svg',
          width: 40,
          height: 40,
        );
      case NodeType.AR:
        return SvgPicture.asset(
          'assets/images/cisco_router.svg',
          width: 40,
          height: 40,
        );
      case NodeType.CE:
        return SvgPicture.asset(
          'assets/images/customer_edge_router.svg',
          width: 40,
          height: 40,
        );
      case NodeType.RR:
        return SvgPicture.asset(
          'assets/images/route_reflector.svg',
          width: 40,
          height: 40,
        );
      case NodeType.Device:
        return CircleAvatar(
          backgroundColor: getNodeColor(node),
          radius: 20,
          child: Icon(
            getNodeIcon(node),
            color: Colors.white,
            size: 24,
          ),
        );
    }
  }

  // ── Link hover tooltip ────────────────────────────────────────────────────

  /// Builds a floating detail card near the cursor showing capacity, ↑ sent,
  /// and ↓ recv for the hovered link.
  Widget _buildLinkTooltip(String linkId, Offset cursorPos) {
    final metrics = _lastLinkMetrics[linkId];
    if (metrics == null) return const SizedBox.shrink();

    final txBitps = (metrics['txBps'] ?? 0) * 8;
    final rxBitps = (metrics['rxBps'] ?? 0) * 8;
    final capacityBps = metrics['capacityBps'];

    String bpsLabel(double bps) {
      if (bps >= 1e9) return '${(bps / 1e9).toStringAsFixed(1)} Gbps';
      if (bps >= 1e6) return '${(bps / 1e6).toStringAsFixed(1)} Mbps';
      if (bps >= 1e3) return '${(bps / 1e3).toStringAsFixed(0)} Kbps';
      return '${bps.toStringAsFixed(0)} bps';
    }

    const labelStyle = TextStyle(fontSize: 11, color: Colors.black54);
    const valueStyle = TextStyle(
      fontSize: 11,
      fontWeight: FontWeight.w600,
      color: Colors.black87,
    );

    final rows = <Widget>[
      if (capacityBps != null && capacityBps > 0)
        _tooltipRow('Capacity', bpsLabel(capacityBps), labelStyle, valueStyle),
      _tooltipRow('↑ Sent', bpsLabel(txBitps), labelStyle, valueStyle),
      _tooltipRow('↓ Recv', bpsLabel(rxBitps), labelStyle, valueStyle),
    ];

    // Position the card 12px below and to the right of the cursor, clamped
    // so it doesn't overflow the right/bottom edge of the widget.
    const cardWidth = 160.0;
    const cardHeight = 80.0;
    const offset = 14.0;

    return Positioned(
      left: cursorPos.dx + offset,
      top: cursorPos.dy + offset,
      child: IgnorePointer(
        child: Material(
          elevation: 4,
          borderRadius: BorderRadius.circular(8),
          color: Colors.white,
          child: Container(
            width: cardWidth,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.blue.shade100),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: rows,
            ),
          ),
        ),
      ),
    );
  }

  Widget _tooltipRow(
    String label,
    String value,
    TextStyle labelStyle,
    TextStyle valueStyle,
  ) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: labelStyle),
          Text(value, style: valueStyle),
        ],
      ),
    );
  }

  Widget _buildLegendItem(Color color, String label) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 12,
            height: 12,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(fontSize: 11),
          ),
        ],
      ),
    );
  }

  Widget _buildInfraItem(VyosInfrastructureInfo infra) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Infrastructure CR row
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 12,
                height: 12,
                decoration: BoxDecoration(
                  color: infra.phaseColor,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 6),
              Text(
                infra.name,
                style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w500,
                ),
              ),
              const SizedBox(width: 4),
              Text(
                infra.phase,
                style: TextStyle(
                  fontSize: 10,
                  color: infra.phaseColor,
                ),
              ),
            ],
          ),
          if (infra.routerCount > 0 || infra.networkCount > 0 || infra.deviceCount > 0)
            Padding(
              padding: const EdgeInsets.only(left: 18, top: 1),
              child: Text(
                infra.resourceSummary,
                style: const TextStyle(
                  fontSize: 10,
                  color: Colors.black45,
                ),
              ),
            ),
          // Underlay CR rows (same indentation as the infrastructure row)
          ...infra.underlays.map((underlay) => Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    color: underlay.phaseColor,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  underlay.name,
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(width: 4),
                Text(
                  underlay.phase,
                  style: TextStyle(
                    fontSize: 10,
                    color: underlay.phaseColor,
                  ),
                ),
              ],
            ),
          )),
        ],
      ),
    );
  }
}

class TopologyPainter extends CustomPainter {
  final NetworkTopology topology;
  final Map<String, Offset> positions;

  /// Per-link throughput data used by the metric overlay.
  /// Key: connection ID → {txBps, rxBps} in bytes/s from the source-side
  /// interface metrics already held in [Appstate.metrics].
  final Map<String, Map<String, double>> linkMetrics;

  /// When true the painter colours links by throughput and draws midpoint labels.
  final bool overlayEnabled;

  TopologyPainter({
    required this.topology,
    required this.positions,
    this.linkMetrics = const {},
    this.overlayEnabled = false,
  });

  // ── Colour encoding ────────────────────────────────────────────────────────

  /// Line colour based on utilisation % of link capacity.
  ///
  /// Uses the peak direction (max of txBitps, rxBitps) so that a bidirectional
  /// 90 Mbps test on a 100 Mbps link correctly shows 90 % utilisation (orange)
  /// rather than 180 % (which would be nonsensical for a full-duplex link).
  ///
  ///   < 60 %  → green   (healthy)
  ///   60–80 % → amber   (moderate)
  ///   80–90 % → orange  (high)
  ///   ≥ 90 %  → red     (critical)
  ///
  /// When capacity is unknown (null / 0) the colour falls back to a simple
  /// presence/absence check: any traffic → green, no traffic → grey.
  Color _linkColor(double txBitps, double rxBitps, double? capacityBps) {
    final peakBitps = max(txBitps, rxBitps);
    if (capacityBps != null && capacityBps > 0) {
      final pct = peakBitps / capacityBps * 100;
      if (pct < 60) return Colors.green.withValues(alpha: 0.85);
      if (pct < 80) return Colors.amber.withValues(alpha: 0.90);
      if (pct < 90) return Colors.orange.withValues(alpha: 0.90);
      return Colors.red.shade600.withValues(alpha: 0.9);
    }
    // Fallback when capacity is unknown
    if (peakBitps <= 0) return Colors.blueGrey.withValues(alpha: 0.4);
    return Colors.green.withValues(alpha: 0.80);
  }

  /// Compact midpoint label — shows only utilisation % when capacity is known,
  /// or the peak direction rate when capacity is unknown.
  ///
  /// Full detail (↑tx, ↓rx, capacity) is shown in the hover tooltip instead.
  String _linkLabel(double txBitps, double rxBitps, double? capacityBps) {
    if (capacityBps != null && capacityBps > 0) {
      final peakBitps = max(txBitps, rxBitps);
      final pct = peakBitps / capacityBps * 100;
      return '${pct.toStringAsFixed(0)}%';
    }
    // No capacity configured — show the peak direction rate.
    final peakBitps = max(txBitps, rxBitps);
    if (peakBitps <= 0) return '';
    return _bpsLabel(peakBitps);
  }

  /// Compact human-readable bit rate string (fallback label).
  String _bpsLabel(double bps) {
    if (bps >= 1e9) return '${(bps / 1e9).toStringAsFixed(1)} G';
    if (bps >= 1e6) return '${(bps / 1e6).toStringAsFixed(1)} M';
    if (bps >= 1e3) return '${(bps / 1e3).toStringAsFixed(0)} K';
    return '${bps.toStringAsFixed(0)} bps';
  }

  @override
  void paint(Canvas canvas, Size size) {
    for (var conn in topology.connections) {
      final p1 = positions[conn.sourceId];
      final p2 = positions[conn.targetId];
      if (p1 == null || p2 == null) continue;

      final metrics = overlayEnabled ? linkMetrics[conn.id] : null;
      // Router-to-router links have an empty 'type' property; device connections
      // use 'device_to_interface' / 'device_to_router' and are not overlaid.
      final isRouterLink =
          (conn.properties['type'] as String? ?? '').isEmpty;

      final Color lineColor;
      final double strokeWidth;

      if (metrics != null && isRouterLink) {
        // txBps / rxBps are in bytes/sec (from node_network_*_bytes_total via
        // ALIGN_RATE).  Multiply by 8 to convert to bits/sec so that
        // utilisation % and _bpsLabel suffixes (K/M/G) correctly represent
        // Kbps / Mbps / Gbps.
        final txBitps = (metrics['txBps'] ?? 0) * 8;
        final rxBitps = (metrics['rxBps'] ?? 0) * 8;
        final capacityBps = metrics['capacityBps'];
        lineColor = _linkColor(txBitps, rxBitps, capacityBps);
        strokeWidth = max(txBitps, rxBitps) > 0 ? 3.0 : 1.5;
      } else {
        lineColor = Colors.blueGrey.withValues(alpha: 0.5);
        strokeWidth = 2.0;
      }

      canvas.drawLine(
        p1,
        p2,
        Paint()
          ..color = lineColor
          ..strokeWidth = strokeWidth
          ..style = PaintingStyle.stroke,
      );

      // Utilisation label at the midpoint, router links only.
      if (metrics != null && isRouterLink) {
        final txBitps = (metrics['txBps'] ?? 0) * 8;
        final rxBitps = (metrics['rxBps'] ?? 0) * 8;
        final capacityBps = metrics['capacityBps'];
        final mid = Offset((p1.dx + p2.dx) / 2, (p1.dy + p2.dy) / 2);
        _drawLinkLabel(canvas, _linkLabel(txBitps, rxBitps, capacityBps), mid, lineColor);
      }
    }
  }

  void _drawLinkLabel(Canvas canvas, String text, Offset center, Color color) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: 9.0,
          fontWeight: FontWeight.w700,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();

    // White pill background so the label is readable over the canvas.
    final bgRect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: center,
        width: tp.width + 6,
        height: tp.height + 3,
      ),
      const Radius.circular(3),
    );
    canvas.drawRRect(
      bgRect,
      Paint()..color = Colors.white.withValues(alpha: 0.82),
    );

    tp.paint(canvas, center.translate(-tp.width / 2, -tp.height / 2));
  }

  @override
  bool shouldRepaint(covariant TopologyPainter oldDelegate) {
    return oldDelegate.positions != positions ||
        oldDelegate.linkMetrics != linkMetrics ||
        oldDelegate.overlayEnabled != overlayEnabled;
  }
}
