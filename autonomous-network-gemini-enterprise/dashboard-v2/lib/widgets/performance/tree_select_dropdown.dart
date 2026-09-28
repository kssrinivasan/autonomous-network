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

// ---------------------------------------------------------------------------
// Data model
// ---------------------------------------------------------------------------

/// A single group with a label and its children.
class TreeGroup<T> {
  final String label;
  final List<(T, String)> children; // (item, displayLabel)

  const TreeGroup({required this.label, required this.children});
}

// ---------------------------------------------------------------------------
// TreeSelectDropdown
// ---------------------------------------------------------------------------

/// A compact dropdown button that opens a grouped, expandable tree of
/// checkboxes.  Groups can be expanded/collapsed and selected as a whole;
/// individual children can be toggled independently.
///
/// When [selected] is `null` the widget is in "all selected" mode.
class TreeSelectDropdown<T> extends StatefulWidget {
  final String label;
  final IconData icon;
  final List<TreeGroup<T>> groups;

  /// Currently selected items.  Pass `null` to indicate "all selected" mode.
  final Set<T>? selected;

  /// Called when the user taps the top-level "All" entry.
  final VoidCallback? onAllToggled;

  /// Called when the user toggles an individual item.
  final void Function(T) onItemToggled;

  /// Called when the user toggles an entire group (select/deselect all children).
  final void Function(TreeGroup<T>, bool select) onGroupToggled;

  const TreeSelectDropdown({
    super.key,
    required this.label,
    required this.icon,
    required this.groups,
    required this.selected,
    this.onAllToggled,
    required this.onItemToggled,
    required this.onGroupToggled,
  });

  @override
  State<TreeSelectDropdown<T>> createState() => _TreeSelectDropdownState<T>();
}

class _TreeSelectDropdownState<T> extends State<TreeSelectDropdown<T>> {
  final LayerLink _layerLink = LayerLink();
  OverlayEntry? _overlay;

  // Which groups are currently expanded in the popup.
  final Set<String> _expanded = {};

  void _open() {
    if (_overlay != null) return;
    final renderBox = context.findRenderObject() as RenderBox;
    final size = renderBox.size;
    // Compute the absolute position of the button so the popup can calculate
    // how much vertical space is available below it.
    final buttonOffset = renderBox.localToGlobal(Offset.zero);

    _overlay = OverlayEntry(
      builder: (_) => _TreePopup<T>(
        layerLink: _layerLink,
        buttonSize: size,
        buttonOffset: buttonOffset,
        groups: widget.groups,
        selected: widget.selected,
        expanded: _expanded,
        onAllToggled: () {
          widget.onAllToggled?.call();
          _close();
        },
        onGroupToggled: (g, select) {
          widget.onGroupToggled(g, select);
          // Rebuild button label, then rebuild overlay after parent setState.
          setState(() {});
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) setState(() {});
            _overlay?.markNeedsBuild();
          });
        },
        onItemToggled: (item) {
          widget.onItemToggled(item);
          // Rebuild button label, then rebuild overlay after parent setState.
          setState(() {});
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) setState(() {});
            _overlay?.markNeedsBuild();
          });
        },
        onExpandToggled: (label) {
          setState(() {
            if (_expanded.contains(label)) {
              _expanded.remove(label);
            } else {
              _expanded.add(label);
            }
          });
          _overlay?.markNeedsBuild();
        },
        onClose: _close,
      ),
    );

    Overlay.of(context).insert(_overlay!);
  }

  void _close() {
    _overlay?.remove();
    _overlay = null;
  }

  @override
  void dispose() {
    _close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return CompositedTransformTarget(
      link: _layerLink,
      child: GestureDetector(
        onTap: () {
          if (_overlay != null) {
            _close();
          } else {
            _open();
          }
        },
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: Colors.grey.shade300),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(widget.icon, size: 14, color: const Color(0xFF0D47A1)),
              const SizedBox(width: 4),
              Text(
                widget.label,
                style: const TextStyle(fontSize: 12, color: Color(0xFF0D47A1)),
              ),
              const SizedBox(width: 4),
              const Icon(Icons.arrow_drop_down,
                  size: 16, color: Color(0xFF0D47A1)),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Popup overlay
// ---------------------------------------------------------------------------

class _TreePopup<T> extends StatefulWidget {
  final LayerLink layerLink;
  final Size buttonSize;
  final Offset buttonOffset;
  final List<TreeGroup<T>> groups;
  final Set<T>? selected;
  final Set<String> expanded;
  final VoidCallback onAllToggled;
  final void Function(TreeGroup<T>, bool) onGroupToggled;
  final void Function(T) onItemToggled;
  final void Function(String) onExpandToggled;
  final VoidCallback onClose;

  const _TreePopup({
    required this.layerLink,
    required this.buttonSize,
    required this.buttonOffset,
    required this.groups,
    required this.selected,
    required this.expanded,
    required this.onAllToggled,
    required this.onGroupToggled,
    required this.onItemToggled,
    required this.onExpandToggled,
    required this.onClose,
  });

  @override
  State<_TreePopup<T>> createState() => _TreePopupState<T>();
}

class _TreePopupState<T> extends State<_TreePopup<T>> {
  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        // Dismiss on tap outside
        Positioned.fill(
          child: GestureDetector(
            onTap: widget.onClose,
            behavior: HitTestBehavior.translucent,
            child: const SizedBox.expand(),
          ),
        ),
        // The popup itself.
        // maxHeight = distance from the bottom of the button to the bottom of
        // the screen, minus 16 px padding so it never touches the edge.
        CompositedTransformFollower(
          link: widget.layerLink,
          showWhenUnlinked: false,
          offset: Offset(0, widget.buttonSize.height + 4),
          child: Align(
            alignment: Alignment.topLeft,
            child: ConstrainedBox(
              constraints: BoxConstraints(
                minWidth: 220,
                maxWidth: 300,
                maxHeight: (MediaQuery.of(context).size.height
                        - widget.buttonOffset.dy
                        - widget.buttonSize.height
                        - 4   // gap between button and popup
                        - 16) // bottom padding
                    .clamp(100.0, double.infinity),
              ),
              child: Material(
                elevation: 8,
                borderRadius: BorderRadius.circular(8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Scrollbar(
                    thumbVisibility: true,
                    child: SingleChildScrollView(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: _buildRows(),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  List<Widget> _buildRows() {
    final rows = <Widget>[];

    // "All" row
    rows.add(_CheckRow(
      label: 'All',
      checked: widget.selected == null,
      indeterminate: false,
      indent: 0,
      onTap: widget.onAllToggled,
    ));
    rows.add(const Divider(height: 1, thickness: 1));

    for (final group in widget.groups) {
      if (group.children.isEmpty) continue;

      final isExpanded = widget.expanded.contains(group.label);
      final allItems = group.children.map((c) => c.$1).toSet();
      final selectedInGroup = widget.selected == null
          ? allItems
          : allItems.intersection(widget.selected!);
      final allSelected = selectedInGroup.length == allItems.length;
      final someSelected =
          selectedInGroup.isNotEmpty && selectedInGroup.length < allItems.length;

      // Group header row.
      // The row is split into independent tap zones so that clicking the
      // checkbox only toggles group selection, while clicking the chevron or
      // label only expands/collapses.
      rows.add(
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(
            children: [
              // ── Chevron: expand / collapse ──────────────────────────────
              GestureDetector(
                onTap: () => widget.onExpandToggled(group.label),
                behavior: HitTestBehavior.opaque,
                child: Padding(
                  padding: const EdgeInsets.all(2),
                  child: Icon(
                    isExpanded ? Icons.expand_more : Icons.chevron_right,
                    size: 16,
                    color: Colors.grey.shade600,
                  ),
                ),
              ),
              const SizedBox(width: 4),
              // ── Checkbox: select / deselect all children ─────────────────
              // When in "all selected" mode (selected == null), allSelected is
              // always true. We want clicking the group checkbox to *deselect*
              // that group (i.e. the parent seeds all items then removes this
              // group's children). Pass select=false so the parent removes them.
              GestureDetector(
                onTap: () => widget.onGroupToggled(
                  group,
                  widget.selected == null ? false : !allSelected,
                ),
                behavior: HitTestBehavior.opaque,
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: IgnorePointer(
                    child: Checkbox(
                      value: someSelected ? null : allSelected,
                      tristate: true,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      onChanged: (_) {},
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              // ── Label: expand / collapse ─────────────────────────────────
              Expanded(
                child: GestureDetector(
                  onTap: () => widget.onExpandToggled(group.label),
                  behavior: HitTestBehavior.opaque,
                  child: Text(
                    group.label,
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              Text(
                '${selectedInGroup.length}/${allItems.length}',
                style: TextStyle(fontSize: 10, color: Colors.grey.shade500),
              ),
            ],
          ),
        ),
      );

      // Children (only when expanded)
      if (isExpanded) {
        for (final (item, childLabel) in group.children) {
          final checked =
              widget.selected == null || widget.selected!.contains(item);
          rows.add(_CheckRow(
            label: childLabel,
            checked: checked,
            indeterminate: false,
            indent: 32,
            onTap: () => widget.onItemToggled(item),
          ));
        }
      }
    }

    return rows;
  }
}

// ---------------------------------------------------------------------------
// Simple checkbox row
// ---------------------------------------------------------------------------

class _CheckRow extends StatelessWidget {
  final String label;
  final bool checked;
  final bool indeterminate;
  final double indent;
  final VoidCallback onTap;

  const _CheckRow({
    required this.label,
    required this.checked,
    required this.indeterminate,
    required this.indent,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: EdgeInsets.only(left: indent, right: 8, top: 4, bottom: 4),
        child: Row(
          children: [
            SizedBox(
              width: 20,
              height: 20,
              child: IgnorePointer(
                child: Checkbox(
                  value: indeterminate ? null : checked,
                  tristate: indeterminate,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  onChanged: (_) {},
                ),
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(label, style: const TextStyle(fontSize: 12)),
            ),
          ],
        ),
      ),
    );
  }
}
