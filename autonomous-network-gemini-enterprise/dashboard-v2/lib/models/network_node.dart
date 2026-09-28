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

enum NodeType {
  P,
  PE,
  CE,
  RR,
  PA,
  AR,
  Device
}

class NetworkNode {
  final String id;
  final String name;
  final NodeType type;
  final Map<String, dynamic> properties;

  NetworkNode({
    required this.id,
    required this.name,
    required this.type,
    this.properties = const {},
    this.anomalyScore,
    this.isAnomaly = false,
    this.rootCause,
    this.stgnnScore,
    this.dgatScore,
    this.hetgnnScore,
    this.interfaceMSEs,
    this.routerRCA,
    this.embeddingTimestamp,
  });

  final double? anomalyScore;
  final bool isAnomaly;
  final String? rootCause;
  
  // Embeddings data - all 3 GNN models
  final double? stgnnScore;
  final double? dgatScore;
  final double? hetgnnScore;
  final Map<String, Map<String, double>>? interfaceMSEs; // interface_id -> {stgnn_score, dgat_score, hetgnn_score}
  final dynamic routerRCA; // Root Cause Analysis (can be JSON object)
  final String? embeddingTimestamp;
  
  // Computed property to check if router or any interface has high MSE (any of the 3 models)
  bool get hasHighMSE {
    const double threshold = 2.0;
    
    // Check router scores from any of the 3 models
    if (stgnnScore != null && stgnnScore! > threshold) return true;
    if (dgatScore != null && dgatScore! > threshold) return true;
    if (hetgnnScore != null && hetgnnScore! > threshold) return true;
    
    // Check interface scores from any of the 3 models
    if (interfaceMSEs != null) {
      for (var scores in interfaceMSEs!.values) {
        if ((scores['stgnn_score'] ?? 0.0) > threshold) return true;
        if ((scores['dgat_score'] ?? 0.0) > threshold) return true;
        if ((scores['hetgnn_score'] ?? 0.0) > threshold) return true;
      }
    }
    
    return false;
  }

  NetworkNode copyWith({
    String? id,
    String? name,
    NodeType? type,
    Map<String, dynamic>? properties,
    double? anomalyScore,
    bool? isAnomaly,
    String? rootCause,
    double? stgnnScore,
    double? dgatScore,
    double? hetgnnScore,
    Map<String, Map<String, double>>? interfaceMSEs,
    dynamic routerRCA,
    String? embeddingTimestamp,
  }) {
    return NetworkNode(
      id: id ?? this.id,
      name: name ?? this.name,
      type: type ?? this.type,
      properties: properties ?? this.properties,
      anomalyScore: anomalyScore ?? this.anomalyScore,
      isAnomaly: isAnomaly ?? this.isAnomaly,
      rootCause: rootCause ?? this.rootCause,
      stgnnScore: stgnnScore ?? this.stgnnScore,
      dgatScore: dgatScore ?? this.dgatScore,
      hetgnnScore: hetgnnScore ?? this.hetgnnScore,
      interfaceMSEs: interfaceMSEs ?? this.interfaceMSEs,
      routerRCA: routerRCA ?? this.routerRCA,
      embeddingTimestamp: embeddingTimestamp ?? this.embeddingTimestamp,
    );
  }
  
  // Get the appropriate color for a node status
  static Color getStatusColor(String? status) {
    if (status == null) {
      return Colors.grey; // Default color for unknown status
    }
    
    switch (status.toLowerCase()) {
      case 'failed':
      case 'error':
      case 'notfound':
      case 'notready':
        return Colors.red;
      case 'pending':
      case 'creating':
      case 'terminating':
      case 'updating':
      case 'configuring':
      case 'validating':
      case 'processing':
      case 'waiting':
      case 'deploying':
        return Colors.orange;
      case 'ready':
      case 'running':
      case 'succeeded':
        return Colors.green; // Green for Operational nodes
      case 'unknown':
      default:
        return Colors.grey; // Default color for unknown status
    }
  }
  
  // Map the kind from the server to a NodeType
  static NodeType mapKindToNodeType(String kind) {
    switch (kind) {
      case 'PE':
        return NodeType.PE;
      case 'CE':
        return NodeType.CE;
      case 'RR':
        return NodeType.RR;
      case 'PA':
        return NodeType.PA;
      case 'AR':
        return NodeType.AR;
      case 'P':
      default:
        return NodeType.P;
    }
  }
}

class NetworkConnection {
  final String id;
  final String sourceId;
  final String targetId;
  final String label;
  final Map<String, dynamic> properties;

  NetworkConnection({
    required this.id,
    required this.sourceId,
    required this.targetId,
    this.label = '',
    this.properties = const {},
  });
}

class NetworkTopology {
  final List<NetworkNode> nodes;
  final List<NetworkConnection> connections;

  NetworkTopology({
    required this.nodes,
    required this.connections,
  });
  
  // Create an empty topology with no nodes or connections
  NetworkTopology.empty()
      : nodes = [],
        connections = [];
  
  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    if (other is! NetworkTopology) return false;
    
    // Compare nodes and connections lengths
    if (nodes.length != other.nodes.length || 
        connections.length != other.connections.length) {
      // print('Topology comparison: Different lengths');
      return false;
    }
    
    // Create maps of nodes by ID for more efficient comparison
    final thisNodesMap = <String, NetworkNode>{};
    final otherNodesMap = <String, NetworkNode>{};
    
    for (var node in nodes) {
      thisNodesMap[node.id] = node;
    }
    
    for (var node in other.nodes) {
      otherNodesMap[node.id] = node;
    }
    
    // Check if both topologies have the same node IDs
    if (!thisNodesMap.keys.toSet().containsAll(otherNodesMap.keys) ||
        !otherNodesMap.keys.toSet().containsAll(thisNodesMap.keys)) {
      // print('Topology comparison: Different node IDs');
      return false;
    }
    
    // Compare each node by ID, name, type, and properties
    for (final id in thisNodesMap.keys) {
      final thisNode = thisNodesMap[id]!;
      final otherNode = otherNodesMap[id]!;
      
      if (thisNode.name != otherNode.name || thisNode.type != otherNode.type) {
        // print('Topology comparison: Node $id has different name or type');
        return false;
      }
      
      // Compare properties
      if (thisNode.properties.length != otherNode.properties.length) {
        // print('Topology comparison: Node $id has different property count');
        return false;
      }
      
      for (final key in thisNode.properties.keys) {
        if (!otherNode.properties.containsKey(key) ||
            thisNode.properties[key] != otherNode.properties[key]) {
          // print('Topology comparison: Node $id has different property $key');
          return false;
        }
      }
    }
    
    // Create maps of connections by source and target for more efficient comparison
    final thisConnectionsMap = <String, NetworkConnection>{};
    final otherConnectionsMap = <String, NetworkConnection>{};
    
    for (var conn in connections) {
      final key = '${conn.sourceId}-${conn.targetId}';
      thisConnectionsMap[key] = conn;
    }
    
    for (var conn in other.connections) {
      final key = '${conn.sourceId}-${conn.targetId}';
      otherConnectionsMap[key] = conn;
    }
    
    // Check if both topologies have the same connection keys
    if (!thisConnectionsMap.keys.toSet().containsAll(otherConnectionsMap.keys) ||
        !otherConnectionsMap.keys.toSet().containsAll(thisConnectionsMap.keys)) {
      // print('Topology comparison: Different connection pairs');
      return false;
    }
    
    // Compare each connection by source, target, label, and properties
    for (final key in thisConnectionsMap.keys) {
      final thisConn = thisConnectionsMap[key]!;
      final otherConn = otherConnectionsMap[key]!;
      
      if (thisConn.label != otherConn.label) {
        // print('Topology comparison: Connection $key has different label');
        return false;
      }
      
      // Compare properties
      if (thisConn.properties.length != otherConn.properties.length) {
        // print('Topology comparison: Connection $key has different property count');
        return false;
      }
      
      for (final propKey in thisConn.properties.keys) {
        if (!otherConn.properties.containsKey(propKey) ||
            thisConn.properties[propKey] != otherConn.properties[propKey]) {
          // print('Topology comparison: Connection $key has different property $propKey');
          return false;
        }
      }
    }
    
    return true;
  }
  
  @override
  int get hashCode {
    // Create a more robust hashCode that doesn't depend on the order of nodes and connections
    final nodesHash = nodes.fold(0, (hash, node) => hash ^ node.id.hashCode);
    final connectionsHash = connections.fold(0, (hash, conn) => 
        hash ^ '${conn.sourceId}-${conn.targetId}'.hashCode);
    return nodesHash ^ connectionsHash;
  }
}
