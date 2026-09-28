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

class CollapsibleJsonViewer extends StatelessWidget {
  final dynamic json;
  final int initialDepth;

  const CollapsibleJsonViewer({
    super.key, 
    required this.json,
    this.initialDepth = 1,
  });

  @override
  Widget build(BuildContext context) {
    return _buildJsonNode(json, 0);
  }

  Widget _buildJsonNode(dynamic content, int depth) {
    if (content is Map) {
      return _buildObjectNode(content as Map<String, dynamic>, depth);
    } else if (content is List) {
      return _buildArrayNode(content, depth);
    } else {
      return _buildPrimitiveNode(content);
    }
  }

  Widget _buildObjectNode(Map<String, dynamic> object, int depth) {
    if (object.isEmpty) {
      return const Text('{}', style: TextStyle(color: Colors.grey));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: object.entries.map((entry) {
        final isComplex = entry.value is Map || entry.value is List;
        
        if (isComplex) {
          return ExpansionTile(
            tilePadding: EdgeInsets.zero,
            childrenPadding: const EdgeInsets.only(left: 16.0),
            dense: true,
            initiallyExpanded: depth < initialDepth,
            title: Row(
              children: [
                Text('${entry.key}: ', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.blue)),
                Text(
                  entry.value is Map ? '{...}' : '[...]',
                  style: const TextStyle(color: Colors.grey),
                ),
              ],
            ),
            children: [_buildJsonNode(entry.value, depth + 1)],
          );
        } else {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 2.0),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${entry.key}: ', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.blue)),
                Expanded(child: _buildPrimitiveNode(entry.value)),
              ],
            ),
          );
        }
      }).toList(),
    );
  }

  Widget _buildArrayNode(List<dynamic> array, int depth) {
    if (array.isEmpty) {
      return const Text('[]', style: TextStyle(color: Colors.grey));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: array.asMap().entries.map((entry) {
        final isComplex = entry.value is Map || entry.value is List;
        
        if (isComplex) {
          return ExpansionTile(
            tilePadding: EdgeInsets.zero,
            childrenPadding: const EdgeInsets.only(left: 16.0),
            dense: true,
            initiallyExpanded: depth < initialDepth,
            title: Row(
              children: [
                Text('[${entry.key}]: ', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.purple)),
                Text(
                  entry.value is Map ? '{...}' : '[...]',
                  style: const TextStyle(color: Colors.grey),
                ),
              ],
            ),
            children: [_buildJsonNode(entry.value, depth + 1)],
          );
        } else {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 2.0),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('[${entry.key}]: ', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.purple)),
                Expanded(child: _buildPrimitiveNode(entry.value)),
              ],
            ),
          );
        }
      }).toList(),
    );
  }

  Widget _buildPrimitiveNode(dynamic value) {
    if (value == null) {
      return const Text('null', style: TextStyle(color: Colors.red));
    } else if (value is num) {
      return Text(value.toString(), style: const TextStyle(color: Colors.green));
    } else if (value is bool) {
      return Text(value.toString(), style: const TextStyle(color: Colors.orange));
    } else {
      return Text('"$value"', style: const TextStyle(color: Colors.brown));
    }
  }
}
