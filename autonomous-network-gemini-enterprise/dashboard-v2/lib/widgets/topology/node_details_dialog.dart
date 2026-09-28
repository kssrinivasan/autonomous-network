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
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/network_node.dart';
import '../../utils/APIService.dart';
import '../../utils/node_visuals.dart';
import '../performance/node_performance_card.dart';
import '../performance/routing_metrics_card.dart';
import '../performance/router_interface_performance_card.dart';

class NodeDetailsDialog extends StatefulWidget {
  final NetworkNode node;

  const NodeDetailsDialog({
    super.key,
    required this.node,
  });

  @override
  State<NodeDetailsDialog> createState() => _NodeDetailsDialogState();
}

class _NodeDetailsDialogState extends State<NodeDetailsDialog> {
  bool _isLoading = true;
  String _markdownSummary = '';
  String? _error;
  final APIService _apiService = APIService();
  Map<String, dynamic>? _embeddingsData;
  Map<String, dynamic>? _routingMetrics;

  @override
  void initState() {
    super.initState();
    _fetchNodeDetails();
    _fetchEmbeddings();
    _fetchRoutingMetrics();
  }

  Future<void> _fetchEmbeddings() async {
    try {
      // Only fetch embeddings for routers
      bool isRouter = widget.node.properties['kind'] == 'Router' ||
                     widget.node.properties['kind'] == 'PhysicalRouter';
      
      if (isRouter) {
        final embeddings = await _apiService.fetchNodeEmbeddings(widget.node.id);
        setState(() {
          _embeddingsData = embeddings;
        });
      }
    } catch (e) {
      print('Error fetching embeddings: $e');
      // Don't set error state, just continue without embeddings
    }
  }

  Future<void> _fetchRoutingMetrics() async {
    try {
      final bool isRouter =
          widget.node.properties['kind'] == 'Router' ||
          widget.node.properties['kind'] == 'PhysicalRouter';
      if (!isRouter) return;

      final metrics =
          await _apiService.fetchRoutingMetrics(widget.node.name);
      if (mounted) {
        setState(() {
          _routingMetrics = metrics.isEmpty ? null : metrics;
        });
      }
    } catch (e) {
      print('Error fetching routing metrics: $e');
    }
  }

  Future<void> _fetchNodeDetails() async {
    try {
      // check if node is a router based on properties
      bool isRouter = widget.node.properties['kind'] == 'Router' ||
                     widget.node.properties['kind'] == 'PhysicalRouter';
      bool isDevice = widget.node.properties['kind'] == 'Device';
      
      if (isRouter) {
        final details = await _apiService.fetchRouterDetails(widget.node.id);
        final summary = _formatRouterDetails(details);
        setState(() {
          _isLoading = false;
          _markdownSummary = summary;
        });
      } else if (isDevice) {
        final details = await _apiService.fetchDeviceDetails(widget.node.id);
        final summary = _formatDeviceDetails(details);
        setState(() {
          _isLoading = false;
          _markdownSummary = summary;
        });
      } else {
        // Fallback for other node types
        final summary = await _apiService.getNodeDetails(widget.node.id);
        setState(() {
          _isLoading = false;
          _markdownSummary = summary;
        });
      }
    } catch (e) {
      setState(() {
        _isLoading = false;
        _error = e.toString();
      });
    }
  }

  String _formatDeviceDetails(Map<String, dynamic> data) {
    final buffer = StringBuffer();
    
    buffer.writeln('### ${data['name'] ?? 'Device Details'}');
    buffer.writeln('');
    buffer.writeln('**ID**: `${data['id']}`');
    buffer.writeln('**Status**: ${data['status'] ?? 'Unknown'}');
    buffer.writeln('**Network**: ${data['network_name'] ?? 'Unknown'}');
    buffer.writeln('**IP Address**: ${data['ip_address'] ?? 'Unknown'}');
    buffer.writeln('**Gateway**: ${data['gateway'] ?? 'Unknown'}');
    
    if (data['vlan'] != null) {
      buffer.writeln('**VLAN**: ${data['vlan']}');
    }
    
    if (data['router_id'] != null) {
      buffer.writeln('**Connected Router**: ${data['router_id']}');
    }
    
    if (data['config'] != null && (data['config'] as Map).isNotEmpty) {
      buffer.writeln('');
      buffer.writeln('### Configuration');
      buffer.writeln('```json');
      buffer.writeln(data['config'].toString());
      buffer.writeln('```');
    }
    
    return buffer.toString();
  }
  
  String _formatRouterDetails(Map<String, dynamic> data) {
    final buffer = StringBuffer();
    
    buffer.writeln('### ${data['name'] ?? 'Router Details'}');
    buffer.writeln('');
    buffer.writeln('**ID**: `${data['id']}`');
    buffer.writeln('**Role**: ${data['role'] ?? 'Unknown'}');
    buffer.writeln('**Status**: ${data['status'] ?? 'Unknown'}');
    buffer.writeln('**Vendor/Model**: ${data['vendor'] ?? 'Unknown'} / ${data['model'] ?? 'Unknown'}');
    
    if (data['location'] != null) {
      final loc = data['location'];
      buffer.writeln('**Location**: ${loc['city'] ?? 'Unknown'}');
      if (loc['latitude'] != null && loc['longitude'] != null) {
        buffer.writeln('**Coordinates**: ${loc['latitude']}, ${loc['longitude']}');
      }
    }
    
    buffer.writeln('');
    buffer.writeln('### Interfaces');
    buffer.writeln('');
    
    if (data['interfaces'] != null && (data['interfaces'] as List).isNotEmpty) {
      buffer.writeln('| Name | IP Address | Status | Speed |');
      buffer.writeln('|---|---|---|---|');
      
      for (var iface in data['interfaces']) {
        final name = iface['name'] ?? '-';
        final ip = iface['ip_address'] ?? '-';
        final status = iface['status'] ?? '-';
        final speed = iface['speed'] ?? '-';
        
        // Add status icon if possible, or just text
        String statusIcon = '';
        if (status.toString().toLowerCase() == 'up') {
          statusIcon = '🟢 ';
        } else if (status.toString().toLowerCase() == 'down') {
          statusIcon = '🔴 ';
        }
        
        buffer.writeln('| $name | $ip | $statusIcon$status | $speed |');
      }
    } else {
      buffer.writeln('No interfaces found.');
    }
    
    if (data['config'] != null && (data['config'] as Map).isNotEmpty) {
      buffer.writeln('');
      buffer.writeln('### Configuration');
      buffer.writeln('```json');
      // Pretty print JSON config if possible, or just dump it
      try {
        // We can't easily pretty print JSON in Dart without import 'dart:convert';
        // But markdown json block is decent enough
        buffer.writeln(data['config'].toString());
      } catch (_) {
        buffer.writeln('Error displaying config');
      }
      buffer.writeln('```');
    }
    
    return buffer.toString();
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
      ),
      elevation: 0,
      backgroundColor: Colors.transparent,
      child: _buildContent(context),
    );
  }

  Widget _buildContent(BuildContext context) {
    // Get the screen size to set a maximum height for the dialog
    final screenSize = MediaQuery.of(context).size;
    final maxHeight = screenSize.height * 0.8; // 80% of screen height
    
    return Container(
      constraints: BoxConstraints(
        maxHeight: maxHeight,
        maxWidth: screenSize.width * 0.9, // 90% of screen width
      ),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        shape: BoxShape.rectangle,
        borderRadius: BorderRadius.circular(16),
        boxShadow: const [
          BoxShadow(
            color: Colors.black26,
            blurRadius: 10.0,
            offset: Offset(0.0, 10.0),
          ),
        ],
      ),
      child: _isLoading
          ? const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(),
                  SizedBox(height: 16),
                  Text('Loading node details...'),
                ],
              ),
            )
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.error_outline, color: Colors.red, size: 48),
                      const SizedBox(height: 16),
                      Text('Error: $_error'),
                      const SizedBox(height: 16),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF0D47A1),
                          foregroundColor: Colors.white,
                        ),
                        onPressed: () {
                          Navigator.of(context).pop();
                        },
                        child: const Text('Close'),
                      ),
                    ],
                  ),
                )
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Header with node type icon and name
                    Row(
                      children: [
                        _buildDialogNodeIcon(widget.node),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                widget.node.name,
                                style: const TextStyle(
                                  fontSize: 20,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              Text(
                                widget.node.type.toString().split('.').last,
                                style: TextStyle(
                                  fontSize: 14,
                                  color: Colors.grey[600],
                                ),
                              ),
                              Row(
                                children: [
                                  SelectableText(
                                    'Id: ${widget.node.id}',
                                    style: TextStyle(
                                      fontSize: 14,
                                      color: Colors.grey[600],
                                    ),
                                  ),
                                  const SizedBox(width: 4),
                                  IconButton(
                                    icon: const Icon(Icons.copy, size: 16),
                                    tooltip: 'Copy ID',
                                    padding: EdgeInsets.zero,
                                    constraints: const BoxConstraints(),
                                    onPressed: () {
                                      Clipboard.setData(ClipboardData(text: widget.node.id));
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        const SnackBar(content: Text('Node ID copied to clipboard')),
                                      );
                                    },
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    
                    // Scrollable content area
                    // Use Flexible with FlexFit.loose instead of Expanded to allow the column to size itself
                    Flexible(
                      fit: FlexFit.loose,
                      child: SingleChildScrollView(
                        child: Column(
                          mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
                          children: [
                            // Performance Card - Using Consumer to automatically update when metrics change
                            Consumer<Appstate>(
                              builder: (context, appState, child) {
                                final nodeMetrics = appState.metrics.data[widget.node.id];
                                if (nodeMetrics != null && nodeMetrics.isNotEmpty) {
                                  return Container(
                                    margin: const EdgeInsets.symmetric(vertical: 8),
                                    child: StreamBuilder<void>(
                                      // This stream will rebuild whenever appState.metrics changes
                                      stream: Stream.periodic(const Duration(milliseconds: 100))
                                          .asyncMap((_) async => appState.metrics),
                                      builder: (context, snapshot) {
                                        // Get the latest metrics for this node
                                        final latestNodeMetrics = appState.metrics.data[widget.node.id] ?? [];
                                        return NodePerformanceWidget(
                                          metrics: latestNodeMetrics,
                                          nodeId: widget.node.id,
                                          showCpuMetrics: true,
                                          showNetworkMetrics: true,
                                        );
                                      },
                                    ),
                                  );
                                } else {
                                  return const SizedBox.shrink();
                                }
                              },
                            ),
                            // Underlay Network (Layer 2) metrics card — shown for P/PE routers
                            if (_routingMetrics != null)
                              Container(
                                margin: const EdgeInsets.symmetric(vertical: 8),
                                child: UnderlayMetricsCard(data: _routingMetrics!),
                              ),
                            // Historical interface performance panel — shown for routers
                            if (widget.node.properties['kind'] == 'Router' ||
                                widget.node.properties['kind'] == 'PhysicalRouter')
                              Container(
                                margin: const EdgeInsets.symmetric(vertical: 8),
                                child: RouterInterfacePerformanceCard(
                                  nodeId: widget.node.id,
                                  nodeName: widget.node.name,
                                ),
                              ),
                            // Embeddings Card (if available)
                            if (_embeddingsData != null && 
                                (_embeddingsData!['router_embedding'] != null || 
                                 (_embeddingsData!['interface_embeddings'] as List?)?.isNotEmpty == true))
                              Card(
                                elevation: 2,
                                margin: const EdgeInsets.all(4.0),
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(8.0),
                                  side: BorderSide(
                                    color: Color(0xFFFF6F00),
                                    width: 1.0,
                                  ),
                                ),
                                child: Padding(
                                  padding: const EdgeInsets.all(8.0),
                                  child: Column(
                                    mainAxisSize: MainAxisSize.min,
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        children: [
                                          Icon(
                                            Icons.analytics,
                                            color: Color(0xFFFF6F00),
                                            size: 16,
                                          ),
                                          SizedBox(width: 4),
                                          Text(
                                            'GNN Embeddings (MSE)',
                                            style: TextStyle(
                                              fontSize: 14,
                                              fontWeight: FontWeight.bold,
                                              color: Color(0xFFE65100),
                                            ),
                                          ),
                                        ],
                                      ),
                                      Divider(height: 12),
                                      _buildEmbeddingsContent(),
                                    ],
                                  ),
                                ),
                              ),
                            // Configuration Card
                            Card(
                              elevation: 2,
                              margin: const EdgeInsets.all(4.0),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(8.0),
                                side: BorderSide(
                                  color: Color(0xFF1976D2),
                                  width: 1.0,
                                ),
                              ),
                              child: Padding(
                                padding: const EdgeInsets.all(8.0),
                                child: Column(
                                  mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Icon(
                                          Icons.settings,
                                          color: Color(0xFF1976D2),
                                          size: 16,
                                        ),
                                        SizedBox(width: 4),
                                        Text(
                                          'Configuration',
                                          style: TextStyle(
                                            fontSize: 14,
                                            fontWeight: FontWeight.bold,
                                            color: Color(0xFF0D47A1),
                                          ),
                                        ),
                                      ],
                                    ),
                                    Divider(height: 12),
                                    MarkdownBody(
                                      data: _markdownSummary,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    
                    const SizedBox(height: 16),
                    
                    // Actions
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        
                        const SizedBox(width: 8),
                        ElevatedButton(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: const Color(0xFF0D47A1),
                            foregroundColor: Colors.white,
                          ),
                          onPressed: () {
                            Navigator.of(context).pop();
                          },
                          child: const Text('Close'),
                        ),
                      ],
                    ),
                  ],
                ),
    );
  }

  /// Returns the SVG icon for router node types, or a CircleAvatar for devices.
  Widget _buildDialogNodeIcon(NetworkNode node) {
    String? svgAsset;
    switch (node.type) {
      case NodeType.P:
        svgAsset = 'assets/images/cisco_router.svg';
        break;
      case NodeType.PA:
        svgAsset = 'assets/images/cisco_router.svg';
        break;
      case NodeType.PE:
        svgAsset = 'assets/images/provider_edge_router.svg';
        break;
      case NodeType.AR:
        svgAsset = 'assets/images/cisco_router.svg';
        break;
      case NodeType.CE:
        svgAsset = 'assets/images/customer_edge_router.svg';
        break;
      case NodeType.RR:
        svgAsset = 'assets/images/route_reflector.svg';
        break;
      case NodeType.Device:
        return CircleAvatar(
          backgroundColor: getNodeColor(node),
          radius: 22,
          child: Icon(
            getNodeIcon(node),
            color: Colors.white,
            size: 26,
          ),
        );
    }
    return SvgPicture.asset(
      svgAsset,
      width: 48,
      height: 48,
    );
  }

  Widget _buildEmbeddingsContent() {
    if (_embeddingsData == null) {
      return Text('No embeddings data available', style: TextStyle(fontSize: 12, color: Colors.grey));
    }

    final routerEmbedding = _embeddingsData!['router_embedding'];
    final interfaceEmbeddings = _embeddingsData!['interface_embeddings'] as List? ?? [];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Router embeddings (all 3 models)
        if (routerEmbedding != null) ...[
          Text(
            'Router Anomaly Scores:',
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.bold,
              color: Color(0xFFE65100),
            ),
          ),
          SizedBox(height: 4),
          Padding(
            padding: const EdgeInsets.only(left: 8.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _buildModelScore('STGNN', routerEmbedding['stgnn_score']),
                _buildModelScore('DGAT', routerEmbedding['dgat_score']),
                _buildModelScore('HetGNN', routerEmbedding['hetgnn_score']),
              ],
            ),
          ),
          SizedBox(height: 4),
          if (routerEmbedding['timestamp'] != null)
            Text(
              'Last updated: ${_formatTimestamp(routerEmbedding['timestamp'])}',
              style: TextStyle(fontSize: 11, color: Colors.grey[600]),
            ),
          if (routerEmbedding['stgnn_embedding'] != null)
            Text(
              'Embedding dim: ${(routerEmbedding['stgnn_embedding'] as List).length}',
              style: TextStyle(fontSize: 11, color: Colors.grey[600]),
            ),
        ],
        
        // Interface embeddings (all 3 models)
        if (interfaceEmbeddings.isNotEmpty) ...[
          SizedBox(height: 12),
          Text(
            'Interface Anomaly Scores:',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.bold,
              color: Color(0xFFE65100),
            ),
          ),
          SizedBox(height: 4),
          ...interfaceEmbeddings.map((iface) {
            return Padding(
              padding: const EdgeInsets.only(left: 8.0, top: 4.0, bottom: 4.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${iface['interface_name']}:',
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
                  ),
                  Padding(
                    padding: const EdgeInsets.only(left: 8.0, top: 2.0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _buildModelScore('STGNN', iface['stgnn_score'], fontSize: 10),
                        _buildModelScore('DGAT', iface['dgat_score'], fontSize: 10),
                        _buildModelScore('HetGNN', iface['hetgnn_score'], fontSize: 10),
                      ],
                    ),
                  ),
                ],
              ),
            );
          }).toList(),
        ],
        
        if (routerEmbedding == null && interfaceEmbeddings.isEmpty)
          Text(
            'No embeddings available for this node',
            style: TextStyle(fontSize: 12, color: Colors.grey),
          ),
      ],
    );
  }

  Widget _buildModelScore(String modelName, dynamic score, {double fontSize = 11}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 2.0),
      child: Row(
        children: [
          SizedBox(
            width: 60,
            child: Text(
              '$modelName:',
              style: TextStyle(fontSize: fontSize, color: Colors.grey[700]),
            ),
          ),
          Text(
            _formatMSE(score),
            style: TextStyle(
              fontSize: fontSize,
              fontWeight: FontWeight.w600,
              color: _getMSEColor(score),
            ),
          ),
        ],
      ),
    );
  }

  String _formatMSE(dynamic mse) {
    if (mse == null) return 'N/A';
    if (mse is num) {
      return mse.toStringAsFixed(6);
    }
    return mse.toString();
  }

  Color _getMSEColor(dynamic mse) {
    if (mse == null) return Colors.grey;
    
    double value = 0.0;
    if (mse is num) {
      value = mse.toDouble();
    } else {
      try {
        value = double.parse(mse.toString());
      } catch (e) {
        return Colors.grey;
      }
    }
    
    // Color coding based on MSE value
    // Lower MSE = better (green), higher MSE = worse (red)
    if (value < 0.01) {
      return Colors.green;
    } else if (value < 0.05) {
      return Colors.orange;
    } else {
      return Colors.red;
    }
  }

  String _formatTimestamp(String? timestamp) {
    if (timestamp == null) return 'Unknown';
    try {
      final dt = DateTime.parse(timestamp);
      final now = DateTime.now();
      final diff = now.difference(dt);
      
      if (diff.inMinutes < 1) {
        return 'Just now';
      } else if (diff.inHours < 1) {
        return '${diff.inMinutes}m ago';
      } else if (diff.inDays < 1) {
        return '${diff.inHours}h ago';
      } else {
        return '${diff.inDays}d ago';
      }
    } catch (e) {
      return timestamp;
    }
  }

}
