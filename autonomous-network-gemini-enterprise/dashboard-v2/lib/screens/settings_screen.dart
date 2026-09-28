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
import '../appstate.dart';
import '../models/agent.dart';
import '../utils/APIService.dart';
import '../models/available_agent.dart';
import '../utils/environment_config.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Google logo
              ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: Image.asset(
                  'assets/images/google.png',
                  width: 24,
                  height: 24,
                  fit: BoxFit.cover,
                ),
              ),
              const SizedBox(width: 12),
              const Text(
                'Settings',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
          backgroundColor: const Color(0xFF0D47A1),
          foregroundColor: Colors.white,
          centerTitle: true,
          actions: [
            IconButton(
              icon: const Icon(Icons.refresh),
              onPressed: () {
                // Implementation left empty for now
              },
              tooltip: 'Refresh',
            ),
          ],
          bottom: const TabBar(
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white70,
            indicatorColor: Colors.white,
            tabs: [
              Tab(
                icon: Icon(Icons.engineering),
                text: 'Agent Settings',
              ),
              Tab(
                icon: Icon(Icons.storage),
                text: 'Spanner',
              ),
            ],
          ),
        ),
        body: const TabBarView(
          children: [
            Padding(
              padding: EdgeInsets.all(16.0),
              child: AgentSettingsSection(),
            ),
            Padding(
              padding: EdgeInsets.all(16.0),
              child: SpannerSection(),
            ),
          ],
        ),
      ),
    );
  }
}

class AgentSettingsSection extends StatelessWidget {
  const AgentSettingsSection({super.key});

  @override
  Widget build(BuildContext context) {
    final List<Map<String, String>> reasoningEngines = [
      {
        "name": "SupervisorAgent",
        "role": "Master Incident Orchestrator",
        "id": EnvironmentConfig.reasoningEngineId,
        "status": "ONLINE"
      },
      {
        "name": "DetectiveAgent",
        "role": "Telemetry Metrics, K8s & Syslog Lead",
        "id": EnvironmentConfig.detectiveEngineId,
        "status": "ONLINE"
      },
      {
        "name": "ImpactAgent",
        "role": "Blast Radius Simulator",
        "id": EnvironmentConfig.impactEngineId,
        "status": "ONLINE"
      },
      {
        "name": "DesignerAgent",
        "role": "Declarative Topology Planner",
        "id": EnvironmentConfig.designerEngineId,
        "status": "ONLINE"
      },
      {
        "name": "TesterAgent",
        "role": "Synthetic Traffic Executor",
        "id": EnvironmentConfig.testerEngineId,
        "status": "ONLINE"
      },
      {
        "name": "ChaosAgent",
        "role": "Network Degradation Injector",
        "id": EnvironmentConfig.chaosEngineId,
        "status": "ONLINE"
      },
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Authoritative Reasoning Engine Team',
          style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.bold,
                color: const Color(0xFF0D47A1),
              ),
        ),
        const SizedBox(height: 8),
        const Text(
          'Fully managed, serverless Vertex AI Reasoning Engine AI suite. Dynamic registration is decoupled as peer collaboration is orchestrated programmatically via delegate_to_agent.',
          style: TextStyle(color: Colors.grey, fontSize: 13),
        ),
        const SizedBox(height: 16),
        Expanded(
          child: ListView.builder(
            itemCount: reasoningEngines.length,
            itemBuilder: (context, index) {
              final re = reasoningEngines[index];
              return Card(
                elevation: 2,
                margin: const EdgeInsets.only(bottom: 12),
                child: ListTile(
                  leading: const Icon(Icons.psychology, color: Color(0xFF0D47A1), size: 36),
                  title: Text(re["name"]!, style: const TextStyle(fontWeight: FontWeight.bold)),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 4),
                      Text('Role: ${re["role"]}'),
                      Text('Engine ID: ${re["id"]}', style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
                    ],
                  ),
                  trailing: Chip(
                    label: Text(re["status"]!, style: const TextStyle(color: Colors.white, fontSize: 10)),
                    backgroundColor: Colors.green,
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class AgentList extends StatelessWidget {
  const AgentList({super.key});

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);
    final agents = appState.agents;

    if (agents.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.engineering,
              size: 64,
              color: Colors.grey[400],
            ),
            const SizedBox(height: 16),
            Text(
              'No agents configured',
              style: TextStyle(
                fontSize: 18,
                color: Colors.grey[600],
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Add an agent to get started',
              style: TextStyle(
                fontSize: 14,
                color: Colors.grey[500],
              ),
            ),
          ],
        ),
      );
    }

    return Expanded(
      child: ListView.builder(
        itemCount: agents.length,
        itemBuilder: (context, index) {
          final agent = agents[index];
          return AgentListItem(agent: agent);
        },
      ),
    );
  }
}

class AgentListItem extends StatelessWidget {
  final Agent agent;

  const AgentListItem({super.key, required this.agent});

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context, listen: false);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Google logo image on the left side
            Image.asset(
              agent.name == "Anomaly Resolution Agent" ? 'assets/images/Zinkworks.png' : 'assets/images/google.png',
              width: 80,
              height: 40,
              fit: BoxFit.contain,
            ),
            const SizedBox(width: 16), // Spacing between image and content
            // Main content column
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Text(
                          agent.name.isNotEmpty ? agent.name : 'Unnamed Agent',
                          style: const TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                          ),
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.delete_outline, color: Colors.red),
                        onPressed: () => _confirmDelete(context, appState),
                        tooltip: 'Remove Agent',
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Description: ${agent.description}',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontSize: 14,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'URL: ${agent.url}',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontSize: 14,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _confirmDelete(BuildContext context, Appstate appState) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove Agent'),
        content: Text('Are you sure you want to remove "${agent.name.isNotEmpty ? agent.name : 'Unnamed Agent'}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              await appState.removeAgent(agent.id);
              if (context.mounted) {
                Navigator.of(context).pop();
              }
            },
            child: const Text('Remove', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }
}

class AddAgentDialog extends StatefulWidget {
  const AddAgentDialog({super.key});

  @override
  State<AddAgentDialog> createState() => _AddAgentDialogState();
}

class _AddAgentDialogState extends State<AddAgentDialog> {
  final _urlController = TextEditingController();
  final _focusNode = FocusNode();
  
  @override
  void initState() {
    super.initState();
    // Request focus after the dialog is built
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focusNode.requestFocus();
    });
  }
  
  @override
  void dispose() {
    _urlController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add Agent URL'),
      content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
             TextField(
              controller: _urlController,
              focusNode: _focusNode,
              decoration: const InputDecoration(
                hintText: 'Enter agent URL',
                border: OutlineInputBorder(),
                contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              ),
              autofocus: true,
              onSubmitted: (_) => _addAgent(),
            ),
          ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: _addAgent,
          child: const Text('Add'),
        ),
      ],
    );
  }

  Future<void> _addAgent() async {
    final appState = Provider.of<Appstate>(context, listen: false);
    await appState.addAgent(
      _urlController.text.trim(),
    );
    if (context.mounted) {
      Navigator.of(context).pop();
    }
  }
}

class AvailableAgentsDialog extends StatefulWidget {
  const AvailableAgentsDialog({super.key});

  @override
  State<AvailableAgentsDialog> createState() => _AvailableAgentsDialogState();
}

class _AvailableAgentsDialogState extends State<AvailableAgentsDialog> {
  late Future<List<AvailableAgent>> _availableAgentsFuture;
  final APIService _apiService = APIService();

  @override
  void initState() {
    super.initState();
    _availableAgentsFuture = _apiService.getAvailableAgents();
  }

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);
    final addedAgentUrls = appState.agents.map((a) => a.url).toSet();

    return Dialog(
      child: Container(
        width: MediaQuery.of(context).size.width * 0.8,
        height: MediaQuery.of(context).size.height * 0.7,
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Available Agents',
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: const Color(0xFF0D47A1),
                      ),
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'The following agents are currently running and can be added to your dashboard.',
              style: TextStyle(color: Colors.grey, fontSize: 14),
            ),
            const SizedBox(height: 16),
            Expanded(
              child: FutureBuilder<List<AvailableAgent>>(
                future: _availableAgentsFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  } else if (snapshot.hasError) {
                    return Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 64,
                            color: Colors.grey[400],
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Error fetching available agents',
                            style: TextStyle(
                              fontSize: 18,
                              color: Colors.grey[600],
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            '${snapshot.error}',
                            style: TextStyle(
                              fontSize: 14,
                              color: Colors.grey[500],
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    );
                  } else if (!snapshot.hasData || snapshot.data!.isEmpty) {
                    return Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.engineering,
                            size: 64,
                            color: Colors.grey[400],
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'No available agents found',
                            style: TextStyle(
                              fontSize: 18,
                              color: Colors.grey[600],
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'No agents are currently running',
                            style: TextStyle(
                              fontSize: 14,
                              color: Colors.grey[500],
                            ),
                          ),
                        ],
                      ),
                    );
                  }

                  final availableAgents = snapshot.data!;
                  final agentsToShow = availableAgents.where((a) => !addedAgentUrls.contains(a.url)).toList();

                  if (agentsToShow.isEmpty) {
                    return Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.check_circle_outline,
                            size: 64,
                            color: Colors.green[400],
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'All available agents added',
                            style: TextStyle(
                              fontSize: 18,
                              color: Colors.grey[600],
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'All running agents have been added to your dashboard',
                            style: TextStyle(
                              fontSize: 14,
                              color: Colors.grey[500],
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    );
                  }

                  return SingleChildScrollView(
                    child: DataTable(
                      columns: const [
                        DataColumn(label: Text('Name', style: TextStyle(fontWeight: FontWeight.bold))),
                        DataColumn(label: Text('URL', style: TextStyle(fontWeight: FontWeight.bold))),
                        DataColumn(label: Text('Action', style: TextStyle(fontWeight: FontWeight.bold))),
                      ],
                      rows: agentsToShow.map((agent) => DataRow(cells: [
                        DataCell(Text(agent.name)),
                        DataCell(Text(agent.url)),
                        DataCell(
                          ElevatedButton(
                            onPressed: () async {
                              await appState.addAgent(agent.url);
                              if (context.mounted) {
                                Navigator.of(context).pop();
                              }
                            },
                            style: ElevatedButton.styleFrom(
                              backgroundColor: const Color(0xFF0D47A1),
                              foregroundColor: Colors.white,
                            ),
                            child: const Text('Add'),
                          ),
                        ),
                      ])).toList(),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SpannerSection extends StatefulWidget {
  const SpannerSection({super.key});

  @override
  State<SpannerSection> createState() => _SpannerSectionState();
}

class _SpannerSectionState extends State<SpannerSection> {
  bool _isDeleting = false;
  bool _isDeletingLogs = false;
  bool _isDeletingTopology = false;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Spanner Database Management',
          style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.bold,
                color: const Color(0xFF0D47A1),
              ),
        ),
        const SizedBox(height: 16),
        const Text(
          'Manage performance metrics and logs stored in Spanner database.',
          style: TextStyle(
            color: Colors.grey,
            fontSize: 14,
          ),
        ),
        const SizedBox(height: 32),
        
        // Performance Metrics Section
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.analytics,
                      color: const Color(0xFF0D47A1),
                      size: 24,
                    ),
                    const SizedBox(width: 12),
                    Text(
                      'Performance Metrics',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  'Delete all performance metrics data from the Spanner database. This action cannot be undone.',
                  style: TextStyle(
                    color: Colors.grey,
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: _isDeleting ? null : () => _showDeleteMetricsDialog(context),
                  icon: _isDeleting 
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
                        ),
                      )
                    : const Icon(Icons.delete),
                  label: Text(_isDeleting ? 'Deleting...' : 'Delete Performance Metrics'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.red,
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ),
          ),
        ),
        
        const SizedBox(height: 20),
        
        // Logs Section
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.description,
                      color: const Color(0xFF0D47A1),
                      size: 24,
                    ),
                    const SizedBox(width: 12),
                    Text(
                      'System Logs',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  'Remove all system logs from the Spanner database. This action cannot be undone.',
                  style: TextStyle(
                    color: Colors.grey,
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: _isDeletingLogs ? null : () => _showDeleteLogsDialog(context),
                  icon: _isDeletingLogs 
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
                        ),
                      )
                    : const Icon(Icons.delete),
                  label: Text(_isDeletingLogs ? 'Removing...' : 'Remove All Logs'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.red,
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ),
          ),
        ),

        const SizedBox(height: 20),

        // Network Topology Section
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.hub,
                      color: const Color(0xFF0D47A1),
                      size: 24,
                    ),
                    const SizedBox(width: 12),
                    Text(
                      'Network Topology',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  'Clear all physical and logical topology data from Spanner, including routers, interfaces, links, devices, VRFs, BGP sessions, and GNN embeddings. This action cannot be undone.',
                  style: TextStyle(
                    color: Colors.grey,
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: _isDeletingTopology ? null : () => _showDeleteTopologyDialog(context),
                  icon: _isDeletingTopology
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
                        ),
                      )
                    : const Icon(Icons.delete_sweep),
                  label: Text(_isDeletingTopology ? 'Clearing...' : 'Clear Topology Data'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.red,
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  void _showDeleteMetricsDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning, color: Colors.orange),
            SizedBox(width: 8),
            Text('Delete Performance Metrics'),
          ],
        ),
        content: const Text(
          'Are you sure you want to delete all performance metrics? This action cannot be undone and will permanently remove all stored performance data.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => _deleteMetrics(context),
            child: const Text(
              'Delete',
              style: TextStyle(color: Colors.red),
            ),
          ),
        ],
      ),
    );
  }

  void _showDeleteLogsDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning, color: Colors.orange),
            SizedBox(width: 8),
            Text('Remove All Logs'),
          ],
        ),
        content: const Text(
          'Are you sure you want to remove all system logs? This action cannot be undone and will permanently delete all log entries.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => _deleteLogs(context),
            child: const Text(
              'Remove',
              style: TextStyle(color: Colors.red),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _deleteMetrics(BuildContext context) async {
    Navigator.of(context).pop(); // Close the confirmation dialog first
    
    // Set loading state
    setState(() {
      _isDeleting = true;
    });

    try {
      print('Starting metrics deletion...');
      final apiService = APIService();
      final success = await apiService.resetMetrics();
      print('Metrics deletion completed. Success: $success');
      
      // Clear loading state
      if (mounted) {
        setState(() {
          _isDeleting = false;
        });
      }
      
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              success
                  ? 'Performance metrics deleted successfully'
                  : 'Failed to delete performance metrics',
            ),
            backgroundColor: success ? Colors.green : Colors.red,
          ),
        );
      }
    } catch (e) {
      print('Error during metrics deletion: $e');
      
      // Clear loading state
      if (mounted) {
        setState(() {
          _isDeleting = false;
        });
      }
      
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error deleting performance metrics: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  Future<void> _deleteLogs(BuildContext context) async {
    Navigator.of(context).pop(); // Close the confirmation dialog first
    
    // Set loading state
    setState(() {
      _isDeletingLogs = true;
    });

    try {
      print('Starting logs deletion...');
      final apiService = APIService();
      final success = await apiService.deleteLogs();
      print('Logs deletion completed. Success: $success');
      
      // Clear loading state
      if (mounted) {
        setState(() {
          _isDeletingLogs = false;
        });
      }
      
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              success
                  ? 'All logs removed successfully'
                  : 'Failed to remove logs',
            ),
            backgroundColor: success ? Colors.green : Colors.red,
          ),
        );
      }
    } catch (e) {
      print('Error during logs deletion: $e');
      
      // Clear loading state
      if (mounted) {
        setState(() {
          _isDeletingLogs = false;
        });
      }
      
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error removing logs: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  void _showDeleteTopologyDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning, color: Colors.orange),
            SizedBox(width: 8),
            Text('Clear Topology Data'),
          ],
        ),
        content: const Text(
          'Are you sure you want to clear all network topology data? This will permanently remove all routers, interfaces, links, devices, VRFs, BGP sessions, and GNN embeddings from Spanner. This action cannot be undone.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => _deleteTopology(context),
            child: const Text(
              'Clear',
              style: TextStyle(color: Colors.red),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _deleteTopology(BuildContext context) async {
    Navigator.of(context).pop(); // Close the confirmation dialog first

    setState(() {
      _isDeletingTopology = true;
    });

    try {
      print('Starting topology clear...');
      final apiService = APIService();
      final success = await apiService.resetTopology();
      print('Topology clear completed. Success: $success');

      if (mounted) {
        setState(() {
          _isDeletingTopology = false;
        });
      }

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              success
                  ? 'Topology data cleared successfully'
                  : 'Failed to clear topology data',
            ),
            backgroundColor: success ? Colors.green : Colors.red,
          ),
        );
      }
    } catch (e) {
      print('Error during topology clear: $e');

      if (mounted) {
        setState(() {
          _isDeletingTopology = false;
        });
      }

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error clearing topology data: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }
}
