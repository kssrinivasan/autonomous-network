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

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_ai_toolkit/flutter_ai_toolkit.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'package:uuid/uuid.dart';

/// Simple socket provider that bridges the plain chat_message / chat_response
/// socket protocol with the flutter_ai_toolkit LlmProvider interface.
///
/// Backend protocol:
///   Send:    chat_message  { text: String, thread_id: String }
///   Receive: chat_response { text: String, done: bool, error?: bool }
class ChatSocketProvider with ChangeNotifier implements LlmProvider {
  final io.Socket socket;
  final Uuid uuid = const Uuid();
  List<ChatMessage> _history = [];

  // Persistent thread ID — one per conversation session
  String _persistentThreadId;

  // Current active streaming message / controller
  ChatMessage? _currentStreamingMessage;
  StreamController<String>? _currentController;

  // Whether any text has been received for the current response
  bool _hasStartedMessage = false;

  ChatSocketProvider({required this.socket})
      : _persistentThreadId = const Uuid().v4() {
    debugPrint('ChatSocketProvider: created thread_id=$_persistentThreadId');
    socket.on('chat_response', _handleChatResponse);
  }

  // ---------------------------------------------------------------------------
  // Socket event handler
  // ---------------------------------------------------------------------------

  void _handleChatResponse(dynamic data) {
    try {
      if (data == null) return;

      final text = (data['text'] as String?) ?? '';
      final done = (data['done'] as bool?) ?? false;
      final isError = (data['error'] as bool?) ?? false;

      debugPrint('chat_response: text="${text.length > 80 ? '${text.substring(0, 80)}…' : text}" done=$done error=$isError');

      // Start a new assistant message on first non-empty chunk
      if (!_hasStartedMessage && text.isNotEmpty) {
        _currentStreamingMessage = ChatMessage.llm();
        _history.add(_currentStreamingMessage!);
        _hasStartedMessage = true;
        notifyListeners();
      }

      // Append text chunk
      if (text.isNotEmpty && _currentStreamingMessage != null) {
        _currentStreamingMessage!.append(text);
        if (_currentController != null && !_currentController!.isClosed) {
          _currentController!.add(text);
        }
        notifyListeners();
      }

      // Close on done
      if (done) {
        // Edge case: error with no prior text — add an error message to history
        if (isError && !_hasStartedMessage && text.isNotEmpty) {
          final errMsg = ChatMessage.llm();
          errMsg.append(text);
          _history.add(errMsg);
          notifyListeners();
        }

        if (_currentController != null && !_currentController!.isClosed) {
          if (isError) {
            _currentController!.addError(Exception(text.isNotEmpty ? text : 'Unknown error'));
          }
          _currentController!.close();
        }

        _currentStreamingMessage = null;
        _currentController = null;
        _hasStartedMessage = false;
      }
    } catch (e) {
      debugPrint('Error handling chat_response: $e');
      if (_currentController != null && !_currentController!.isClosed) {
        _currentController!.addError(e);
        _currentController!.close();
      }
      _currentStreamingMessage = null;
      _currentController = null;
      _hasStartedMessage = false;
    }
  }

  // ---------------------------------------------------------------------------
  // LlmProvider interface
  // ---------------------------------------------------------------------------

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
    final threadId = _persistentThreadId;

    _currentController = controller;
    _hasStartedMessage = false;

    // Add user message to history immediately
    _history.add(ChatMessage(
      origin: MessageOrigin.user,
      text: prompt,
      attachments: attachments?.toList() ?? [],
    ));

    SchedulerBinding.instance.addPostFrameCallback((_) {
      notifyListeners();
    });

    // Emit plain chat_message to the backend
    try {
      debugPrint('ChatSocketProvider: sending chat_message thread_id=$threadId');
      socket.emit('chat_message', {
        'text': prompt,
        'thread_id': threadId,
      });
    } catch (e) {
      debugPrint('Error sending chat_message: $e');
      controller.addError(e);
      controller.close();
      _currentController = null;
    }

    return controller.stream;
  }

  @override
  Stream<String> sendMessageStream(
    String prompt, {
    Iterable<Attachment>? attachments,
  }) =>
      generateStream(prompt, attachments: attachments);

  // ---------------------------------------------------------------------------
  // Conversation management
  // ---------------------------------------------------------------------------

  /// Reset the conversation: generate a new thread ID and clear history.
  void resetConversation() {
    _persistentThreadId = uuid.v4();
    debugPrint('ChatSocketProvider: reset — new thread_id=$_persistentThreadId');

    _history.clear();
    _currentStreamingMessage = null;
    if (_currentController != null && !_currentController!.isClosed) {
      _currentController!.close();
    }
    _currentController = null;
    _hasStartedMessage = false;

    notifyListeners();
  }

  /// The current thread ID (for debugging / display).
  String get currentThreadId => _persistentThreadId;

  @override
  void dispose() {
    socket.off('chat_response', _handleChatResponse);
    _currentController?.close();
    super.dispose();
  }
}
