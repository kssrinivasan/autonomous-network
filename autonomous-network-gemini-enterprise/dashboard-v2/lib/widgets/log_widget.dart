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
import '../models/log_entry.dart';
import '../models/panel_type.dart';
import '../screens/full_screen_panel_view.dart';
import 'performance/flow_performance_graph.dart' show TimePeriod, TimePeriodExt;

/// Filter options for the log source dropdown.
enum LogSourceFilter {
  all,
  vyosSyslog,
  automation,
}

extension LogSourceFilterExtension on LogSourceFilter {
  String get label {
    switch (this) {
      case LogSourceFilter.all:
        return 'All';
      case LogSourceFilter.vyosSyslog:
        return 'VyOS Syslog';
      case LogSourceFilter.automation:
        return 'Automation';
    }
  }

  /// The value sent to the backend as `source_filter`.
  String get backendValue {
    switch (this) {
      case LogSourceFilter.all:
        return 'all';
      case LogSourceFilter.vyosSyslog:
        return 'vyos_syslog';
      case LogSourceFilter.automation:
        return 'automation';
    }
  }
}

/// Filter options for the log severity dropdown.
enum LogSeverityFilter {
  all,
  critical,
  error,
  warning,
  info,
  debug,
}

extension LogSeverityFilterExtension on LogSeverityFilter {
  String get label {
    switch (this) {
      case LogSeverityFilter.all:
        return 'All Severities';
      case LogSeverityFilter.critical:
        return 'Critical';
      case LogSeverityFilter.error:
        return 'Error';
      case LogSeverityFilter.warning:
        return 'Warning';
      case LogSeverityFilter.info:
        return 'Info';
      case LogSeverityFilter.debug:
        return 'Debug';
    }
  }

  /// The value sent to the backend as `severity_filter`.
  String get backendValue {
    switch (this) {
      case LogSeverityFilter.all:
        return 'all';
      case LogSeverityFilter.critical:
        return 'critical';
      case LogSeverityFilter.error:
        return 'error';
      case LogSeverityFilter.warning:
        return 'warning';
      case LogSeverityFilter.info:
        return 'info';
      case LogSeverityFilter.debug:
        return 'debug';
    }
  }

  Color get indicatorColor {
    switch (this) {
      case LogSeverityFilter.all:
        return Colors.grey;
      case LogSeverityFilter.critical:
        return Colors.red;
      case LogSeverityFilter.error:
        return Colors.orange;
      case LogSeverityFilter.warning:
        return Colors.yellow;
      case LogSeverityFilter.info:
        return Colors.green;
      case LogSeverityFilter.debug:
        return Colors.blue;
    }
  }
}

class LogWidget extends StatefulWidget {
  final List<LogEntry> logs;
  final socket;
  final bool isLoading;
  final bool isFullScreen;

  const LogWidget({
    super.key,
    required this.socket,
    required this.logs,
    this.isLoading = false,
    this.isFullScreen = false,
  });

  @override
  State<LogWidget> createState() => _LogWidgetState();
}

class _LogWidgetState extends State<LogWidget> {
  LogSourceFilter _selectedSourceFilter = LogSourceFilter.all;
  LogSeverityFilter _selectedSeverityFilter = LogSeverityFilter.all;
  int _rowLimit = 50;
  TimePeriod? _selectedTimePeriod;

  static const List<int> _rowLimitOptions = [50, 100, 200];

  void _resetLogs(BuildContext context) {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.resetLogs();
  }

  void _onSourceFilterChanged(LogSourceFilter? newFilter) {
    if (newFilter == null || newFilter == _selectedSourceFilter) return;
    setState(() {
      _selectedSourceFilter = newFilter;
    });
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.toggleLogs(
      true,
      sourceFilter: newFilter.backendValue,
      severityFilter: _selectedSeverityFilter.backendValue,
      rowLimit: _rowLimit,
      timePeriod: _selectedTimePeriod,
    );
  }

  /// Wraps a dropdown in a white rounded-rectangle pill with a subtle border.
  Widget _buildDropdownContainer({required Widget child}) {
    return Container(
      height: 30,
      padding: const EdgeInsets.symmetric(horizontal: 10.0),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(6.0),
        border: Border.all(color: const Color(0xFF90CAF9), width: 1.0),
      ),
      child: child,
    );
  }

  void _onSeverityFilterChanged(LogSeverityFilter? newFilter) {
    if (newFilter == null || newFilter == _selectedSeverityFilter) return;
    setState(() {
      _selectedSeverityFilter = newFilter;
    });
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.toggleLogs(
      true,
      sourceFilter: _selectedSourceFilter.backendValue,
      severityFilter: newFilter.backendValue,
      rowLimit: _rowLimit,
      timePeriod: _selectedTimePeriod,
    );
  }

  void _onRowLimitChanged(int? newLimit) {
    if (newLimit == null || newLimit == _rowLimit) return;
    setState(() {
      _rowLimit = newLimit;
    });
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.toggleLogs(
      true,
      sourceFilter: _selectedSourceFilter.backendValue,
      severityFilter: _selectedSeverityFilter.backendValue,
      rowLimit: newLimit,
      timePeriod: _selectedTimePeriod,
    );
  }

  void _onTimePeriodChanged(TimePeriod? newPeriod) {
    if (newPeriod == _selectedTimePeriod) return;
    setState(() {
      _selectedTimePeriod = newPeriod;
    });
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.toggleLogs(
      true,
      sourceFilter: _selectedSourceFilter.backendValue,
      severityFilter: _selectedSeverityFilter.backendValue,
      rowLimit: _rowLimit,
      timePeriod: newPeriod,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 8.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Source and severity filter dropdowns (positioned on the left)
              Positioned(
                left: 0,
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Source filter
                    _buildDropdownContainer(
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<LogSourceFilter>(
                          value: _selectedSourceFilter,
                          isDense: true,
                          style: const TextStyle(
                            fontSize: 13,
                            color: Color(0xFF0D47A1),
                            fontWeight: FontWeight.w500,
                          ),
                          dropdownColor: Colors.white,
                          icon: const Icon(
                            Icons.filter_list,
                            size: 16,
                            color: Color(0xFF0D47A1),
                          ),
                          items: LogSourceFilter.values.map((filter) {
                            return DropdownMenuItem<LogSourceFilter>(
                              value: filter,
                              child: Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 4.0),
                                child: Text(filter.label),
                              ),
                            );
                          }).toList(),
                          onChanged: _onSourceFilterChanged,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    // Severity filter
                    _buildDropdownContainer(
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<LogSeverityFilter>(
                          value: _selectedSeverityFilter,
                          isDense: true,
                          style: const TextStyle(
                            fontSize: 13,
                            color: Color(0xFF0D47A1),
                            fontWeight: FontWeight.w500,
                          ),
                          dropdownColor: Colors.white,
                          icon: const Icon(
                            Icons.expand_more,
                            size: 16,
                            color: Color(0xFF0D47A1),
                          ),
                          selectedItemBuilder: (context) {
                            return LogSeverityFilter.values.map((filter) {
                              return Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Container(
                                    width: 10,
                                    height: 10,
                                    decoration: BoxDecoration(
                                      shape: BoxShape.circle,
                                      color: filter.indicatorColor,
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    filter.label,
                                    style: const TextStyle(
                                      fontSize: 13,
                                      color: Color(0xFF0D47A1),
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                ],
                              );
                            }).toList();
                          },
                          items: LogSeverityFilter.values.map((filter) {
                            return DropdownMenuItem<LogSeverityFilter>(
                              value: filter,
                              child: Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 4.0),
                                child: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Container(
                                      width: 10,
                                      height: 10,
                                      decoration: BoxDecoration(
                                        shape: BoxShape.circle,
                                        color: filter.indicatorColor,
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Text(filter.label),
                                  ],
                                ),
                              ),
                            );
                          }).toList(),
                          onChanged: _onSeverityFilterChanged,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    // Time filter
                    _buildDropdownContainer(
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<TimePeriod?>(
                          value: _selectedTimePeriod,
                          isDense: true,
                          style: const TextStyle(
                            fontSize: 13,
                            color: Color(0xFF0D47A1),
                            fontWeight: FontWeight.w500,
                          ),
                          dropdownColor: Colors.white,
                          icon: const Icon(
                            Icons.access_time,
                            size: 16,
                            color: Color(0xFF0D47A1),
                          ),
                          items: [
                            const DropdownMenuItem<TimePeriod?>(
                              value: null,
                              child: Padding(
                                padding: EdgeInsets.symmetric(horizontal: 4.0),
                                child: Text('Any Time'),
                              ),
                            ),
                            ...TimePeriod.values.map((p) => DropdownMenuItem<TimePeriod?>(
                                  value: p,
                                  child: Padding(
                                    padding: const EdgeInsets.symmetric(horizontal: 4.0),
                                    child: Text(p.label),
                                  ),
                                )),
                          ],
                          onChanged: _onTimePeriodChanged,
                        ),
                      ),
                    ),
                  ],
                ),
              ),

              // Centered Title
              Center(
                child: Text(
                  'Logs (${widget.logs.length})',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: const Color(0xFF0D47A1),
                  ),
                ),
              ),

              // Row limit dropdown (positioned to the right of centre, before expand/reset)
              Positioned(
                right: 96,
                child: _buildDropdownContainer(
                  child: DropdownButtonHideUnderline(
                    child: DropdownButton<int>(
                      value: _rowLimit,
                      isDense: true,
                      style: const TextStyle(
                        fontSize: 13,
                        color: Color(0xFF0D47A1),
                        fontWeight: FontWeight.w500,
                      ),
                      dropdownColor: Colors.white,
                      icon: const Icon(
                        Icons.expand_more,
                        size: 16,
                        color: Color(0xFF0D47A1),
                      ),
                      items: _rowLimitOptions.map((limit) {
                        return DropdownMenuItem<int>(
                          value: limit,
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 4.0),
                            child: Text('$limit rows'),
                          ),
                        );
                      }).toList(),
                      onChanged: _onRowLimitChanged,
                    ),
                  ),
                ),
              ),

              // Expand/Collapse button (positioned on the right, before reset button)
              Positioned(
                right: 48,
                child: IconButton(
                  icon: Icon(
                    widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                    color: const Color(0xFF0D47A1),
                  ),
                  tooltip: widget.isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                  onPressed: () {
                    if (widget.isFullScreen) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (context) => FullScreenPanelView(
                            panelType: PanelType.logs,
                            socket: widget.socket,
                            logs: widget.logs,
                            isLoading: widget.isLoading,
                          ),
                        ),
                      );
                    }
                  },
                ),
              ),

              // Reset logs button (positioned on the right)
              Positioned(
                right: 0,
                child: IconButton(
                  icon: const Icon(Icons.delete_forever, color: Color(0xFF0D47A1)),
                  tooltip: 'Delete logs',
                  onPressed: () {
                    _resetLogs(context);
                  },
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: widget.isLoading
              ? const Center(child: CircularProgressIndicator())
              : widget.logs.isEmpty
                  ? Center(
                      child: Text(
                        'No logs available',
                        style: TextStyle(
                          color: Colors.grey[700],
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    )
                  : _buildLogTable(context),
        ),
      ],
    );
  }

  Widget _buildLogTable(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.vertical,
      child: LayoutBuilder(
        builder: (context, constraints) {
          return SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: ConstrainedBox(
              constraints: BoxConstraints(minWidth: constraints.maxWidth),
              child: DataTable(
                headingRowColor: WidgetStateProperty.all(const Color(0xFFE3F2FD)),
                headingRowHeight: 36,
                dataRowMinHeight: 32,
                dataRowMaxHeight: 48,
                columns: const [
                  DataColumn(
                    label: Text(
                      'Timestamp',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Severity',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Source',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Message',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                ],
                rows: widget.logs.map((log) {
                  return DataRow(
                    cells: [
                      DataCell(Text(_formatTimestamp(log.timestamp))),
                      DataCell(_buildSeverityIndicator(log.severity)),
                      DataCell(Text(log.source)),
                      DataCell(
                        Container(
                          alignment: Alignment.centerLeft,
                          child: SelectableText(log.message),
                        ),
                      ),
                    ],
                  );
                }).toList(),
              ),
            ),
          );
        },
      ),
    );
  }

  String _formatTimestamp(String timestamp) {
    try {
      final dateTime = DateTime.parse(timestamp);
      return '${dateTime.hour.toString().padLeft(2, '0')}:${dateTime.minute.toString().padLeft(2, '0')}:${dateTime.second.toString().padLeft(2, '0')}';
    } catch (e) {
      return timestamp;
    }
  }

  Widget _buildSeverityIndicator(String severity) {
    Color color;
    switch (severity.toUpperCase()) {
      case 'CRITICAL':
        color = Colors.red;
        break;
      case 'ERROR':
        color = Colors.orange;
        break;
      case 'WARNING':
        color = Colors.yellow;
        break;
      case 'INFO':
        color = Colors.green;
        break;
      case 'DEBUG':
        color = Colors.blue;
        break;
      default:
        color = Colors.grey;
    }

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: color,
          ),
        ),
        const SizedBox(width: 8),
        Text(severity.toUpperCase()),
      ],
    );
  }
}
