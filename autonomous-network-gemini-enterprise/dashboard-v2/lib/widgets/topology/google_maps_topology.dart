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
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/network_node.dart';
import '../../utils/node_visuals.dart';
import 'node_details_dialog.dart';
import 'dart:ui' as ui;
import 'dart:async';
import 'dart:typed_data';

class GoogleMapsTopologyWidget extends StatefulWidget {
  final NetworkTopology topology;

  const GoogleMapsTopologyWidget({
    super.key,
    required this.topology,
  });

  @override
  State<GoogleMapsTopologyWidget> createState() => _GoogleMapsTopologyWidgetState();
}

class _GoogleMapsTopologyWidgetState extends State<GoogleMapsTopologyWidget> {
  GoogleMapController? _mapController;
  Set<Marker> _markers = {};
  Set<Polyline> _polylines = {};
  MapType _currentMapType = MapType.normal;

  // UK center coordinates
  static const LatLng _ukCenter = LatLng(54.5, -2.5);
  
  @override
  void initState() {
    super.initState();
    _createMarkersAndPolylines();
  }

  @override
  void didUpdateWidget(GoogleMapsTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.topology != widget.topology) {
      _createMarkersAndPolylines();
    }
  }

  Future<void> _createMarkersAndPolylines() async {
    final Set<Marker> markers = {};
    final Set<Polyline> polylines = {};

    // Use topology nodes directly
    final nodes = widget.topology.nodes;
    final connections = widget.topology.connections;

    // Create markers for each node
    for (final node in nodes) {
      // Assign location based on node properties
      LatLng? position = _getNodePosition(node);

      // Skip nodes with no valid location
      if (position == null) {
        continue;
      }

      final String statusText = node.properties['status']?.toString() ?? 'unknown';
      final BitmapDescriptor markerIcon = await _createNodeMarkerIcon(node);

      markers.add(
        Marker(
          markerId: MarkerId(node.id),
          position: position,
          infoWindow: InfoWindow(
            title: node.name,
            snippet: '${node.properties['role'] ?? 'Router'} - $statusText',
          ),
          icon: markerIcon,
          anchor: const Offset(0.5, 0.5), // Center the marker over coordinate
          onTap: () => _showNodeDetails(node),
        ),
      );
    }

    // Create polylines for connections
    for (final connection in connections) {
      final sourceNode = nodes.where((n) => n.id == connection.sourceId).firstOrNull;
      final targetNode = nodes.where((n) => n.id == connection.targetId).firstOrNull;

      if (sourceNode != null && targetNode != null) {
        final LatLng? sourcePos = _getNodePosition(sourceNode);
        final LatLng? targetPos = _getNodePosition(targetNode);

        if (sourcePos != null && targetPos != null) {
          polylines.add(
            Polyline(
              polylineId: PolylineId('${connection.sourceId}-${connection.targetId}'),
              points: [sourcePos, targetPos],
              color: const Color(0xFF1976D2),
              width: 3,
              geodesic: true,
            ),
          );
        }
      }
    }

    if (mounted) {
      setState(() {
        _markers = markers;
        _polylines = polylines;
      });
      
      // Auto-zoom to fit all markers if map is ready
      if (_mapController != null && markers.isNotEmpty) {
        // Add a small delay to ensure the map is fully rendered
        Future.delayed(const Duration(milliseconds: 500), _zoomToFit);
      }
    }
  }

  void _zoomToFit() {
    if (_mapController == null || _markers.isEmpty) return;

    double minLat = 90.0;
    double minLng = 180.0;
    double maxLat = -90.0;
    double maxLng = -180.0;

    for (final marker in _markers) {
      if (marker.position.latitude < minLat) minLat = marker.position.latitude;
      if (marker.position.latitude > maxLat) maxLat = marker.position.latitude;
      if (marker.position.longitude < minLng) minLng = marker.position.longitude;
      if (marker.position.longitude > maxLng) maxLng = marker.position.longitude;
    }

    if (minLat > maxLat) return;

    _mapController!.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(
          southwest: LatLng(minLat, minLng),
          northeast: LatLng(maxLat, maxLng),
        ),
        100.0, // padding
      ),
    );
  }

  LatLng? _getNodePosition(NetworkNode node) {
    // Try to get location from properties
    if (node.properties.containsKey('location') && node.properties['location'] != null) {
      final location = node.properties['location'];
      if (location is Map && location.containsKey('latitude') && location.containsKey('longitude')) {
        try {
          final lat = _parseDouble(location['latitude']);
          final lng = _parseDouble(location['longitude']);
          if (lat != null && lng != null) {
            return LatLng(lat, lng);
          }
        } catch (e) {
          print('Error parsing location for node ${node.id}: $e');
        }
      }
    }
    
    // No valid location found
    return null;
  }
  
  double? _parseDouble(dynamic value) {
    if (value is double) return value;
    if (value is int) return value.toDouble();
    if (value is String) return double.tryParse(value);
    return null;
  }

  /// Creates a custom marker icon matching the style of the logical topology view.
  /// Uses the same color scheme and visual indicators (status dot, anomaly glow).
  Future<BitmapDescriptor> _createNodeMarkerIcon(NetworkNode node) async {
    final String statusText = node.properties['status']?.toString() ?? 'unknown';
    final Color statusColor = NetworkNode.getStatusColor(node.properties['status']);
    final Color nodeColor = getNodeColor(node);

    final ui.PictureRecorder recorder = ui.PictureRecorder();
    final Canvas canvas = Canvas(recorder);

    const double markerSize = 50.0;
    const double textHeight = 50.0;
    const double width = 180.0;
    const double height = markerSize + textHeight + 20.0;

    // Center the marker horizontally
    final double centerX = width / 2;
    final double markerY = 10.0;

    // Draw anomaly glow if needed
    if (node.isAnomaly) {
      final Paint glowPaint = Paint()
        ..color = Colors.red.withValues(alpha: 0.3)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 10);
      canvas.drawCircle(
        Offset(centerX, markerY + markerSize / 2),
        markerSize / 2 + 8,
        glowPaint,
      );

      // Red border for anomalies
      final Paint borderPaint = Paint()
        ..color = Colors.red
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3;
      canvas.drawCircle(
        Offset(centerX, markerY + markerSize / 2),
        markerSize / 2 + 4,
        borderPaint,
      );
    }

    // Draw main circle (using node type color from logical topology)
    final Paint mainPaint = Paint()..color = nodeColor;
    canvas.drawCircle(
      Offset(centerX, markerY + markerSize / 2),
      markerSize / 2,
      mainPaint,
    );

    // Draw white inner circle
    final Paint whitePaint = Paint()..color = Colors.white;
    canvas.drawCircle(
      Offset(centerX, markerY + markerSize / 2),
      markerSize / 2 - 5,
      whitePaint,
    );

    // Draw node type indicator icon in the center
    final IconData icon = getNodeIcon(node);
    final textPainter = TextPainter(
      text: TextSpan(
        text: String.fromCharCode(icon.codePoint),
        style: TextStyle(
          fontSize: 24,
          fontFamily: icon.fontFamily,
          color: nodeColor,
        ),
      ),
      textDirection: TextDirection.ltr,
    );
    textPainter.layout();
    textPainter.paint(
      canvas,
      Offset(
        centerX - textPainter.width / 2,
        markerY + markerSize / 2 - textPainter.height / 2,
      ),
    );

    // Draw status indicator circle (bottom-right of main circle)
    final Paint statusBgPaint = Paint()..color = Colors.white;
    canvas.drawCircle(
      Offset(centerX + markerSize / 2 - 6, markerY + markerSize - 6),
      8,
      statusBgPaint,
    );

    final Paint statusPaint = Paint()..color = statusColor;
    canvas.drawCircle(
      Offset(centerX + markerSize / 2 - 6, markerY + markerSize - 6),
      6,
      statusPaint,
    );

    // Draw text label below the marker
    final double textY = markerY + markerSize + 8;

    // Node name
    final TextSpan nameSpan = TextSpan(
      text: node.name,
      style: TextStyle(
        color: node.isAnomaly ? Colors.red : Colors.black87,
        fontSize: 13,
        fontWeight: FontWeight.bold,
      ),
    );
    final TextPainter namePainter = TextPainter(
      text: nameSpan,
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    );
    namePainter.layout(minWidth: 0, maxWidth: width);

    // Status text
    final TextSpan statusSpan = TextSpan(
      text: statusText,
      style: TextStyle(
        color: node.isAnomaly ? Colors.red : Colors.black54,
        fontSize: 11,
        fontWeight: FontWeight.normal,
      ),
    );
    final TextPainter statusPainter = TextPainter(
      text: statusSpan,
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    );
    statusPainter.layout(minWidth: 0, maxWidth: width);

    // Background for text
    final double maxTextWidth = namePainter.width > statusPainter.width
        ? namePainter.width
        : statusPainter.width;
    final Rect textBgRect = Rect.fromLTWH(
      centerX - maxTextWidth / 2 - 8,
      textY - 4,
      maxTextWidth + 16,
      namePainter.height + statusPainter.height + 10,
    );
    final RRect textBgRRect = RRect.fromRectAndRadius(
      textBgRect,
      const Radius.circular(6),
    );

    // Shadow for text background
    canvas.drawShadow(
      Path()..addRRect(textBgRRect),
      Colors.black,
      4.0,
      true,
    );

    // White background
    final Paint bgPaint = Paint()..color = Colors.white;
    canvas.drawRRect(textBgRRect, bgPaint);

    // Paint text
    namePainter.paint(canvas, Offset(centerX - namePainter.width / 2, textY));
    statusPainter.paint(
      canvas,
      Offset(centerX - statusPainter.width / 2, textY + namePainter.height + 4),
    );

    // Convert to image
    final ui.Picture picture = recorder.endRecording();
    final ui.Image image = await picture.toImage(width.toInt(), height.toInt());
    final ByteData? bytes = await image.toByteData(format: ui.ImageByteFormat.png);

    return BitmapDescriptor.bytes(bytes!.buffer.asUint8List());
  }

  void _showNodeDetails(NetworkNode node) {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.getNodeDetails(node.id);

    showDialog(
      context: context,
      builder: (BuildContext context) => NodeDetailsDialog(node: node),
    );
  }

  void _onMapCreated(GoogleMapController controller) {
    _mapController = controller;
  }

  void _toggleMapType() {
    setState(() {
      _currentMapType = _currentMapType == MapType.normal
          ? MapType.satellite
          : _currentMapType == MapType.satellite
              ? MapType.hybrid
              : MapType.normal;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD),
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Center(
            child: Text(
              'Network Topology - UK Router Network',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: const Color(0xFF0D47A1),
                  ),
            ),
          ),
        ),
        Expanded(
          child: Stack(
                  children: [
                    GoogleMap(
                      onMapCreated: _onMapCreated,
                      initialCameraPosition: const CameraPosition(
                        target: _ukCenter,
                        zoom: 6.0,
                      ),
                      mapType: _currentMapType,
                      markers: _markers,
                      polylines: _polylines,
                      myLocationButtonEnabled: false,
                      zoomControlsEnabled: false,
                    ),
                    // Controls positioned in top-right corner
                    Positioned(
                      top: 16,
                      right: 16,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          // Logical view toggle
                          Container(
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
                            child: IconButton(
                              icon: const Icon(Icons.device_hub),
                              color: const Color(0xFF1976D2),
                              tooltip: 'Switch to Logical View',
                              onPressed: () {
                                final appState = Provider.of<Appstate>(context, listen: false);
                                appState.toggleTopologyView();
                              },
                            ),
                          ),
                          const SizedBox(height: 8),
                          // Map type toggle
                          Container(
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
                            child: IconButton(
                              icon: const Icon(Icons.layers),
                              color: const Color(0xFF1976D2),
                              tooltip: 'Toggle Map Type',
                              onPressed: _toggleMapType,
                            ),
                          ),
                        ],
                      ),
                    ),
                    // Legend positioned in bottom-left corner
                    Positioned(
                      bottom: 16,
                      left: 16,
                      child: Container(
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
                              'Status',
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 12,
                              ),
                            ),
                            const SizedBox(height: 4),
                            _buildLegendItem(Colors.green, 'Operational'),
                            _buildLegendItem(Colors.orange, 'Degraded'),
                            _buildLegendItem(Colors.red, 'Failed'),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
        ),
      ],
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

  @override
  void dispose() {
    _mapController?.dispose();
    super.dispose();
  }
}
