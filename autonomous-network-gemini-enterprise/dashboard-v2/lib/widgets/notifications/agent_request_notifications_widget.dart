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
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../appstate.dart';
import '../../models/push_notification.dart';

class AgentRequestNotificationsWidget extends StatefulWidget {
  const AgentRequestNotificationsWidget({super.key});

  @override
  State<AgentRequestNotificationsWidget> createState() => _AgentRequestNotificationsWidgetState();
}

class _AgentRequestNotificationsWidgetState extends State<AgentRequestNotificationsWidget> {
  // Set to track which notification IDs are expanded
  final Set<String> _expandedCards = {};

  // Toggle card expansion
  void _toggleCardExpansion(String notificationId) {
    setState(() {
      if (_expandedCards.contains(notificationId)) {
        _expandedCards.remove(notificationId);
      } else {
        _expandedCards.add(notificationId);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        final notifications = appState.pushNotifications;
        
        if (notifications.isEmpty) {
          return const Center(
            child: Text(
              'No agent requests',
              style: TextStyle(
                fontSize: 18,
                fontStyle: FontStyle.italic,
                color: Colors.grey,
              ),
            ),
          );
        }
        
        return Container(
          width: double.infinity,
          color: Colors.white,
          child: ListView.builder(
            padding: const EdgeInsets.all(8.0),
            itemCount: notifications.length,
            itemBuilder: (context, index) {
              final notification = notifications[index];
              final isExpanded = _expandedCards.contains(notification.id);
              
              return Padding(
                padding: const EdgeInsets.only(bottom: 8.0),
                child: Card(
                  elevation: 2,
                  margin: EdgeInsets.zero,
                  color: Colors.white,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                    side: BorderSide(color: Colors.grey.shade300, width: 1),
                  ),
                  child: InkWell(
                    onTap: () => _toggleCardExpansion(notification.id),
                    hoverColor: Colors.transparent,
                    splashColor: Colors.transparent,
                    highlightColor: Colors.transparent,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        // Card header
                        Padding(
                          padding: const EdgeInsets.all(12.0),
                          child: Row(
                            children: [
                              // Name
                              Expanded(
                                child: Text(
                                  notification.name,
                                  style: TextStyle(
                                    fontWeight: notification.isRead ? FontWeight.normal : FontWeight.bold,
                                    fontSize: 16,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                              
                              // State chip
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                                decoration: BoxDecoration(
                                  color: _getStateColor(notification.state),
                                  borderRadius: BorderRadius.circular(12),
                                  boxShadow: [
                                    BoxShadow(
                                      color: _getStateColor(notification.state).withOpacity(0.3),
                                      blurRadius: 4,
                                      offset: const Offset(0, 2),
                                    ),
                                  ],
                                ),
                                child: Text(
                                  notification.state.toUpperCase(),
                                  style: const TextStyle(
                                    fontSize: 10,
                                    color: Colors.white,
                                    fontWeight: FontWeight.bold,
                                    letterSpacing: 0.5,
                                  ),
                                ),
                              ),
                              
                              const SizedBox(width: 8),
                              
                              // Timestamp
                              Text(
                                _formatTimestamp(notification.timestamp),
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: Colors.grey,
                                ),
                              ),
                              
                              const SizedBox(width: 8),
                              
                              // Expand/collapse icon
                              Icon(
                                isExpanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                                color: Colors.grey,
                              ),
                            ],
                          ),
                        ),
                        
                        // Content preview (when collapsed)
                        if (!isExpanded)
                          Padding(
                            padding: const EdgeInsets.fromLTRB(12.0, 0.0, 12.0, 12.0),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Divider(),
                                Text(
                                  _getFirstLines(notification.content),
                                  style: const TextStyle(fontSize: 14),
                                  overflow: TextOverflow.ellipsis,
                                  maxLines: 1,
                                ),
                                if (_hasMoreContent(notification.content))
                                  Padding(
                                    padding: const EdgeInsets.only(top: 4.0),
                                    child: Text(
                                      'Tap to expand...',
                                      style: TextStyle(
                                        color: Theme.of(context).colorScheme.primary,
                                        fontSize: 12,
                                        fontStyle: FontStyle.italic,
                                      ),
                                    ),
                                  ),
                              ],
                            ),
                          ),
                        
                        // Expanded content
                        if (isExpanded) ...[
                          const Divider(height: 1),
                          if (notification.inputData != null)
                            Padding(
                              padding: const EdgeInsets.fromLTRB(12.0, 12.0, 12.0, 12.0),
                              child: InputDataWidget(
                                inputData: notification.inputData!,
                                content: notification.content,
                              ),
                            )
                          else
                            Padding(
                              padding: const EdgeInsets.all(12.0),
                              child: MarkdownBody(
                                data: notification.content,
                                styleSheet: MarkdownStyleSheet(
                                  p: const TextStyle(fontSize: 14),
                                  h1: const TextStyle(
                                      fontSize: 16, fontWeight: FontWeight.bold),
                                  h2: const TextStyle(
                                      fontSize: 15, fontWeight: FontWeight.bold),
                                  h3: const TextStyle(
                                      fontSize: 14, fontWeight: FontWeight.bold),
                                  code: const TextStyle(
                                    backgroundColor: Colors.white,
                                    color: Colors.black,
                                    fontSize: 13,
                                  ),
                                  codeblockDecoration: BoxDecoration(
                                    color: Colors.white,
                                    borderRadius: BorderRadius.circular(4.0),
                                  ),
                                  blockquote: const TextStyle(
                                    color: Colors.grey,
                                    fontStyle: FontStyle.italic,
                                    fontSize: 13,
                                  ),
                                ),
                                onTapLink: (text, href, title) async {
                                  if (href != null) {
                                    try {
                                      final Uri url = Uri.parse(href);
                                      if (await canLaunchUrl(url)) {
                                        await launchUrl(url,
                                            mode: LaunchMode.externalApplication);
                                      }
                                    } catch (e) {
                                      // Ignore link errors
                                    }
                                  }
                                },
                              ),
                            ),
                          
                          // Action buttons
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 8.0),
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.end,
                              children: [
                                // Thumbs up
                                TextButton.icon(
                                  icon: const Icon(
                                    Icons.thumb_up_outlined,
                                    color: Colors.green,
                                    size: 20,
                                  ),
                                  label: const Text('Approve'),
                                  onPressed: () {
                                    _showConfirmationDialog(
                                      context,
                                      notification,
                                      'approve',
                                    );
                                  },
                                ),
                                const SizedBox(width: 8),
                                // Thumbs down
                                TextButton.icon(
                                  icon: const Icon(
                                    Icons.thumb_down_outlined,
                                    color: Colors.red,
                                    size: 20,
                                  ),
                                  label: const Text('Reject'),
                                  onPressed: () {
                                    _showConfirmationDialog(
                                      context,
                                      notification,
                                      'reject',
                                    );
                                  },
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              );
            },
          ),
        );
      },
    );
  }

  // Helper method to show a confirmation dialog
  void _showConfirmationDialog(
    BuildContext context,
    PushNotification notification,
    String feedback,
  ) {
    showDialog(
      context: context,
      builder: (BuildContext dialogContext) {
        return AlertDialog(
          title: Text('Confirm ${feedback == 'approve' ? 'Approval' : 'Rejection'}'),
          content: Text('Are you sure you want to ${feedback == 'approve' ? 'approve' : 'reject'} this notification?'),
          actions: <Widget>[
            TextButton(
              child: const Text('No'),
              onPressed: () {
                Navigator.of(dialogContext).pop(); // Close the dialog
              },
            ),
            TextButton(
              child: const Text('Yes'),
              onPressed: () {
                final appState = Provider.of<Appstate>(context, listen: false);

                // Send notification feedback
                if (appState.socket != null && appState.socket!.connected) {
                  appState.socket!.emit('notification_feedback', {
                    'notification_id': notification.id,
                    'feedback': feedback,
                    'notification_details': notification.toJson(),
                  });
                  print('Sent $feedback feedback for notification: ${notification.id}');
                }

                // Remove the notification
                appState.removeNotification(notification.id);

                // Show a confirmation snackbar
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text('Notification ${feedback == 'approve' ? 'approved' : 'rejected'}'),
                    duration: const Duration(seconds: 2),
                  ),
                );
                Navigator.of(dialogContext).pop(); // Close the dialog
              },
            ),
          ],
        );
      },
    );
  }

  // Helper method to format timestamp
  String _formatTimestamp(DateTime timestamp) {
    final now = DateTime.now();
    final difference = now.difference(timestamp);
    
    if (difference.inDays > 0) {
      return '${difference.inDays}d ago';
    } else if (difference.inHours > 0) {
      return '${difference.inHours}h ago';
    } else if (difference.inMinutes > 0) {
      return '${difference.inMinutes}m ago';
    } else {
      return 'Just now';
    }
  }

  // Helper method to get color based on notification state
  Color _getStateColor(String state) {
    switch (state.toLowerCase()) {
      case 'input_required':
        return Colors.orange;
      case 'completed':
        return Colors.green;
      case 'error':
        return Colors.red;
      case 'warning':
        return Colors.amber;
      case 'info':
        return Colors.blue;
      default:
        return Colors.grey;
    }
  }
  
  // Helper method to get first line of content
  String _getFirstLines(String content) {
    final lines = content.split('\n');
    if (lines.isEmpty) {
      return '';
    }
    return lines.first;
  }
  
  // Helper method to check if there's more content
  bool _hasMoreContent(String content) {
    return content.split('\n').length > 1;
  }
}

/// A widget to display the input_data map in a formatted way
class InputDataWidget extends StatelessWidget {
  final Map<String, dynamic> inputData;
  final String content;

  const InputDataWidget(
      {super.key, required this.inputData, required this.content});

  String _convertMapToMarkdownTable(Map<String, dynamic> data) {
    String table = '| Parameter | Value |\n|---|---|\n';

    void buildRows(Map<String, dynamic> map, [String prefix = '']) {
      map.forEach((key, value) {
        final newKey = prefix.isEmpty ? key : '$prefix.$key';
        if (value is Map<String, dynamic>) {
          buildRows(value, newKey);
        } else {
          table += '| $newKey | `$value` |\n';
        }
      });
    }

    buildRows(data);
    return table;
  }

  @override
  Widget build(BuildContext context) {
    final markdownString = '''
### Request to approve
$content

Please review the details below and use the buttons to approve or reject the proposed action.

### Details from Agent
${_convertMapToMarkdownTable(inputData)}
''';

    return Container(
      padding: const EdgeInsets.all(12.0),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8.0),
      ),
      child: MarkdownBody(
        data: markdownString,
        styleSheet: MarkdownStyleSheet(
          h3: const TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.bold,
              color: Colors.black87),
          p: const TextStyle(fontSize: 14, height: 1.5),
          code: const TextStyle(
            backgroundColor: Colors.white,
            color: Colors.black,
            fontSize: 13,
          ),
          tableBorder: TableBorder.all(
            color: const Color(0xFFE0E0E0),
            width: 1,
          ),
          tableHead: const TextStyle(
            fontWeight: FontWeight.bold,
          ),
        ),
      ),
    );
  }
}
