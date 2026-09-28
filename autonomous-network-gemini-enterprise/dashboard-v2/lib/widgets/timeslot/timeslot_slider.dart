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
import '../../../appstate.dart';
import 'package:intl/intl.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';

class TimeslotSlider extends StatefulWidget {
  const TimeslotSlider({super.key});

  @override
  State<TimeslotSlider> createState() => _TimeslotSliderState();
}

class _TimeslotSliderState extends State<TimeslotSlider> {
  double _currentIndex = 0;
  int _lastSnapshotCount = 0;

  // No local refresh timer needed: this widget is wrapped in Consumer<Appstate>
  // and rebuilds automatically whenever appState.availableSnapshots changes,
  // which happens on the 10-second topology polling timer in appstate.dart.

  @override
  void initState() {
    super.initState();
  }

  @override
  void dispose() {
    super.dispose();
  }

  void _onSliderChanged(double value) {
    setState(() {
      _currentIndex = value;
    });
  }

  void _onSliderChangeEnd(double value, List<String> snapshots) {
    if (snapshots.isEmpty) return;
    
    int index = value.toInt().clamp(0, snapshots.length);
    
    // If at the very end (index equals length), that's LIVE mode
    bool isLive = index >= snapshots.length;
    
    final appState = Provider.of<Appstate>(context, listen: false);
    
    if (isLive) {
      // Switch to LIVE mode
      appState.setLiveMode(true);
    } else {
      // Historical mode - use the exact snapshot timestamp
      final selectedTimestamp = snapshots[index];
      appState.setLiveMode(false, timestamp: selectedTimestamp);
    }
  }

  String _formatTimestamp(String isoTimestamp) {
    try {
      final dt = DateTime.parse(isoTimestamp);
      return DateFormat('HH:mm:ss').format(dt.toLocal());
    } catch (_) {
      return isoTimestamp.substring(11, 19);  // Fallback to substring
    }
  }

  String _formatRelativeTime(String isoTimestamp, bool isLive) {
    if (isLive) return 'Live';
    
    try {
      final dt = DateTime.parse(isoTimestamp);
      final difference = DateTime.now().difference(dt);
      
      if (difference.inHours > 0) {
        final mins = difference.inMinutes % 60;
        return '-${difference.inHours}h${mins > 0 ? ' ${mins}m' : ''}';
      } else if (difference.inMinutes > 0) {
        final secs = difference.inSeconds % 60;
        return '-${difference.inMinutes}m${secs > 0 ? ' ${secs}s' : ''}';
      } else {
        return '-${difference.inSeconds}s';
      }
    } catch (_) {
      return isoTimestamp;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        final snapshots = appState.availableSnapshots;
        final isLive = appState.isLiveMode;
        
        // Detect if snapshot count has changed
        if (snapshots.length != _lastSnapshotCount) {
          _lastSnapshotCount = snapshots.length;
          
          // If in LIVE mode, automatically move slider to the end when new snapshots appear
          if (isLive && snapshots.isNotEmpty) {
            // Use post-frame callback to update state after build
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (mounted) {
                setState(() {
                  _currentIndex = snapshots.length.toDouble();
                });
              }
            });
          }
        }
        
        // If in LIVE mode, keep slider at max position
        if (isLive && snapshots.isNotEmpty) {
          _currentIndex = snapshots.length.toDouble();
        }
        
        // Need at least 1 snapshot to show the slider
        if (snapshots.isEmpty) {
          return PointerInterceptor(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.9),
                borderRadius: BorderRadius.circular(16),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.1),
                    blurRadius: 10,
                    spreadRadius: 2,
                  ),
                ],
              ),
              child: const Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  SizedBox(width: 8),
                  Icon(Icons.access_time, size: 16, color: Colors.grey),
                  SizedBox(width: 8),
                  Text('Loading snapshots...', style: TextStyle(fontSize: 12, color: Colors.grey)),
                ],
              ),
            ),
          );
        }
        
        final maxIndex = snapshots.length.toDouble();
        final currentIdx = _currentIndex.clamp(0, maxIndex).toInt();
        final isAtLive = currentIdx >= snapshots.length;
        
        return PointerInterceptor(
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onHorizontalDragUpdate: (_) {},
            onVerticalDragUpdate: (_) {},
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.9),
                borderRadius: BorderRadius.circular(16),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.1),
                    blurRadius: 10,
                    spreadRadius: 2,
                  ),
                ],
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Text(
                            'Timeline  ', 
                            style: TextStyle(fontWeight: FontWeight.bold, color: Colors.blueGrey.shade700)
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: Colors.blue.shade50,
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.blue.shade200),
                            ),
                            child: Text(
                              '${snapshots.length} snapshots',
                              style: TextStyle(fontSize: 10, color: Colors.blue.shade700, fontWeight: FontWeight.bold),
                            ),
                          ),
                        ],
                      ),
                      Row(
                        children: [
                          if (!isAtLive) 
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                              margin: const EdgeInsets.only(right: 8),
                              decoration: BoxDecoration(
                                color: Colors.red.shade100,
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text('Historical', style: TextStyle(color: Colors.red.shade900, fontSize: 12, fontWeight: FontWeight.bold)),
                            ),
                          Text(
                            isAtLive 
                              ? 'Live' 
                              : _formatRelativeTime(snapshots[currentIdx], false),
                            style: TextStyle(
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              color: isAtLive ? Colors.green.shade700 : Colors.black87,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                  SliderTheme(
                    data: SliderTheme.of(context).copyWith(
                      activeTrackColor: Colors.blue,
                      inactiveTrackColor: Colors.blue.withOpacity(0.2),
                      trackHeight: 4.0,
                      thumbColor: isAtLive ? Colors.green.shade700 : Colors.blue.shade700,
                      thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8.0),
                      overlayColor: Colors.blue.withOpacity(0.2),
                      tickMarkShape: const RoundSliderTickMarkShape(tickMarkRadius: 3.0),
                      activeTickMarkColor: Colors.white.withOpacity(0.6),
                      inactiveTickMarkColor: Colors.blue.withOpacity(0.4),
                      valueIndicatorShape: const RectangularSliderValueIndicatorShape(),
                      valueIndicatorColor: isAtLive ? Colors.green.shade800 : Colors.blueGrey.shade800,
                      valueIndicatorTextStyle: const TextStyle(color: Colors.white, fontSize: 12),
                    ),
                    child: Slider(
                      value: _currentIndex.clamp(0, maxIndex),
                      min: 0,
                      max: maxIndex,
                      divisions: maxIndex.toInt(),
                      label: isAtLive 
                        ? 'Live'
                        : _formatRelativeTime(snapshots[currentIdx], false),
                      onChanged: _onSliderChanged,
                      onChangeEnd: (value) => _onSliderChangeEnd(value, snapshots),
                    ),
                  ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        snapshots.isNotEmpty ? _formatRelativeTime(snapshots.first, false) : '',
                        style: const TextStyle(fontSize: 10, color: Colors.grey),
                      ),
                      Icon(Icons.arrow_drop_up, size: 12, color: Colors.grey.shade400),
                      Text(
                        snapshots.length > 1 ? _formatRelativeTime(snapshots[snapshots.length ~/ 2], false) : '',
                        style: const TextStyle(fontSize: 10, color: Colors.grey),
                      ),
                      Icon(Icons.arrow_drop_up, size: 12, color: Colors.grey.shade400),
                      const Text(
                        'Live',
                        style: TextStyle(fontSize: 10, color: Colors.grey),
                      ),
                    ],
                  )
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
