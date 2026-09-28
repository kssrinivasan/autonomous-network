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
import '../models/network_node.dart';
import '../utils/environment_config.dart';
import '../widgets/chat_panel.dart';
import '../widgets/topology/google_maps_topology.dart';
import '../widgets/topology/logical_topology.dart';
import '../widgets/markdown_drawer.dart';
import '../widgets/main_drawer.dart';
import '../widgets/log_widget.dart';
import '../widgets/performance/performance_tab_widget.dart';
import '../widgets/anomaly_panel.dart';
import '../widgets/timeslot/timeslot_slider.dart';
import '../widgets/vpn_traffic_panel.dart';
import 'settings_screen.dart';

class NetworkDashboard extends StatefulWidget {
  const NetworkDashboard({super.key});

  @override
  State<NetworkDashboard> createState() => _NetworkDashboardState();
}

class _NetworkDashboardState extends State<NetworkDashboard>
    with TickerProviderStateMixin {
  // Global key for the scaffold to access the drawer
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();

  // Track whether the drawer has been opened at least once (removes highlight)
  bool _drawerHasBeenOpened = false;

  // Pulse animation for the menu icon highlight when no network is loaded
  late AnimationController _pulseController;
  late Animation<double> _pulseAnimation;

  // Widget display state
  bool _showChat = false; // Chat is hidden by default
  bool _showLogs = false;
  bool _showAnomalies = false;
  bool _showVpnPanel = true;
  bool _showTimeslot = false; // Timeslot slider hidden by default

  // Control the horizontal split view ratios
  double _chatPanelRatio = 0.3; // 30% for chat panel
  double _anomalyPanelRatio = 0.25; // 25% for anomaly panel
  double _vpnPanelRatio = 0.25; // 25% for VPN/traffic test panel
  static const double _minHorizontalSplitRatio = 0.15;
  static const double _maxHorizontalSplitRatio = 0.8;

  // Control the vertical split view ratio (topology vs. logs)
  double _verticalSplitRatio = 0.5; // 50% for topology, 50% for performance/logs
  static const double _minVerticalSplitRatio = 0.3;
  static const double _maxVerticalSplitRatio = 0.9;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
    _pulseAnimation = Tween<double>(begin: 0.2, end: 0.85).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  void _toggleLogs() {
    final appState = Provider.of<Appstate>(context, listen: false);
    setState(() {
      _showLogs = !_showLogs;
      appState.toggleLogs(_showLogs);

      // If logs are being shown, hide performance graph
      if (_showLogs && appState.showPerformanceGraph) {
        appState.togglePerformanceGraph();
      }
    });
  }

  void _togglePerformanceGraph() {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.togglePerformanceGraph();

    // If performance graph is being shown, hide logs
    if (appState.showPerformanceGraph && _showLogs) {
      setState(() {
        _showLogs = false;
        appState.toggleLogs(false);
      });
    }
  }

  void _toggleChat() {
    setState(() {
      _showChat = !_showChat;
    });
  }

  void _toggleAnomalies() {
    setState(() {
      _showAnomalies = !_showAnomalies;
      // Close VPN panel when opening anomalies panel
      if (_showAnomalies) {
        _showVpnPanel = false;
      }
    });
  }

  void _toggleVpnPanel() {
    setState(() {
      _showVpnPanel = !_showVpnPanel;
      // Close anomaly panel when opening VPN panel
      if (_showVpnPanel) {
        _showAnomalies = false;
      }
    });
  }

  void _toggleTimeslot() {
    setState(() {
      _showTimeslot = !_showTimeslot;
    });
  }

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);

    return Scaffold(
      key: _scaffoldKey,
      drawer: const SavedNetworksDrawer(),
      onDrawerChanged: (isOpened) {
        if (isOpened && !_drawerHasBeenOpened) {
          setState(() {
            _drawerHasBeenOpened = true;
          });
        }
      },
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D47A1), // Dark blue
        foregroundColor: Colors.white,
        centerTitle: true, // Center the title
        leading: Consumer<Appstate>(
          builder: (context, appState, child) {
            final shouldHighlight =
                appState.topology.nodes.isEmpty && !_drawerHasBeenOpened;
            if (shouldHighlight) {
              return AnimatedBuilder(
                animation: _pulseAnimation,
                builder: (context, child) {
                  return Stack(
                    alignment: Alignment.center,
                    children: [
                      Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: Colors.amber.withValues(
                              alpha: _pulseAnimation.value * 0.55),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.amber.withValues(
                                  alpha: _pulseAnimation.value * 0.4),
                              blurRadius: 12,
                              spreadRadius: 2,
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.menu, color: Colors.white),
                        tooltip: 'Open menu',
                        onPressed: () =>
                            _scaffoldKey.currentState?.openDrawer(),
                      ),
                    ],
                  );
                },
              );
            }
            return IconButton(
              icon: const Icon(Icons.menu, color: Colors.white),
              tooltip: 'Open menu',
              onPressed: () => _scaffoldKey.currentState?.openDrawer(),
            );
          },
        ),
        // leading: Consumer<Appstate>(
        //   builder: (context, appState, child) {
        //     final notificationCount = appState.pushNotifications.length;
        //     final incidentCount = appState.incidents
        //         .where((i) => !i.hasResolution)
        //         .length;
        //     final hasAnyAlerts = notificationCount > 0 || incidentCount > 0;

        //     return AnimatedBuilder(
        //       animation: _vibrationAnimation,
        //       builder: (context, child) {
        //         return Transform.scale(
        //           scale: _vibrationAnimation.value,
        //           child: IconButton(
        //             icon: Icon(
        //               Icons.notifications,
        //               color: hasAnyAlerts ? Colors.red : Colors.white,
        //             ),
        //             onPressed: () {
        //               // Navigate to the notification screen
        //               Navigator.of(context).push(
        //                 MaterialPageRoute(
        //                   builder: (context) => const NotificationScreen(),
        //                 ),
        //               );
        //             },
        //             tooltip:
        //                 'Notifications & Incidents (${notificationCount + incidentCount})',
        //           ),
        //         );
        //       },
        //     );
        //   },
        // ),
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
              'Digital Twin Dashboard',
              style: TextStyle(
                fontWeight: FontWeight.bold, // Make the title bold
              ),
            ),
            const SizedBox(width: 8),
            // Connection status indicator
            Container(
              width: 12,
              height: 12,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: appState.isConnected ? Colors.green : Colors.red,
              ),
            ),
          ],
        ),
        actions: [
          // Connection status text
          Center(
            child: Padding(
              padding: const EdgeInsets.only(right: 8.0),
              child: Text(
                appState.isConnected ? 'Connected' : 'Disconnected',
                style: TextStyle(
                  fontSize: 12,
                  color: appState.isConnected
                      ? Colors.green[100]
                      : Colors.red[100],
                ),
              ),
            ),
          ),
          // Chat toggle button
          IconButton(
            icon: Icon(
              Icons.chat,
              color: _showChat ? Colors.amber : Colors.white,
            ),
            onPressed: () => _toggleChat(),
            tooltip: 'Toggle Chat',
          ),
          // // Topology view toggle button
          // Consumer<Appstate>(
          //   builder: (context, appState, child) {
          //     return IconButton(
          //       icon: Icon(
          //         appState.currentTopologyView == TopologyViewType.map
          //             ? Icons.map
          //             : Icons.device_hub,
          //         color: Colors.white,
          //       ),
          //       onPressed: () => appState.toggleTopologyView(),
          //       tooltip: appState.currentTopologyView == TopologyViewType.map
          //           ? 'Switch to Logical View'
          //           : 'Switch to Map View',
          //     );
          //   },
          // ),
          // Performance graph toggle button
          Consumer<Appstate>(
            builder: (context, appState, child) {
              return IconButton(
                icon: Icon(
                  Icons.show_chart,
                  color: appState.showPerformanceGraph
                      ? Colors.amber
                      : Colors.white,
                ),
                onPressed: () => _togglePerformanceGraph(),
                tooltip: 'Toggle Performance Graphs',
              );
            },
          ),
          // VPN / traffic test panel toggle button
          Consumer<Appstate>(
            builder: (context, appState, child) {
              final hasActiveTests = appState.trafficTests.any((t) => t.isActive);
              return IconButton(
                icon: Icon(
                  Icons.hub_outlined,
                  color: _showVpnPanel
                      ? Colors.amber
                      : (hasActiveTests ? Colors.lightGreenAccent : Colors.white),
                ),
                onPressed: _toggleVpnPanel,
                tooltip: 'Toggle VPNs & Traffic Tests',
              );
            },
          ),
          // Log toggle button
          IconButton(
            icon: Icon(
              Icons.list_alt,
              color: _showLogs ? Colors.amber : Colors.white,
            ),
            onPressed: _toggleLogs,
            tooltip: 'Toggle Logs',
          ),
        ],
      ),
      // // Add the markdown drawer as the end drawer
      // endDrawer: MarkdownDrawer(
      //   markdownContent: _markdownContent,
      //   title: 'Network Agent Resources',
      // ),
      body: Row(
        children: [
          // Left panel - Chat (only shown if _showChat is true)
          if (_showChat) ...[
            SizedBox(
              width: MediaQuery.of(context).size.width * _chatPanelRatio,
              child: ChatPanel(socket: appState.socket!),
            ),

            // Horizontal resizable divider for chat panel
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _chatPanelRatio +=
                      details.delta.dx / MediaQuery.of(context).size.width;
                  _chatPanelRatio = _chatPanelRatio.clamp(
                    _minHorizontalSplitRatio,
                    _maxHorizontalSplitRatio,
                  );
                });
              },
              child: Container(
                width: 8,
                color: const Color(0xFFE3F2FD), // Light blue background
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(
                      width: 2,
                      height: 30,
                      color: const Color(
                        0xFF90CAF9,
                      ), // Slightly darker blue for the handle
                    ),
                  ],
                ),
              ),
            ),
          ],

          // Main panel - Network Topology and Logs
          Expanded(
            child: Stack(
              children: [
                Column(
                  children: [
                    // Main content area - Always show Network Topology
                    Expanded(
                      flex: (_verticalSplitRatio * 100)
                          .round(), // Convert ratio to flex units
                      child: Consumer<Appstate>(
                        // Listen to topology changes only - filter and layout changes are handled internally
                        builder: (context, appState, child) {
                          // Create a key based only on the topology to avoid excessive rebuilds
                          final topologyKey = ValueKey(
                            'topology-${appState.topology.nodes.length}-'
                            '${appState.topology.connections.length}',
                          );

                          return Stack(
                            children: [
                              AnimatedSwitcher(
                                duration: const Duration(milliseconds: 300),
                                child: appState.currentTopologyView == TopologyViewType.map
                                    ? GoogleMapsTopologyWidget(
                                        key: ValueKey('map_$topologyKey'),
                                        topology: appState.topology,
                                      )
                                    : LogicalTopologyWidget(
                                        key: ValueKey('logical_$topologyKey'),
                                        topology: appState.topology,
                                        highlightedNodeIds: appState.highlightedNodeIds,
                                      ),
                              ),
                              // Empty-state overlay — lives inside the topology area
                              // so it moves with the vertical split handle.
                              if (appState.topology.nodes.isEmpty)
                                Positioned.fill(
                                  child: IgnorePointer(
                                    child: Center(
                                      child: Column(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          Icon(
                                            Icons.lan_outlined,
                                            size: 72,
                                            color: Colors.grey.shade300,
                                          ),
                                          const SizedBox(height: 20),
                                          Text(
                                            'To add a network, load a saved network',
                                            style: TextStyle(
                                              fontSize: 18,
                                              fontWeight: FontWeight.w500,
                                              color: Colors.grey.shade400,
                                            ),
                                          ),
                                          const SizedBox(height: 8),
                                          Row(
                                            mainAxisSize: MainAxisSize.min,
                                            children: [
                                              Icon(
                                                Icons.arrow_back,
                                                color: Colors.grey.shade400,
                                                size: 16,
                                              ),
                                              const SizedBox(width: 4),
                                              Text(
                                                'Open the menu in the top left to get started',
                                                style: TextStyle(
                                                  fontSize: 13,
                                                  color: Colors.grey.shade400,
                                                ),
                                              ),
                                            ],
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
                                ),
                            ],
                          );
                        },
                      ),
                    ),

                    // Show the vertical divider and bottom panel when logs or performance graph are enabled
                    Consumer<Appstate>(
                      builder: (context, appState, child) {
                    if (_showLogs || appState.showPerformanceGraph) {
                      return Expanded(
                        flex: ((1 - _verticalSplitRatio) * 100)
                            .round(), // Convert ratio to flex units
                        child: Column(
                          children: [
                            // Vertical resizable divider
                            GestureDetector(
                              behavior: HitTestBehavior.translucent,
                              onVerticalDragUpdate: (details) {
                                setState(() {
                                  // Calculate the new ratio based on the drag
                                  final totalHeight = MediaQuery.of(
                                    context,
                                  ).size.height;
                                  _verticalSplitRatio +=
                                      details.delta.dy / totalHeight;
                                  _verticalSplitRatio = _verticalSplitRatio
                                      .clamp(
                                        _minVerticalSplitRatio,
                                        _maxVerticalSplitRatio,
                                      );
                                });
                              },
                              child: Container(
                                height: 8,
                                color: const Color(
                                  0xFFE3F2FD,
                                ), // Light blue background
                                child: Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Container(
                                      width: 30,
                                      height: 2,
                                      color: const Color(
                                        0xFF90CAF9,
                                      ), // Slightly darker blue for the handle
                                    ),
                                  ],
                                ),
                              ),
                            ),

                            // Bottom panel - show either logs or performance graph
                            Expanded(
                              child: _showLogs
                                  ? LogWidget(
                                      logs: appState.logs,
                                      socket: appState.socket!,
                                      isLoading: appState.isLoadingLogs,
                                    )
                                  : const PerformanceTabWidget(),
                            ),
                          ],
                        ),
                      );
                    } else {
                      return const SizedBox.shrink();
                    }
                  },
                ),
              ],
            ),
            // Timeslot toggle button (bottom-right of topology area)
            Positioned(
              bottom: 16,
              right: 16,
              child: FloatingActionButton(
                mini: true,
                backgroundColor: _showTimeslot ? Colors.amber.shade700 : Colors.blueGrey,
                onPressed: _toggleTimeslot,
                tooltip: _showTimeslot ? 'Hide Timeline' : 'Show Timeline',
                child: Icon(
                  Icons.schedule,
                  color: Colors.white,
                ),
              ),
            ),

            // Overlay for the timeslot slider (conditionally shown)
            if (_showTimeslot)
              Positioned(
                bottom: 16,
                left: 0,
                right: 56, // leave room for the toggle FAB on the right
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 500),
                    child: const TimeslotSlider(),
                  ),
                ),
              ),
          ],
        ),
      ),

          // Right panel - Anomalies
          if (_showAnomalies) ...[
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _anomalyPanelRatio -=
                      details.delta.dx / MediaQuery.of(context).size.width;
                  _anomalyPanelRatio = _anomalyPanelRatio.clamp(
                    _minHorizontalSplitRatio,
                    _maxHorizontalSplitRatio,
                  );
                });
              },
              child: Container(
                width: 8,
                color: const Color(0xFFE3F2FD),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(width: 2, height: 30, color: const Color(0xFF90CAF9)),
                  ],
                ),
              ),
            ),
            SizedBox(
              width: MediaQuery.of(context).size.width * _anomalyPanelRatio,
              child: const AnomalyPanel(),
            ),
          ],

          // VPN / Traffic test panel (right side, resizable)
          if (_showVpnPanel) ...[
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _vpnPanelRatio -=
                      details.delta.dx / MediaQuery.of(context).size.width;
                  _vpnPanelRatio = _vpnPanelRatio.clamp(
                    _minHorizontalSplitRatio,
                    _maxHorizontalSplitRatio,
                  );
                });
              },
              child: Container(
                width: 8,
                color: const Color(0xFFE3F2FD),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(
                        width: 2, height: 30, color: const Color(0xFF90CAF9)),
                  ],
                ),
              ),
            ),
            SizedBox(
              width: MediaQuery.of(context).size.width * _vpnPanelRatio,
              child: const VpnTrafficPanel(),
            ),
          ],
        ],
      ),
    );
  }
}
