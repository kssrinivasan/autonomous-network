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
import '../models/network_descriptor.dart';
import '../utils/APIService.dart';
import '../screens/settings_screen.dart';

class SavedNetworksDrawer extends StatefulWidget {
  const SavedNetworksDrawer({super.key});

  @override
  State<SavedNetworksDrawer> createState() => _SavedNetworksDrawerState();
}

class _SavedNetworksDrawerState extends State<SavedNetworksDrawer> {
  static const _headerColor = Color(0xFF0D47A1);
  static const _accentColor = Color(0xFF1976D2);

  final APIService _api = APIService();

  // Whether the "Saved Networks" section is expanded.
  bool _networksExpanded = false;

  // Fetch state.
  bool _loading = false;
  String? _error;
  List<NetworkDescriptor> _networks = [];

  // Per-network deploy state (networkId → deploying).
  final Map<String, bool> _deploying = {};

  // Teardown-in-progress state.
  bool _tearingDown = false;

  // -------------------------------------------------------------------------

  Future<void> _loadNetworks() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final networks = await _api.listNetworkDescriptors();
      if (mounted) {
        setState(() {
          _networks = networks;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Failed to load saved networks';
          _loading = false;
        });
      }
    }
  }

  void _toggleNetworksSection() {
    final willExpand = !_networksExpanded;
    setState(() => _networksExpanded = willExpand);
    if (willExpand && _networks.isEmpty && !_loading) {
      _loadNetworks();
    }
  }

  Future<void> _teardown() async {
    // Confirm before wiping all existing CRDs.
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            Icon(Icons.delete_sweep_rounded,
                color: Colors.red[700], size: 22),
            const SizedBox(width: 8),
            const Text('Delete Deployment?'),
          ],
        ),
        content: const Text(
          'This will remove ALL existing network resources from the cluster:\n\n'
          '• TrafficTests\n'
          '• VyOSL3VPNs\n'
          '• VyOSUnderlays\n'
          '• VyOSInfrastructures\n\n'
          'Each resource will be fully deleted and confirmed gone before '
          'the next is removed. This action cannot be undone. Continue?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton.icon(
            onPressed: () => Navigator.of(ctx).pop(true),
            icon: const Icon(Icons.delete_sweep_rounded, size: 16),
            label: const Text('Delete'),
            style: FilledButton.styleFrom(
              backgroundColor: Colors.red[700],
            ),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    // Clear any existing terminal progress so the banner is fresh.
    final appstate = Provider.of<Appstate>(context, listen: false);
    if (appstate.deployProgress?.isTerminal ?? false) {
      appstate.clearDeployProgress();
    }

    setState(() => _tearingDown = true);

    final success = await _api.teardownDeployment();

    if (!mounted) return;
    setState(() => _tearingDown = false);

    if (!success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Failed to start teardown'),
          backgroundColor: Colors.red[700],
          duration: const Duration(seconds: 5),
        ),
      );
    }
    // On success the socket-driven progress banner takes over.
  }

  Future<void> _deploy(NetworkDescriptor network) async {
    // Confirm before wiping all existing CRDs.
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            Icon(Icons.warning_amber_rounded,
                color: Colors.orange[700], size: 22),
            const SizedBox(width: 8),
            const Text('Deploy Network?'),
          ],
        ),
        content: RichText(
          text: TextSpan(
            style: Theme.of(ctx).textTheme.bodyMedium,
            children: [
              const TextSpan(text: 'Deploying '),
              TextSpan(
                text: '"${network.name}"',
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              const TextSpan(
                text: ' will first remove ALL existing network resources '
                    'from the cluster:\n\n'
                    '• TrafficTests\n'
                    '• VyOSL3VPNs\n'
                    '• VyOSUnderlays\n'
                    '• VyOSInfrastructures\n\n'
                    'Each resource will be fully deleted before the next '
                    'is removed. This action cannot be undone. Continue?',
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton.icon(
            onPressed: () => Navigator.of(ctx).pop(true),
            icon: const Icon(Icons.rocket_launch_rounded, size: 16),
            label: const Text('Deploy'),
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xFF1976D2),
            ),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    // Clear any existing terminal progress so the banner is fresh.
    final appstate = Provider.of<Appstate>(context, listen: false);
    if (appstate.deployProgress?.isTerminal ?? false) {
      appstate.clearDeployProgress();
    }

    setState(() => _deploying[network.id] = true);

    final success = await _api.deployNetworkDescriptor(network.id);

    if (!mounted) return;
    setState(() => _deploying.remove(network.id));

    if (!success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Deploy of "${network.name}" failed to start'),
          backgroundColor: Colors.red[700],
          duration: const Duration(seconds: 5),
        ),
      );
    }
    // On success the socket-driven progress banner takes over — no snackbar needed.
  }

  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Drawer(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Header ─────────────────────────────────────────────────────────
          DrawerHeader(
            decoration: const BoxDecoration(color: _headerColor),
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                Row(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: Image.asset(
                        'assets/images/google.png',
                        width: 28,
                        height: 28,
                        fit: BoxFit.cover,
                      ),
                    ),
                    const SizedBox(width: 10),
                    const Expanded(
                      child: Text(
                        'Network Agent',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                const Text(
                  'Dashboard',
                  style: TextStyle(color: Colors.white70, fontSize: 13),
                ),
              ],
            ),
          ),

          // ── Nav items ──────────────────────────────────────────────────────
          Expanded(
            child: Consumer<Appstate>(
              builder: (context, appstate, _) {
                final progress = appstate.deployProgress;
                return Column(
                  children: [
                    Expanded(
                      child: ListView(
                        padding: EdgeInsets.zero,
                        children: [
                          // "Saved Networks" expandable tile
                          ListTile(
                            leading: const Icon(Icons.storage_rounded,
                                color: _accentColor),
                            title: const Text(
                              'Saved Networks',
                              style: TextStyle(fontWeight: FontWeight.w600),
                            ),
                            trailing: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                if (_loading)
                                  const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      valueColor:
                                          AlwaysStoppedAnimation<Color>(
                                              _accentColor),
                                    ),
                                  )
                                else
                                  IconButton(
                                    icon: const Icon(Icons.refresh, size: 18),
                                    color: _accentColor,
                                    tooltip: 'Refresh',
                                    onPressed: _loadNetworks,
                                    padding: EdgeInsets.zero,
                                    constraints: const BoxConstraints(),
                                  ),
                                const SizedBox(width: 4),
                                Icon(
                                  _networksExpanded
                                      ? Icons.expand_less
                                      : Icons.expand_more,
                                  color: _accentColor,
                                ),
                              ],
                            ),
                            onTap: _toggleNetworksSection,
                          ),

                          // Expanded network list
                          if (_networksExpanded)
                            _buildNetworkList(progress),

                          const Divider(height: 1),

                          // "Delete Deployment" tile
                          Consumer<Appstate>(
                            builder: (context, appstate, _) {
                              final operationActive =
                                  (appstate.deployProgress?.isActive ?? false) ||
                                  _tearingDown;
                              return ListTile(
                                leading: _tearingDown
                                    ? const SizedBox(
                                        width: 20,
                                        height: 20,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          valueColor:
                                              AlwaysStoppedAnimation<Color>(
                                                  Colors.red),
                                        ),
                                      )
                                    : Icon(
                                        Icons.delete_sweep_rounded,
                                        color: operationActive
                                            ? Colors.grey
                                            : Colors.red[700],
                                      ),
                                title: Text(
                                  'Delete Deployment',
                                  style: TextStyle(
                                    fontWeight: FontWeight.w600,
                                    color: operationActive
                                        ? Colors.grey
                                        : Colors.red[700],
                                  ),
                                ),
                                subtitle: operationActive && !_tearingDown
                                    ? const Text(
                                        'Operation in progress…',
                                        style: TextStyle(fontSize: 11),
                                      )
                                    : null,
                                enabled: !operationActive,
                                onTap: operationActive ? null : _teardown,
                              );
                            },
                          ),

                          const Divider(height: 1),

                          // "Settings" tile
                          ListTile(
                            leading: const Icon(Icons.settings,
                                color: _accentColor),
                            title: const Text(
                              'Settings',
                              style: TextStyle(fontWeight: FontWeight.w600),
                            ),
                            onTap: () {
                              Navigator.of(context).pop(); // close drawer
                              Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (context) => const SettingsScreen(),
                                ),
                              );
                            },
                          ),
                        ],
                      ),
                    ),

                    // ── Deploy progress banner (pinned at bottom) ───────────
                    if (progress != null)
                      _DeployProgressBanner(
                        progress: progress,
                        onDismiss: () => appstate.clearDeployProgress(),
                      ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  // ── Network list ───────────────────────────────────────────────────────────

  Widget _buildNetworkList(DeployProgress? progress) {
    if (_loading && _networks.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 24),
        child: Center(child: CircularProgressIndicator()),
      );
    }

    if (_error != null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Text(
              _error!,
              style: const TextStyle(color: Colors.red),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            TextButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              onPressed: _loadNetworks,
            ),
          ],
        ),
      );
    }

    if (_networks.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(horizontal: 16, vertical: 20),
        child: Text(
          'No saved networks found.',
          style: TextStyle(color: Colors.black54),
          textAlign: TextAlign.center,
        ),
      );
    }

    return Column(
      children: _networks
          .map((network) => _NetworkCard(
                network: network,
                deploying: _deploying[network.id] ?? false,
                // Disable the button while a deploy for this network is active.
                deployDisabled:
                    (progress?.networkId == network.id &&
                        (progress?.isActive ?? false)) ||
                    (_deploying[network.id] ?? false),
                onDeploy: () => _deploy(network),
              ))
          .toList(),
    );
  }
}

// ── Per-network card ─────────────────────────────────────────────────────────

class _NetworkCard extends StatelessWidget {
  const _NetworkCard({
    required this.network,
    required this.deploying,
    required this.deployDisabled,
    required this.onDeploy,
  });

  final NetworkDescriptor network;
  final bool deploying;
  final bool deployDisabled;
  final VoidCallback onDeploy;

  static const _accentColor = Color(0xFF1976D2);

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        color: const Color(0xFFF5F9FF),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFBBDEFB)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Name row
            Row(
              children: [
                const Icon(Icons.lan_rounded, size: 16, color: _accentColor),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    network.name,
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 14,
                      color: Color(0xFF0D47A1),
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),

            // Description
            if (network.description.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                network.description,
                style:
                    const TextStyle(fontSize: 12, color: Colors.black54),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ],

            // Updated at
            const SizedBox(height: 4),
            Row(
              children: [
                const Icon(Icons.access_time, size: 11, color: Colors.black38),
                const SizedBox(width: 3),
                Text(
                  network.formattedUpdatedAt,
                  style:
                      const TextStyle(fontSize: 11, color: Colors.black38),
                ),
              ],
            ),

            // Deploy button
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerRight,
              child: deploying
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : OutlinedButton.icon(
                      onPressed: deployDisabled ? null : onDeploy,
                      icon: const Icon(Icons.rocket_launch_rounded, size: 14),
                      label: const Text('Deploy',
                          style: TextStyle(fontSize: 12)),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: _accentColor,
                        side: BorderSide(
                          color: deployDisabled
                              ? Colors.grey.shade400
                              : _accentColor,
                        ),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 4),
                        minimumSize: Size.zero,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Deploy progress banner ────────────────────────────────────────────────────

class _DeployProgressBanner extends StatelessWidget {
  const _DeployProgressBanner({
    required this.progress,
    required this.onDismiss,
  });

  final DeployProgress progress;
  final VoidCallback onDismiss;

  Color get _bgColor {
    if (progress.stage == 'deploy_complete') return const Color(0xFF1B5E20);
    if (progress.stage == 'teardown_only_complete') return const Color(0xFF1B5E20);
    if (progress.stage == 'deploy_failed') return Colors.red.shade800;
    if (progress.stage == 'teardown_failed') return Colors.red.shade800;
    return const Color(0xFF0D47A1);
  }

  IconData get _icon {
    if (progress.stage == 'deploy_complete') return Icons.check_circle_outline;
    if (progress.stage == 'teardown_only_complete') return Icons.check_circle_outline;
    if (progress.stage == 'deploy_failed') return Icons.error_outline;
    if (progress.stage == 'teardown_failed') return Icons.error_outline;
    if (progress.isTearingDown) return Icons.delete_sweep_outlined;
    return Icons.rocket_launch_outlined;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: _bgColor,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Status icon or spinner
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: progress.isActive && !progress.isTerminal
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white70,
                      ),
                    )
                  : Icon(_icon, color: Colors.white, size: 16),
            ),
            const SizedBox(width: 8),

            // Message
            Expanded(
              child: Text(
                progress.displayMessage,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                ),
              ),
            ),

            // Dismiss button (only when terminal)
            if (progress.isTerminal)
              GestureDetector(
                onTap: onDismiss,
                child: const Padding(
                  padding: EdgeInsets.only(left: 4),
                  child: Icon(Icons.close, color: Colors.white54, size: 16),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
