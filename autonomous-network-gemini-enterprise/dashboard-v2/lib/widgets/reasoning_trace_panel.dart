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
import '../agent/agent_engine_provider.dart';
import '../agent/reasoning_event.dart';

/// Collapsible panel that displays the agent's live reasoning trace —
/// thought steps, tool calls, and tool responses — as they stream in.
/// Auto-expands when the first reasoning event arrives; collapsed otherwise.
class ReasoningTracePanel extends StatefulWidget {
  final AgentEngineProvider provider;

  const ReasoningTracePanel({super.key, required this.provider});

  @override
  State<ReasoningTracePanel> createState() => _ReasoningTracePanelState();
}

class _ReasoningTracePanelState extends State<ReasoningTracePanel> {
  bool _expanded = false;
  final ScrollController _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    widget.provider.addListener(_onProviderUpdate);
  }

  @override
  void dispose() {
    widget.provider.removeListener(_onProviderUpdate);
    _scrollController.dispose();
    super.dispose();
  }

  void _onProviderUpdate() {
    // Auto-expand when the first reasoning event arrives — not before,
    // so we don't show an empty expanded panel while the agent is thinking.
    if (widget.provider.currentReasoningEvents.isNotEmpty && !_expanded) {
      if (mounted) setState(() => _expanded = true);
    }
    // Scroll to the latest event as they arrive.
    if (_expanded && _scrollController.hasClients) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 150),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.provider,
      builder: (context, _) {
        final events = widget.provider.currentReasoningEvents;
        final isGenerating = widget.provider.isGenerating;

        // Hide completely when not generating and nothing captured.
        if (!isGenerating && events.isEmpty) return const SizedBox.shrink();

        return AnimatedSize(
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeInOut,
          alignment: Alignment.topCenter,
          child: Container(
            margin: const EdgeInsets.fromLTRB(8, 0, 8, 4),
            decoration: BoxDecoration(
              color: const Color(0xFFF8FBFF),
              border: Border.all(color: const Color(0xFFBBDEFB)),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // ── Header row ────────────────────────────────────────────
                InkWell(
                  borderRadius: BorderRadius.circular(8),
                  onTap: events.isEmpty
                      ? null
                      : () => setState(() => _expanded = !_expanded),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 8),
                    child: Row(
                      children: [
                        if (isGenerating)
                          const SizedBox(
                            width: 14,
                            height: 14,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              valueColor: AlwaysStoppedAnimation<Color>(
                                  Color(0xFF0D47A1)),
                            ),
                          )
                        else
                          const Icon(Icons.psychology_outlined,
                              size: 16, color: Color(0xFF0D47A1)),
                        const SizedBox(width: 8),
                        Text(
                          _headerLabel(isGenerating, events.length),
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF0D47A1),
                          ),
                        ),
                        const Spacer(),
                        if (events.isNotEmpty)
                          Icon(
                            _expanded
                                ? Icons.expand_less
                                : Icons.expand_more,
                            size: 16,
                            color: const Color(0xFF0D47A1),
                          ),
                      ],
                    ),
                  ),
                ),
                // ── Events list (visible when expanded) ───────────────────
                if (_expanded && events.isNotEmpty)
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 180),
                    child: ListView.builder(
                      controller: _scrollController,
                      shrinkWrap: true,
                      padding: const EdgeInsets.only(bottom: 8),
                      itemCount: events.length,
                      itemBuilder: (context, i) =>
                          _buildEventTile(events[i]),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  String _headerLabel(bool isGenerating, int count) {
    if (isGenerating) {
      return count == 0
          ? 'Reasoning…'
          : 'Reasoning…  ($count step${count == 1 ? '' : 's'})';
    }
    return 'Reasoning  •  $count step${count == 1 ? '' : 's'}';
  }

  Widget _buildEventTile(ReasoningEvent event) {
    switch (event.type) {
      case ReasoningEventType.thought:
        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 2, 12, 2),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('💭', style: TextStyle(fontSize: 11)),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  event.label,
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF546E7A),
                    fontStyle: FontStyle.italic,
                  ),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        );
      case ReasoningEventType.functionCall:
        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 2, 12, 2),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('🔧', style: TextStyle(fontSize: 11)),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      event.label,
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFF0D47A1),
                        fontFamily: 'monospace',
                      ),
                    ),
                    if (event.detail != null)
                      Text(
                        _truncate(event.detail!, 120),
                        style: const TextStyle(
                            fontSize: 10, color: Color(0xFF78909C)),
                      ),
                  ],
                ),
              ),
            ],
          ),
        );
      case ReasoningEventType.functionResponse:
        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 2, 12, 2),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('📊', style: TextStyle(fontSize: 11)),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  '${event.label} → ${_truncate(event.detail ?? 'ok', 100)}',
                  style:
                      const TextStyle(fontSize: 11, color: Color(0xFF2E7D32)),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        );
    }
  }

  String _truncate(String s, int max) =>
      s.length > max ? '${s.substring(0, max)}…' : s;
}
