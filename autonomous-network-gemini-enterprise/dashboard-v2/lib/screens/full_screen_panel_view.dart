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
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../models/panel_type.dart';
import '../models/log_entry.dart';
import '../widgets/chat_panel.dart';
import '../widgets/log_widget.dart';
import '../widgets/performance/performance_tab_widget.dart';
import '../widgets/anomaly_panel.dart';
import '../appstate.dart';

class FullScreenPanelView extends StatelessWidget {
  final PanelType panelType;
  final io.Socket? socket;
  final List<LogEntry>? logs;
  final bool? isLoading;

  const FullScreenPanelView({
    super.key,
    required this.panelType,
    this.socket,
    this.logs,
    this.isLoading,
  });

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        return Scaffold(
          appBar: AppBar(
            backgroundColor: const Color(0xFF0D47A1), // Dark blue
            foregroundColor: Colors.white,
            centerTitle: true, // Center the title
            leading: IconButton(
              icon: const Icon(Icons.arrow_back),
              onPressed: () {
                Navigator.of(context).pop();
              },
              tooltip: 'Back to Dashboard',
            ),
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
                Text(
                  panelType.displayName,
                  style: const TextStyle(
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
                  padding: const EdgeInsets.only(right: 16.0),
                  child: Text(
                    appState.isConnected ? 'Connected' : 'Disconnected',
                    style: TextStyle(
                      fontSize: 12,
                      color: appState.isConnected ? Colors.green[100] : Colors.red[100],
                    ),
                  ),
                ),
              ),
            ],
          ),
          body: _buildPanelContent(),
        );
      },
    );
  }

  Widget _buildPanelContent() {
    switch (panelType) {
      case PanelType.chat:
        return ChatPanel(socket: socket!, isFullScreen: true);
      case PanelType.logs:
        return Consumer<Appstate>(
          builder: (context, appState, child) {
            return LogWidget(
              logs: logs ?? appState.logs,
              socket: socket!,
              isLoading: isLoading ?? appState.isLoadingLogs,
              isFullScreen: true,
            );
          },
        );
      case PanelType.performance:
        return const PerformanceTabWidget(isFullScreen: true);
      case PanelType.anomaly:
        return const AnomalyPanel(isFullScreen: true);
    }
  }
}
