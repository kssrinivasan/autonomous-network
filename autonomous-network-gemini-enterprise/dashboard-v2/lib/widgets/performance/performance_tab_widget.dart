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
import '../../models/panel_type.dart';
import '../../screens/full_screen_panel_view.dart';
import 'flow_performance_graph.dart' show TimePeriod, TimePeriodExt, FlowPerformanceGraph;
import 'router_performance_graph.dart';

/// A tabbed performance panel inspired by the GCP metrics UI.
///
/// Layout:
///   ┌──────────────────────────────────────────────────────────┐
///   │  [Flow Performance]  [Router Performance]   Last 15m ▼  │
///   │  ─────────────────                                       │
///   │                                                          │
///   │   (per-tab controls: entity selector, metrics, refresh)  │
///   │                                                          │
///   │                    LINE CHART                            │
///   │                                                          │
///   │   legend …                                               │
///   └──────────────────────────────────────────────────────────┘
class PerformanceTabWidget extends StatefulWidget {
  final bool isFullScreen;

  const PerformanceTabWidget({super.key, this.isFullScreen = false});

  @override
  State<PerformanceTabWidget> createState() => _PerformanceTabWidgetState();
}

class _PerformanceTabWidgetState extends State<PerformanceTabWidget>
    with SingleTickerProviderStateMixin {
  /// Shared time period — applies to whichever tab is active.
  TimePeriod _period = TimePeriod.last15m;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return DefaultTabController(
      length: 2,
      child: Builder(
        builder: (context) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // ── Tab bar row ──────────────────────────────────────────────
              Container(
                decoration: BoxDecoration(
                  color: Theme.of(context).cardColor,
                  border: Border(
                    bottom: BorderSide(color: Colors.grey.shade200, width: 1),
                  ),
                ),
                child: Row(
                  children: [
                    // Tabs
                    Expanded(
                      child: TabBar(
                        isScrollable: false,
                        labelColor: colorScheme.primary,
                        unselectedLabelColor: Colors.grey.shade600,
                        labelStyle: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                        ),
                        unselectedLabelStyle: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.normal,
                        ),
                        indicator: UnderlineTabIndicator(
                          borderSide: BorderSide(
                            color: colorScheme.primary,
                            width: 2.5,
                          ),
                          insets: const EdgeInsets.symmetric(horizontal: 0),
                        ),
                        indicatorSize: TabBarIndicatorSize.tab,
                        dividerColor: Colors.transparent,
                        tabs: const [
                          Tab(text: 'Flow Performance'),
                          Tab(text: 'Router Performance'),
                        ],
                      ),
                    ),

                    // ── Shared time period dropdown (top-right, GCP style) ─
                    Padding(
                      padding: const EdgeInsets.only(right: 4),
                      child: _buildPeriodDropdown(context),
                    ),

                    // ── Full-screen / exit button (matches ChatPanel style) ─
                    IconButton(
                      icon: Icon(
                        widget.isFullScreen
                            ? Icons.fullscreen_exit
                            : Icons.fullscreen,
                        color: const Color(0xFF0D47A1),
                      ),
                      tooltip: widget.isFullScreen
                          ? 'Exit full screen'
                          : 'Expand to full screen',
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(),
                      onPressed: () {
                        if (widget.isFullScreen) {
                          Navigator.of(context).pop();
                        } else {
                          Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) => const FullScreenPanelView(
                                panelType: PanelType.performance,
                              ),
                            ),
                          );
                        }
                      },
                    ),
                    const SizedBox(width: 8),
                  ],
                ),
              ),

              // ── Tab content ──────────────────────────────────────────────
              Expanded(
                child: TabBarView(
                  children: [
                    // Flow tab — keeps state alive when switching tabs
                    _KeepAliveTab(
                      child: FlowPerformanceGraph(
                        period: _period,
                        onPeriodChanged: (p) => setState(() => _period = p),
                      ),
                    ),
                    // Router tab
                    _KeepAliveTab(
                      child: RouterPerformanceGraph(
                        period: _period,
                        onPeriodChanged: (p) => setState(() => _period = p),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _buildPeriodDropdown(BuildContext context) {
    return DropdownButton<TimePeriod>(
      value: _period,
      isDense: true,
      icon: const Icon(Icons.arrow_drop_down, size: 18),
      style: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w500,
        color: Theme.of(context).colorScheme.primary,
      ),
      underline: const SizedBox.shrink(),
      items: TimePeriod.values
          .map((TimePeriod p) =>
              DropdownMenuItem<TimePeriod>(value: p, child: Text(p.label)))
          .toList(),
      onChanged: (TimePeriod? p) {
        if (p != null) setState(() => _period = p);
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Helper: keeps a tab's widget tree alive when the tab is not selected.
// ---------------------------------------------------------------------------

class _KeepAliveTab extends StatefulWidget {
  final Widget child;
  const _KeepAliveTab({required this.child});

  @override
  State<_KeepAliveTab> createState() => _KeepAliveTabState();
}

class _KeepAliveTabState extends State<_KeepAliveTab>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return widget.child;
  }
}
