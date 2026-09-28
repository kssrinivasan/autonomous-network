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
import '../models/network_node.dart';

import '../screens/full_screen_panel_view.dart';
import '../models/panel_type.dart';

class AnomalyPanel extends StatelessWidget {
  final bool isFullScreen;

  const AnomalyPanel({super.key, this.isFullScreen = false});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: BoxDecoration(
            color: Colors.red.shade50,
            borderRadius: const BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Center(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.warning_amber_rounded, size: 18, color: Colors.red.shade700),
                      const SizedBox(width: 8),
                      Text(
                        'Top Anomalies',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: Colors.red.shade900,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              IconButton(
                icon: Icon(
                  isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                  color: Colors.red.shade900,
                ),
                tooltip: isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  if (isFullScreen) {
                    Navigator.of(context).pop();
                  } else {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (context) => const FullScreenPanelView(
                          panelType: PanelType.anomaly,
                        ),
                      ),
                    );
                  }
                },
              ),
            ],
          ),
        ),
        Expanded(
          child: Consumer<Appstate>(
            builder: (context, appState, child) {
              final topology = appState.topology;
              
              // Build list of anomalies from topology nodes with MSE data
              final List<Map<String, dynamic>> anomalies = [];
              
              for (var node in topology.nodes) {
                // Calculate average score from all 3 models for router
                double? routerAvgScore;
                int scoreCount = 0;
                double scoreSum = 0.0;
                
                if (node.stgnnScore != null) {
                  scoreSum += node.stgnnScore!;
                  scoreCount++;
                }
                if (node.dgatScore != null) {
                  scoreSum += node.dgatScore!;
                  scoreCount++;
                }
                if (node.hetgnnScore != null) {
                  scoreSum += node.hetgnnScore!;
                  scoreCount++;
                }
                
                if (scoreCount > 0) {
                  routerAvgScore = scoreSum / scoreCount;
                }
                
                // Add router-level anomaly if average score exceeds threshold
                // Threshold: 3.0 matches Mahalanobis distance (3 sigma) from GNN serve.py
                if (routerAvgScore != null && routerAvgScore > 3.0) {
                  anomalies.add({
                    'node_id': node.id,
                    'name': node.name,
                    'node_type': 'Router',
                    'anomaly_score': routerAvgScore,
                    'stgnn_score': node.stgnnScore,
                    'dgat_score': node.dgatScore,
                    'hetgnn_score': node.hetgnnScore,
                    'root_cause': node.routerRCA?.toString() ?? 'No root cause analysis available',
                    'timestamp': node.embeddingTimestamp,
                  });
                }
                
                // Add interface-level anomalies if they exist
                if (node.interfaceMSEs != null) {
                  node.interfaceMSEs!.forEach((interfaceId, scores) {
                    // Calculate average score from all 3 models for interface
                    double interfaceAvgScore = 0.0;
                    int interfaceScoreCount = 0;
                    
                    final stgnn = scores['stgnn_score'];
                    final dgat = scores['dgat_score'];
                    final hetgnn = scores['hetgnn_score'];
                    
                    if (stgnn != null) {
                      interfaceAvgScore += stgnn;
                      interfaceScoreCount++;
                    }
                    if (dgat != null) {
                      interfaceAvgScore += dgat;
                      interfaceScoreCount++;
                    }
                    if (hetgnn != null) {
                      interfaceAvgScore += hetgnn;
                      interfaceScoreCount++;
                    }
                    
                    if (interfaceScoreCount > 0) {
                      interfaceAvgScore /= interfaceScoreCount;
                    }
                    
                    // Threshold: 3.0 matches Mahalanobis distance (3 sigma) from GNN serve.py
                    if (interfaceAvgScore > 3.0) {
                      anomalies.add({
                        'node_id': interfaceId,
                        'name': '${node.name} - Interface',
                        'node_type': 'Interface',
                        'anomaly_score': interfaceAvgScore,
                        'stgnn_score': stgnn,
                        'dgat_score': dgat,
                        'hetgnn_score': hetgnn,
                        'root_cause': 'Interface anomaly detected',
                        'router_id': node.id,
                        'router_name': node.name,
                      });
                    }
                  });
                }
              }
              
              // Sort anomalies by score (highest first)
              anomalies.sort((a, b) {
                final scoreA = a['anomaly_score'] as double? ?? 0.0;
                final scoreB = b['anomaly_score'] as double? ?? 0.0;
                return scoreB.compareTo(scoreA);
              });
              
              if (anomalies.isEmpty) {
                return const Center(
                  child: Padding(
                    padding: EdgeInsets.all(16.0),
                    child: Text('No anomalies detected. All nodes are operating normally.', textAlign: TextAlign.center),
                  ),
                );
              }

              return ListView.builder(
                itemCount: anomalies.length,
                itemBuilder: (context, index) {
                  final anomaly = anomalies[index];
                  final score = anomaly['anomaly_score'] as double? ?? 0.0;
                  final rank = index + 1;
                  
                  return Card(
                    margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    elevation: score > 5.0 ? 2 : 0,
                    shape: RoundedRectangleBorder(
                      side: BorderSide(
                        color: score > 5.0 ? Colors.red.shade300 : Colors.orange.shade200,
                        width: score > 5.0 ? 2 : 1,
                      ),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: ExpansionTile(
                      leading: Stack(
                        alignment: Alignment.center,
                        children: [
                          CircleAvatar(
                            backgroundColor: score > 5.0 ? Colors.red : Colors.orange,
                            radius: 22,
                            child: Text(
                              score.toStringAsFixed(2),
                              style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
                            ),
                          ),
                          Positioned(
                            top: 0,
                            right: 0,
                            child: Container(
                              padding: const EdgeInsets.all(2),
                              decoration: BoxDecoration(
                                color: Colors.blue.shade700,
                                shape: BoxShape.circle,
                              ),
                              child: Text(
                                '#$rank',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 8,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                      title: Text(
                        anomaly['name'] ?? 'Unknown Node',
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      ),
                      subtitle: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(anomaly['node_type'] ?? ''),
                          if (anomaly['timestamp'] != null)
                            Text(
                              'Updated: ${anomaly['timestamp']}',
                              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                color: Colors.grey.shade600,
                                fontSize: 10,
                              ),
                            ),
                        ],
                      ),
                      children: [
                        Padding(
                          padding: const EdgeInsets.all(16.0),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  const Text('MSE Score: ', style: TextStyle(fontWeight: FontWeight.bold)),
                                  Text(
                                    score.toStringAsFixed(2),
                                    style: TextStyle(
                                      color: score > 5.0 ? Colors.red : Colors.orange,
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                    decoration: BoxDecoration(
                                      color: score > 5.0 ? Colors.red.shade100 : Colors.orange.shade100,
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    child: Text(
                                      score > 5.0 ? 'HIGH' : 'MODERATE',
                                      style: TextStyle(
                                        fontSize: 10,
                                        fontWeight: FontWeight.bold,
                                        color: score > 5.0 ? Colors.red.shade900 : Colors.orange.shade900,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 12),
                              const Text('Root Cause Analysis:', style: TextStyle(fontWeight: FontWeight.bold)),
                              const SizedBox(height: 8),
                              Text(
                                anomaly['root_cause']?.toString() ?? 'No explanation available.',
                                style: Theme.of(context).textTheme.bodyMedium,
                              ),
                              const SizedBox(height: 16),
                              Align(
                                alignment: Alignment.centerRight,
                                child: TextButton.icon(
                                  onPressed: () {
                                    final nodeId = anomaly['router_id'] ?? anomaly['node_id'];
                                    appState.getNodeDetails(nodeId);
                                  },
                                  icon: const Icon(Icons.info_outline, size: 16),
                                  label: const Text('View Node Details'),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  );
                },
              );
            },
          ),
        ),
      ],
    );
  }
}
