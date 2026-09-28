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
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/metric_entry.dart';
import '../../models/network_node.dart';
import '../topology/node_details_dialog.dart';

class NodePerformanceWidget extends StatelessWidget {
  final String nodeId;
  final List<MetricEntry> metrics;
  final bool showCpuMetrics;
  final bool showNetworkMetrics;

  const NodePerformanceWidget({
    super.key,
    required this.nodeId,
    required this.metrics,
    required this.showCpuMetrics,
    required this.showNetworkMetrics,
  });

  @override
  Widget build(BuildContext context) {
    // Get the latest metric entry if available
    final latestMetric = metrics.isNotEmpty ? metrics.first : null;
    
    return Card(
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
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Node header - more compact with details button
            Row(
              children: [
                Icon(
                  Icons.speed,
                  color: Color(0xFF1976D2),
                  size: 16,
                ),
                SizedBox(width: 4),
                Expanded(
                  child: Text(
                    'Performance',
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0D47A1),
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                // Details button
                InkWell(
                  onTap: () => _showNodeDetails(context, latestMetric),
                  child: Container(
                    padding: EdgeInsets.all(2),
                    decoration: BoxDecoration(
                      color: Color(0xFFE3F2FD),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Icon(
                      Icons.info_outline,
                      color: Color(0xFF1976D2),
                      size: 14,
                    ),
                  ),
                ),
              ],
            ),
            
            // Timestamp
            Text(
              'Updated: ${_formatTimestamp(latestMetric?.timestamp)}',
              style: TextStyle(
                fontSize: 10,
                color: Colors.grey[600],
                fontStyle: FontStyle.italic,
              ),
            ),
            
            Divider(height: 12),
            
            // Scrollable content area for metrics
            // Use Flexible with FlexFit.loose instead of Expanded to allow the column to size itself
            Flexible(
              fit: FlexFit.loose,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // CPU metrics - more compact
                    if (showCpuMetrics && latestMetric != null)
                      _buildCompactCpuMetrics(latestMetric),
                    
                    // Network metrics - more compact
                    if (showNetworkMetrics && latestMetric != null)
                      _buildCompactNetworkMetrics(latestMetric),
                    
                    // Show message if no metrics available
                    if (latestMetric == null)
                      Center(
                        child: Text(
                          'No metrics',
                          style: TextStyle(
                            fontSize: 12,
                            color: Colors.grey[700],
                            fontStyle: FontStyle.italic,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
  
  Widget _buildCompactCpuMetrics(MetricEntry metric) {
    final cpuPercent = metric.cpu['cpu_percent'] ?? 0.0;
    
    return Column(
      mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.memory, size: 12, color: Color(0xFF0D47A1)),
            SizedBox(width: 4),
            Text(
              'CPU:',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.bold,
              ),
            ),
            SizedBox(width: 4),
            Container(
              width: 40,
              alignment: Alignment.center,
              padding: EdgeInsets.symmetric(vertical: 2, horizontal: 4),
              decoration: BoxDecoration(
                color: _getCpuColor(cpuPercent).withOpacity(0.2),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                '${cpuPercent.toStringAsFixed(1)}%',
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  color: _getCpuColor(cpuPercent),
                ),
              ),
            ),
          ],
        ),
        SizedBox(height: 2),
        LinearProgressIndicator(
          value: cpuPercent / 100,
          backgroundColor: Colors.grey[200],
          valueColor: AlwaysStoppedAnimation<Color>(
            _getCpuColor(cpuPercent),
          ),
          minHeight: 4,
        ),
        SizedBox(height: 8),
      ],
    );
  }
  
  Widget _buildCompactNetworkMetrics(MetricEntry metric) {
    if (metric.interfaces.isEmpty) {
      return Text(
        'No network interfaces',
        style: TextStyle(
          fontSize: 10,
          fontStyle: FontStyle.italic,
          color: Colors.grey[600],
        ),
      );
    }
    
    return Column(
      mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.network_wifi, size: 12, color: Color(0xFF0D47A1)),
            SizedBox(width: 4),
            Text(
              'Network Interfaces',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        SizedBox(height: 4),
        
        // Show all interfaces in a compact format
        ...metric.interfaces.entries.map((entry) {
          final interfaceName = entry.key;
          final interfaceData = entry.value;
          
          // Extract network metrics
          final sentThroughput = interfaceData['byte_sent_throughput'] ?? 0.0;
          final recvThroughput = interfaceData['byte_recv_throughput'] ?? 0.0;
          
          return Padding(
            padding: const EdgeInsets.only(bottom: 4.0),
            child: Column(
              mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Interface name
                Text(
                  interfaceName,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                
                // Network throughput in a compact format
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.upload, size: 10, color: Colors.blue),
                        SizedBox(width: 2),
                        Text(
                          _formatNetworkSpeed(sentThroughput),
                          style: TextStyle(fontSize: 10),
                        ),
                      ],
                    ),
                    Row(
                      children: [
                        Icon(Icons.download, size: 10, color: Colors.green),
                        SizedBox(width: 2),
                        Text(
                          _formatNetworkSpeed(recvThroughput),
                          style: TextStyle(fontSize: 10),
                        ),
                      ],
                    ),
                  ],
                ),
              ],
            ),
          );
        }).toList(),
      ],
    );
  }
  
  // Show node details dialog
  void _showNodeDetails(BuildContext context, MetricEntry? metricEntry) {
    if (metricEntry == null) return;
    
    // Create a NetworkNode from the metric entry
    final node = NetworkNode(
      id: nodeId,
      name: metricEntry.hostname,
      type: NodeType.CE, // Fallback type
      properties: {
        'kind': 'ComputeInstance',
        'ip': '', // We don't have IP in the metric entry
        'status': 'running', // Assume running since we have metrics
        'cpu': metricEntry.cpu,
        'interfaces': metricEntry.interfaces,
        'timestamp': metricEntry.timestamp,
      },
    );
    
    // Use appstate to get node details
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.getNodeDetails(nodeId);
    
    // Show dialog with loading state
    showDialog(
      context: context,
      builder: (BuildContext context) => NodeDetailsDialog(
        node: node,
      ),
    );
  }
  
  // Helper methods
  String _formatTimestamp(int? timestamp) {
    if (timestamp == null) return 'N/A';
    
    final date = DateTime.fromMillisecondsSinceEpoch(timestamp * 1000);
    return '${date.hour}:${date.minute.toString().padLeft(2, '0')}:${date.second.toString().padLeft(2, '0')}';
  }
  
  Color _getCpuColor(double cpuPercent) {
    if (cpuPercent < 50) {
      return Colors.green;
    } else if (cpuPercent < 80) {
      return Colors.orange;
    } else {
      return Colors.red;
    }
  }
  
  String _formatNetworkSpeed(double bytesPerSecond) {
    if (bytesPerSecond < 1024) {
      return '${bytesPerSecond.toStringAsFixed(1)} B/s';
    } else if (bytesPerSecond < 1024 * 1024) {
      return '${(bytesPerSecond / 1024).toStringAsFixed(1)} KB/s';
    } else {
      return '${(bytesPerSecond / (1024 * 1024)).toStringAsFixed(1)} MB/s';
    }
  }
  
  String _formatBytes(dynamic bytes) {
    if (bytes is! num) return '0 B';
    
    final double numBytes = bytes.toDouble();
    if (numBytes < 1024) {
      return '${numBytes.toStringAsFixed(1)} B';
    } else if (numBytes < 1024 * 1024) {
      return '${(numBytes / 1024).toStringAsFixed(1)} KB';
    } else if (numBytes < 1024 * 1024 * 1024) {
      return '${(numBytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    } else {
      return '${(numBytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
    }
  }
}
