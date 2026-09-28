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

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:networkagent/models/agent.dart';
import 'package:networkagent/models/metrics.dart';
import 'package:networkagent/models/available_agent.dart';
import 'package:networkagent/models/network_descriptor.dart';
import 'package:networkagent/models/vpn_info.dart';
import 'package:networkagent/models/vyos_infrastructure_info.dart';
import 'package:networkagent/utils/environment_config.dart' as config;

class APIService{
  Map<String, String> getRequestHeaders = {
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'GET',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Map<String, String> postRequestHeaders = {
    'Content-type': 'application/json',
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'POST',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Future<List<Agent>> listAgents() async {
    List<Agent> agents = [];
    try {
      var agent_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/listagents');
      print('Fetching agents from: $agent_url');
      
      try {
        final http.Response response = await http.get(agent_url, headers: getRequestHeaders);
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                print('Agent data: $agentData');
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Successfully added agent: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                    print('Agent data that failed: $agentData');
                  }
                } else {
                  print('Agent data is not a Map: ${agentData.runtimeType}');
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to load agents: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error fetching agents: $e');
    }
    
    print('Returning ${agents.length} agents');
    return agents;
  }
  
  Future<Agent?> addAgent(String url) async {
    try {
      var addUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/addagent');
      print('Adding agent with URL: $url');
      print('Add endpoint: $addUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          addUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final Map<String, dynamic> agentData = jsonDecode(response.body);
            print('Agent data: $agentData');
            
            final agent = Agent.fromJson(agentData);
            print('Successfully added agent: ${agent.name}');
            return agent;
          } catch (e) {
            print('Error parsing agent data: $e');
          }
        } else {
          print('Failed to add agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error adding agent: $e');
    }
    
    return null;
  }

  Future<List<Agent>> deleteAgent(String url) async {
    List<Agent> agents = [];
    try {
      var deleteUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/deleteagent');
      print('Deleting agent with URL: $url');
      print('Delete endpoint: $deleteUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          deleteUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Updated agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Agent in updated list: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                  }
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to delete agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error deleting agent: $e');
    }
    
    print('Returning ${agents.length} agents after deletion');
    return agents;
  }

  Future<String> getNodeDetails(String nodeId) async {
    try {
      var node_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/node/$nodeId');
      print('Fetching node details from: $node_url');
      
      final http.Response response = await http.get(node_url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData.containsKey('summary')) {
          return decodedData['summary'];
        } else if (decodedData is String) {
          return decodedData;
        }
        else {
          throw Exception('Failed to parse node details summary');
        }
      } else {
        throw Exception('Failed to load node details');
      }
    } catch (e) {
      print('Error fetching node details: $e');
      rethrow;
    }
  }
  
  // Metrics API methods
  
  Future<Metrics> getAllLastMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/last');
      print('Fetching all last metrics from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all last metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all last metrics');
      }
    } catch (e) {
      print('Error fetching all last metrics: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getAllMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/all');
      print('Fetching all metrics from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all metrics');
      }
    } catch (e) {
      print('Error fetching all metrics: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getLastMetricsForId(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/last/$nodeId');
      print('Fetching last metrics for node $nodeId from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load last metrics for node $nodeId: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load last metrics for node $nodeId');
      }
    } catch (e) {
      print('Error fetching last metrics for node $nodeId: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getAllMetricsForId(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/all/$nodeId');
      print('Fetching all metrics for node $nodeId from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all metrics for node $nodeId: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all metrics for node $nodeId');
      }
    } catch (e) {
      print('Error fetching all metrics for node $nodeId: $e');
      rethrow;
    }
  }
  
  Future<bool> resetMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/reset');
      print('Resetting metrics at: $url');
      
      final http.Response response = await http.post(url, headers: postRequestHeaders);
      print('Reset metrics response status: ${response.statusCode}');
      print('Reset metrics response body: ${response.body}');
      
      if (response.statusCode == 200) {
        try {
          final dynamic decodedData = jsonDecode(response.body);
          print('Decoded reset metrics data: $decodedData');
          
          // Check if response has status field
          if (decodedData is Map<String, dynamic> && decodedData.containsKey('status')) {
            bool success = decodedData['status'] == 'success';
            print('Reset metrics success: $success');
            return success;
          } else {
            // If no status field, assume success if we got 200
            print('No status field in response, assuming success for 200 status code');
            return true;
          }
        } catch (jsonError) {
          print('Error parsing JSON response: $jsonError');
          // If JSON parsing fails but we got 200, assume success
          return true;
        }
      } else {
        print('Failed to reset metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false; // Return false instead of throwing exception
      }
    } catch (e) {
      print('Error resetting metrics: $e');
      return false; // Return false instead of rethrowing
    }
  }
  
  Future<bool> resetTopology() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/topology/reset');
      print('Resetting topology at: $url');

      final http.Response response = await http.post(url, headers: postRequestHeaders);
      print('Reset topology response status: ${response.statusCode}');
      print('Reset topology response body: ${response.body}');

      if (response.statusCode == 200) {
        try {
          final dynamic decodedData = jsonDecode(response.body);
          if (decodedData is Map<String, dynamic> && decodedData.containsKey('status')) {
            return decodedData['status'] == 'success';
          }
          return true;
        } catch (jsonError) {
          return true;
        }
      } else {
        print('Failed to reset topology: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false;
      }
    } catch (e) {
      print('Error resetting topology: $e');
      return false;
    }
  }

  Future<bool> deleteLogs() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/logs/delete');
      print('Deleting logs at: $url');
      
      final http.Response response = await http.post(url, headers: postRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return decodedData['status'] == 'success';
      } else {
        print('Failed to delete logs: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to delete logs');
      }
    } catch (e) {
      print('Error deleting logs: $e');
      rethrow;
    }
  }

  Future<List<AvailableAgent>> getAvailableAgents() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/agents/available');
      print('Available network agents at: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final List<dynamic> decodedData = jsonDecode(response.body);
        List<AvailableAgent> availableAgents = [];
        for (var agentData in decodedData) {
          availableAgents.add(AvailableAgent.fromJson(agentData));
        }
        return availableAgents;
      } else {
        print('Failed to get available network agents: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to get available network agents');
      }
    } catch (e) {
      print('Error getting available agents: $e'); rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchPhysicalTopology({String? timestamp}) async {
    try {
      // Build URL with optional timestamp parameter
      String urlStr = '${config.EnvironmentConfig.agentUrl}/topology/physical';
      if (timestamp != null) {
        urlStr += '?timestamp=$timestamp';
      }
      var url = Uri.parse(urlStr);
      print('Fetching physical topology from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          print('Successfully fetched physical topology with ${decodedData['nodes']?.length ?? 0} nodes');
          // Embeddings are now included in the topology response
          return decodedData;
        } else {
          print('Physical topology data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid physical topology data format');
        }
      } else {
        print('Failed to load physical topology: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load physical topology');
      }
    } catch (e) {
      print('Error fetching physical topology: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchRouterDetails(String routerId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/router/$routerId');
      print('Fetching router details from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Router details data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid router details data format');
        }
      } else {
        print('Failed to load router details: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load router details');
      }
    } catch (e) {
      print('Error fetching router details: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchDeviceDetails(String deviceId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/device/$deviceId');
      print('Fetching device details from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Device details data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid device details data format');
        }
      } else {
        print('Failed to load device details: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load device details');
      }
    } catch (e) {
      print('Error fetching device details: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchNodeEmbeddings(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/embeddings/$nodeId');
      print('Fetching node embeddings from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Node embeddings data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid node embeddings data format');
        }
      } else {
        print('Failed to load node embeddings: ${response.statusCode}');
        print('Response body: ${response.body}');
        // Return empty result instead of throwing
        return {'node_id': nodeId, 'router_embedding': null, 'interface_embeddings': []};
      }
    } catch (e) {
      print('Error fetching node embeddings: $e');
      // Return empty result instead of throwing
      return {'node_id': nodeId, 'router_embedding': null, 'interface_embeddings': []};
    }
  }

  // GNN / Anomaly API methods
  Future<List<String>> getSnapshots() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/snapshots');
      print('Fetching snapshots from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData['snapshots'] is List) {
          return List<String>.from(decodedData['snapshots']);
        }
        return [];
      } else {
        print('Failed to load snapshots: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching snapshots: $e');
      return [];
    }
  }

  // Network Descriptor API methods

  Future<List<NetworkDescriptor>> listNetworkDescriptors() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/networks');
      print('Fetching network descriptors from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is List) {
          return decodedData
              .whereType<Map<String, dynamic>>()
              .map(NetworkDescriptor.fromJson)
              .toList();
        }
        return [];
      } else {
        print('Failed to load network descriptors: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching network descriptors: $e');
      return [];
    }
  }

  Future<bool> teardownDeployment() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/networks/teardown');
      print('Tearing down current deployment at: $url');

      final http.Response response =
          await http.post(url, headers: postRequestHeaders);

      if (response.statusCode == 200) {
        print('Teardown initiated');
        return true;
      } else {
        print('Failed to start teardown: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false;
      }
    } catch (e) {
      print('Error starting teardown: $e');
      return false;
    }
  }

  Future<bool> deployNetworkDescriptor(String networkId) async {
    try {
      final encodedId = Uri.encodeComponent(networkId);
      var url = Uri.parse(
          '${config.EnvironmentConfig.agentUrl}/networks/$encodedId/deploy');
      print('Deploying network descriptor: $networkId');

      final http.Response response =
          await http.post(url, headers: postRequestHeaders);

      if (response.statusCode == 200) {
        print('Deploy initiated for: $networkId');
        return true;
      } else {
        print('Failed to deploy network descriptor: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false;
      }
    } catch (e) {
      print('Error deploying network descriptor: $e');
      return false;
    }
  }

  // VPN and TrafficTest API methods

  Future<List<VpnInfo>> fetchVpns() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/vpns');
      print('Fetching VPNs from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is List) {
          return decodedData
              .whereType<Map<String, dynamic>>()
              .map(VpnInfo.fromJson)
              .toList();
        }
        return [];
      } else {
        print('Failed to load VPNs: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching VPNs: $e');
      return [];
    }
  }

  Future<List<TrafficTestInfo>> fetchTrafficTests() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/traffictests');
      print('Fetching traffic tests from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is List) {
          return decodedData
              .whereType<Map<String, dynamic>>()
              .map(TrafficTestInfo.fromJson)
              .toList();
        }
        return [];
      } else {
        print('Failed to load traffic tests: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching traffic tests: $e');
      return [];
    }
  }

  Future<bool> deleteTrafficTest(String name) async {
    try {
      final encoded = Uri.encodeComponent(name);
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/traffictests/$encoded/delete');
      print('Deleting TrafficTest: $name at $url');

      final http.Response response = await http.post(url, headers: postRequestHeaders);

      if (response.statusCode == 200) {
        print('Successfully deleted TrafficTest: $name');
        return true;
      } else {
        print('Failed to delete TrafficTest $name: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false;
      }
    } catch (e) {
      print('Error deleting TrafficTest $name: $e');
      return false;
    }
  }

  Future<bool> deleteVpn(String name) async {
    try {
      final encoded = Uri.encodeComponent(name);
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/vpns/$encoded/delete');
      print('Deleting VPN: $name at $url');

      final http.Response response = await http.post(url, headers: postRequestHeaders);

      if (response.statusCode == 200) {
        print('Successfully initiated VPN delete: $name');
        return true;
      } else if (response.statusCode == 409) {
        // Another VPN delete is already in progress.
        print('VPN delete already in progress: ${response.body}');
        return false;
      } else {
        print('Failed to delete VPN $name: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false;
      }
    } catch (e) {
      print('Error deleting VPN $name: $e');
      return false;
    }
  }

  Future<Map<String, dynamic>> getVpnDeleteStatus() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/vpns/delete/status');
      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        return jsonDecode(response.body) as Map<String, dynamic>;
      }
      return {'in_progress': false, 'vpn_name': null};
    } catch (e) {
      print('Error getting VPN delete status: $e');
      return {'in_progress': false, 'vpn_name': null};
    }
  }

  Future<List<VyosInfrastructureInfo>> fetchVyosInfrastructure() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/infrastructure');
      print('Fetching VyosInfrastructure from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is List) {
          return decodedData
              .whereType<Map<String, dynamic>>()
              .map(VyosInfrastructureInfo.fromJson)
              .toList();
        }
        return [];
      } else {
        print('Failed to load VyosInfrastructure: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching VyosInfrastructure: $e');
      return [];
    }
  }

  /// Fetch VPNs, TrafficTests, and VyosInfrastructure in a single round-trip.
  ///
  /// Calls GET /infrastructure/state which replaces three separate calls
  /// (GET /vpns + GET /traffictests + GET /infrastructure).
  ///
  /// Returns a record with three typed lists.  Falls back gracefully: if the
  /// server returns an error the individual lists are empty.
  Future<({List<VpnInfo> vpns, List<TrafficTestInfo> trafficTests, List<VyosInfrastructureInfo> infrastructure})>
      fetchInfrastructureState() async {
    const empty = (vpns: <VpnInfo>[], trafficTests: <TrafficTestInfo>[], infrastructure: <VyosInfrastructureInfo>[]);
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/infrastructure/state');
      print('Fetching infrastructure state from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decoded = jsonDecode(response.body);
        if (decoded is! Map<String, dynamic>) return empty;

        final vpns = (decoded['vpns'] as List? ?? [])
            .whereType<Map<String, dynamic>>()
            .map(VpnInfo.fromJson)
            .toList();

        final trafficTests = (decoded['traffic_tests'] as List? ?? [])
            .whereType<Map<String, dynamic>>()
            .map(TrafficTestInfo.fromJson)
            .toList();

        final infrastructure = (decoded['infrastructure'] as List? ?? [])
            .whereType<Map<String, dynamic>>()
            .map(VyosInfrastructureInfo.fromJson)
            .toList();

        return (vpns: vpns, trafficTests: trafficTests, infrastructure: infrastructure);
      } else {
        print('Failed to load infrastructure state: ${response.statusCode}');
        return empty;
      }
    } catch (e) {
      print('Error fetching infrastructure state: $e');
      return empty;
    }
  }

  Future<Map<String, dynamic>> fetchRoutingMetrics(String nodeId) async {
    try {
      final encoded = Uri.encodeComponent(nodeId);
      final url = Uri.parse(
          '${config.EnvironmentConfig.agentUrl}/metrics/routing/$encoded');
      print('Fetching routing metrics for: $nodeId at $url');

      final http.Response response =
          await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) return decoded;
        return {};
      } else {
        print(
            'Failed to load routing metrics for $nodeId: ${response.statusCode}');
        return {};
      }
    } catch (e) {
      print('Error fetching routing metrics for $nodeId: $e');
      return {};
    }
  }

  Future<Map<String, dynamic>> fetchRouterMetricsRange(
    DateTime start,
    DateTime end, {
    List<String>? nodeNames,
  }) async {
    try {
      final startIso = start.toUtc().toIso8601String();
      final endIso   = end.toUtc().toIso8601String();
      String urlStr  = '${config.EnvironmentConfig.agentUrl}/metrics/range?start=${Uri.encodeComponent(startIso)}&end=${Uri.encodeComponent(endIso)}';
      if (nodeNames != null && nodeNames.isNotEmpty) {
        urlStr += '&nodes=${Uri.encodeComponent(nodeNames.join(','))}';
      }
      final url = Uri.parse(urlStr);
      print('Fetching router metrics range from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);
      if (response.statusCode == 200) {
        final dynamic decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) return decoded;
        return {};
      } else {
        print('Failed to load router metrics range: ${response.statusCode}');
        return {};
      }
    } catch (e) {
      print('Error fetching router metrics range: $e');
      return {};
    }
  }

  Future<Map<String, dynamic>> fetchTrafficMetricsRange(
    List<String> testNames,
    DateTime start,
    DateTime end,
  ) async {
    try {
      final startIso = start.toUtc().toIso8601String();
      final endIso   = end.toUtc().toIso8601String();
      final testsStr = testNames.join(',');
      final urlStr   = '${config.EnvironmentConfig.agentUrl}/traffictests/metrics/range'
          '?start=${Uri.encodeComponent(startIso)}'
          '&end=${Uri.encodeComponent(endIso)}'
          '&tests=${Uri.encodeComponent(testsStr)}';
      final url = Uri.parse(urlStr);
      print('Fetching traffic metrics range from: $url');

      final http.Response response = await http.get(url, headers: getRequestHeaders);
      if (response.statusCode == 200) {
        final dynamic decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) return decoded;
        return {};
      } else {
        print('Failed to load traffic metrics range: ${response.statusCode}');
        return {};
      }
    } catch (e) {
      print('Error fetching traffic metrics range: $e');
      return {};
    }
  }

  Future<List<TrafficFlowMetrics>> fetchTrafficTestMetrics(String testName) async {
    try {
      final encoded = Uri.encodeComponent(testName);
      final url = Uri.parse(
          '${config.EnvironmentConfig.agentUrl}/traffictests/$encoded/metrics');
      print('Fetching traffic metrics for: $testName at $url');

      final http.Response response =
          await http.get(url, headers: getRequestHeaders);

      if (response.statusCode == 200) {
        final dynamic decoded = jsonDecode(response.body);
        if (decoded is List) {
          return decoded
              .whereType<Map<String, dynamic>>()
              .map(TrafficFlowMetrics.fromJson)
              .toList();
        }
        return [];
      } else {
        print(
            'Failed to load traffic metrics for $testName: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching traffic metrics for $testName: $e');
      return [];
    }
  }

  Future<List<Map<String, dynamic>>> getAnomalies({String? timestamp}) async {
    try {
      String urlStr = '${config.EnvironmentConfig.agentUrl}/anomalies';
      if (timestamp != null) {
        urlStr += '?timestamp=$timestamp';
      }
      var url = Uri.parse(urlStr);
      print('Fetching anomalies from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData['anomalies'] is List) {
           return List<Map<String, dynamic>>.from(decodedData['anomalies']);
        }
        return [];
      } else {
        print('Failed to load anomalies: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching anomalies: $e');
      return [];
    }
  }
}
