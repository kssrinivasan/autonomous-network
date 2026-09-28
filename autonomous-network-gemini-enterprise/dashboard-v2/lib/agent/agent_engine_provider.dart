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

import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_ai_toolkit/flutter_ai_toolkit.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';
import '../utils/environment_config.dart';
import 'reasoning_event.dart';

/// Next-generation LLM Provider that connects directly to Google Cloud Vertex AI
/// Reasoning Engine (Agent Engine) over authenticated REST HTTP streaming APIs.
class AgentEngineProvider with ChangeNotifier implements LlmProvider {
  final http.Client httpClient = http.Client();
  final Uuid uuid = const Uuid();
  List<ChatMessage> _history = [];
  String _persistentThreadId;

  ChatMessage? _currentStreamingMessage;
  StreamController<String>? _currentController;
  bool _hasStartedMessage = false;

  List<ReasoningEvent> _currentReasoningEvents = [];
  bool _isGenerating = false;

  AgentEngineProvider() : _persistentThreadId = const Uuid().v4() {
    debugPrint('AgentEngineProvider: initialized thread_id=$_persistentThreadId');
  }

  List<ReasoningEvent> get currentReasoningEvents =>
      List.unmodifiable(_currentReasoningEvents);
  bool get isGenerating => _isGenerating;

  @override
  List<ChatMessage> get history => _history;

  @override
  set history(Iterable<ChatMessage> newHistory) {
    _history = newHistory.toList();
    notifyListeners();
  }

  @override
  Stream<String> generateStream(
    String prompt, {
    Iterable<Attachment>? attachments,
  }) {
    final controller = StreamController<String>();
    _currentController = controller;
    _hasStartedMessage = false;
    _currentReasoningEvents = [];
    _isGenerating = true;

    _history.add(ChatMessage(
      origin: MessageOrigin.user,
      text: prompt,
      attachments: attachments?.toList() ?? [],
    ));

    SchedulerBinding.instance.addPostFrameCallback((_) {
      notifyListeners();
    });

    _executeStreamQuery(prompt, controller);

    return controller.stream;
  }

  Future<void> _executeStreamQuery(
      String prompt, StreamController<String> controller) async {
    try {
      final url = Uri.parse('/streamQuery');

      final payload = {
        "classMethod": "streaming_agent_run_with_events",
        "input": {
          "request_json": jsonEncode({
            "session_id": _persistentThreadId,
            "message": {
              "role": "user",
              "parts": [
                {"text": prompt}
              ]
            }
          })
        }
      };

      debugPrint('AgentEngineProvider: POST $url');

      final request = http.Request('POST', url)
        ..headers.addAll({
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          if (EnvironmentConfig.aiPlatformApiKey.isNotEmpty)
            'Authorization': 'Bearer ${EnvironmentConfig.aiPlatformApiKey}',
        })
        ..body = jsonEncode(payload);

      final response = await httpClient.send(request);

      if (response.statusCode != 200) {
        final errStr = await response.stream.bytesToString();
        debugPrint(
            'AgentEngineProvider HTTP Error ${response.statusCode}: $errStr');
        _appendError('API Error (${response.statusCode}): $errStr');
        controller.addError(Exception('HTTP ${response.statusCode}'));
        _isGenerating = false;
        notifyListeners();
        controller.close();
        return;
      }

      response.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(
        (line) {
          line = line.trim();
          if (line.isEmpty || line.startsWith(':')) return;

          if (line.startsWith('data: ')) {
            final dataStr = line.substring(6).trim();
            if (dataStr == '[DONE]') return;

            // Log raw chunk to browser console so we can inspect the event structure.
            debugPrint('AgentEngineProvider CHUNK: $dataStr');

            String textChunk = '';
            try {
              if (dataStr.startsWith('{')) {
                final dynamic decoded = jsonDecode(dataStr);
                if (decoded is Map) {
                  // Agent Runtime allocates its own numeric session id and only
                  // continues a conversation when that exact id comes back. A
                  // client-generated UUID is accepted but silently opens a NEW
                  // session every turn, so the supervisor keeps re-proposing its
                  // plan instead of executing it. Adopt the id it hands us.
                  final returnedSession = decoded['session_id'];
                  if (returnedSession is String &&
                      returnedSession.isNotEmpty &&
                      returnedSession != _persistentThreadId) {
                    debugPrint('AgentEngineProvider: adopting server session_id '
                        '$returnedSession (was $_persistentThreadId)');
                    _persistentThreadId = returnedSession;
                  }

                  final events = decoded['events'];
                  if (events is List) {
                    // First pass: determine whether any event signals completion.
                    // ADK serializes this as is_final_response (snake) or isFinalResponse
                    // (proto-JSON camel). turn_complete / turnComplete are alternatives.
                    final bool hasFinalSignal = events.any((e) {
                      if (e is! Map) return false;
                      return e['is_final_response'] == true ||
                          e['isFinalResponse'] == true ||
                          e['turn_complete'] == true ||
                          e['turnComplete'] == true;
                    });

                    final buf = StringBuffer();
                    for (final event in events) {
                      if (event is! Map) continue;
                      final content = event['content'];
                      if (content is! Map) continue;
                      final parts = content['parts'];
                      if (parts is! List) continue;

                      final isFinal = event['is_final_response'] == true ||
                          event['isFinalResponse'] == true ||
                          event['turn_complete'] == true ||
                          event['turnComplete'] == true;
                      final author = event['author'] as String? ?? '';

                      for (final part in parts) {
                        if (part is! Map) continue;

                        // Function call — ADK may serialise as camelCase or snake_case.
                        final funcCall =
                            part['functionCall'] ?? part['function_call'];
                        if (funcCall is Map) {
                          _addReasoningEvent(ReasoningEvent(
                            type: ReasoningEventType.functionCall,
                            label: funcCall['name'] as String? ?? 'tool',
                            detail: funcCall['args'] != null
                                ? jsonEncode(funcCall['args'])
                                : null,
                          ));
                          continue;
                        }

                        // Function response — same camel/snake ambiguity.
                        final funcResp = part['functionResponse'] ??
                            part['function_response'];
                        if (funcResp is Map) {
                          _addReasoningEvent(ReasoningEvent(
                            type: ReasoningEventType.functionResponse,
                            label: funcResp['name'] as String? ?? 'tool',
                            detail: funcResp['response'] != null
                                ? jsonEncode(funcResp['response'])
                                : null,
                          ));
                          continue;
                        }

                        if (part['text'] is String) {
                          final text = part['text'] as String;
                          if (part['thought'] == true) {
                            // Gemini thinking-mode part → always reasoning.
                            if (text.isNotEmpty) {
                              _addReasoningEvent(ReasoningEvent(
                                type: ReasoningEventType.thought,
                                label: text,
                              ));
                            }
                          } else if (hasFinalSignal &&
                              !isFinal &&
                              text.isNotEmpty) {
                            // There is a final event in this batch and this is
                            // not it → intermediate agent text → reasoning trace.
                            _addReasoningEvent(ReasoningEvent(
                              type: ReasoningEventType.thought,
                              label: author.isNotEmpty
                                  ? '[$author] $text'
                                  : text,
                            ));
                          } else {
                            buf.write(text);
                          }
                        }
                      }
                    }
                    textChunk = buf.toString();
                  }
                }
              }
            } catch (_) {}

            if (!_hasStartedMessage && textChunk.isNotEmpty) {
              _currentStreamingMessage = ChatMessage.llm();
              _history.add(_currentStreamingMessage!);
              _hasStartedMessage = true;
              notifyListeners();
            }

            if (textChunk.isNotEmpty && _currentStreamingMessage != null) {
              _currentStreamingMessage!.append(textChunk);
              controller.add(textChunk);
              notifyListeners();
            }
          }
        },
        onError: (err) {
          debugPrint('AgentEngineProvider Stream Error: $err');
          _appendError('Streaming exception: $err');
          controller.addError(err);
          _isGenerating = false;
          notifyListeners();
          if (!controller.isClosed) controller.close();
        },
        onDone: () {
          debugPrint('AgentEngineProvider stream complete.');
          _currentStreamingMessage = null;
          _currentController = null;
          _hasStartedMessage = false;
          _isGenerating = false;
          notifyListeners();
          if (!controller.isClosed) controller.close();
        },
      );
    } catch (e) {
      debugPrint('AgentEngineProvider Execution Exception: $e');
      _appendError('Execution failure: $e');
      _isGenerating = false;
      notifyListeners();
      if (!controller.isClosed) {
        controller.addError(e);
        controller.close();
      }
    }
  }

  void _addReasoningEvent(ReasoningEvent event) {
    _currentReasoningEvents.add(event);
    notifyListeners();
  }

  void _appendError(String errMsg) {
    if (!_hasStartedMessage) {
      final m = ChatMessage.llm();
      m.append(errMsg);
      _history.add(m);
      notifyListeners();
    } else if (_currentStreamingMessage != null) {
      _currentStreamingMessage!.append('\n\n⚠️ $errMsg');
      notifyListeners();
    }
  }

  @override
  Stream<String> sendMessageStream(
    String prompt, {
    Iterable<Attachment>? attachments,
  }) =>
      generateStream(prompt, attachments: attachments);

  void resetConversation() {
    _persistentThreadId = uuid.v4();
    _history.clear();
    _currentStreamingMessage = null;
    _currentController?.close();
    _currentController = null;
    _hasStartedMessage = false;
    _currentReasoningEvents = [];
    _isGenerating = false;
    notifyListeners();
  }

  @override
  void dispose() {
    httpClient.close();
    _currentController?.close();
    super.dispose();
  }
}
