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
import '../models/network_node.dart';

IconData getNodeIcon(NetworkNode node) {
  switch (node.type) {
    case NodeType.P:
      return Icons.hub;
    case NodeType.PA:
      return Icons.account_tree;
    case NodeType.PE:
      return Icons.router;
    case NodeType.AR:
      return Icons.router_outlined;
    case NodeType.CE:
      return Icons.business;
    case NodeType.RR:
      return Icons.sync_alt;
    case NodeType.Device:
      return Icons.devices; // Icon for end devices
  }
}

Color getNodeColor(NetworkNode node) {
  switch (node.type) {
    case NodeType.P:
      return const Color(0xFF0D47A1); // Dark blue (Core)
    case NodeType.PA:
      return const Color(0xFF1565C0); // Medium-dark blue (Pre-Aggregation)
    case NodeType.PE:
      return const Color(0xFF1976D2); // Medium blue (Provider Edge)
    case NodeType.AR:
      return const Color(0xFF2196F3); // Medium-light blue (Access)
    case NodeType.CE:
      return const Color(0xFF42A5F5); // Light blue (Customer Edge)
    case NodeType.RR:
      return const Color(0xFF6A1B9A); // Purple (Route Reflector)
    case NodeType.Device:
      return const Color(0xFF66BB6A); // Green (End devices)
  }
}
