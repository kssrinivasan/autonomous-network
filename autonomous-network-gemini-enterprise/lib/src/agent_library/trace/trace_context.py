# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextvars
import uuid

# Stores the global Trace ID (correlation across services)
_trace_id_var = contextvars.ContextVar("trace_id", default=None)

# Stores the Stack of Span IDs (local hierarchy)
# Default is an empty tuple (immutable)
_span_stack_var = contextvars.ContextVar("span_stack", default=())

class TracingContext:

    # --- Trace ID (Global) ---
    @staticmethod
    def get_trace_id():
        val = _trace_id_var.get()
        if not val:
            val = str(uuid.uuid4())
            _trace_id_var.set(val)
        return val

    @staticmethod
    def set_trace_id(trace_id: str):
        _trace_id_var.set(trace_id)

    # --- Span Stack (Local Hierarchy) ---
    @staticmethod
    def push_span(new_span_id: str):
        """Adds a new ID to the top of the stack."""
        current_stack = _span_stack_var.get()
        # Create new tuple with added ID (Immutable pattern safe for asyncio)
        _span_stack_var.set(current_stack + (new_span_id,))

    @staticmethod
    def pop_span():
        """Removes the top ID from the stack."""
        current_stack = _span_stack_var.get()
        if current_stack:
            _span_stack_var.set(current_stack[:-1])

    @staticmethod
    def get_current_parent_id():
        """Peeks at the top of the stack to find the parent.
        
        This is called BEFORE pushing a new span, so the parent is the
        current top of the stack (if any exists).
        """
        current_stack = _span_stack_var.get()
        if current_stack:
            return current_stack[-1]  # Parent is current top of stack
        return None  # Empty stack = root operation, no parent
    
    @staticmethod
    def get_current_span_id():
        """Returns the current active span ID (top of the stack)."""
        current_stack = _span_stack_var.get()
        if current_stack:
            return current_stack[-1]
        return None
