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

/// A generic multi-select dropdown widget that renders as a compact button.
/// Tapping it opens a [PopupMenuButton]-based checklist — Flutter's built-in
/// popup menu handles all the overlay lifecycle and rebuilds correctly.
class MultiSelectDropdown<T> extends StatefulWidget {
  final String label;
  final IconData icon;
  final List<T> items;

  /// Currently selected items.  Pass `null` to indicate "all selected" mode.
  final Set<T>? selected;

  /// Optional label for the "select all" entry at the top of the list.
  final String? allLabel;

  /// Optional function to convert an item to a display string.
  final String Function(T)? itemLabel;

  /// Called when the user taps the "all" entry.
  final VoidCallback? onAllToggled;

  /// Called when the user toggles an individual item.
  final void Function(T) onItemToggled;

  const MultiSelectDropdown({
    super.key,
    required this.label,
    required this.icon,
    required this.items,
    required this.selected,
    this.allLabel,
    this.itemLabel,
    this.onAllToggled,
    required this.onItemToggled,
  });

  @override
  State<MultiSelectDropdown<T>> createState() => _MultiSelectDropdownState<T>();
}

class _MultiSelectDropdownState<T> extends State<MultiSelectDropdown<T>> {
  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<_MenuAction<T>>(
      tooltip: '',
      offset: const Offset(0, 36),
      constraints: const BoxConstraints(minWidth: 200, maxWidth: 280),
      // Rebuild the menu every time it opens so checkboxes are fresh.
      itemBuilder: (_) {
        final items = <PopupMenuEntry<_MenuAction<T>>>[];

        // "All" entry
        if (widget.allLabel != null && widget.onAllToggled != null) {
          items.add(
            CheckedPopupMenuItem<_MenuAction<T>>(
              value: _MenuAction<T>.all(),
              checked: widget.selected == null,
              child: Text(widget.allLabel!,
                  style: const TextStyle(fontSize: 12)),
            ),
          );
          items.add(const PopupMenuDivider(height: 1));
        }

        // Individual items
        for (final item in widget.items) {
          final lbl = widget.itemLabel != null
              ? widget.itemLabel!(item)
              : item.toString();
          final checked = widget.selected?.contains(item) ?? false;
          items.add(
            CheckedPopupMenuItem<_MenuAction<T>>(
              value: _MenuAction<T>.item(item),
              checked: checked,
              child: Text(lbl, style: const TextStyle(fontSize: 12)),
            ),
          );
        }

        return items;
      },
      onSelected: (action) {
        if (action.isAll) {
          widget.onAllToggled?.call();
        } else {
          widget.onItemToggled(action.item as T);
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
    );
  }
}

// ---------------------------------------------------------------------------
// Simple discriminated union for menu actions
// ---------------------------------------------------------------------------

class _MenuAction<T> {
  final bool isAll;
  final T? item;

  const _MenuAction.all()
      : isAll = true,
        item = null;

  const _MenuAction.item(this.item) : isAll = false;
}
