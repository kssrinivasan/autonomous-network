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

"""Helper functions for A2A SDK protobuf types.

These helpers replace the removed a2a.utils.new_task and
a2a.utils.new_agent_text_message functions from earlier SDK versions.
"""

from a2a.types import Task, TaskStatus, TaskState, Message, Part, Role
from uuid import uuid4


def new_task(message: Message) -> Task:
    """Create a new task from a message.

    Args:
        message: The message that initiated the task

    Returns:
        A new Task with a generated ID and WORKING state.
        If the message has a task_id, that ID is used instead of generating a new one.
    """
    task = Task()
    # Use the task_id from the message if present (check for both existence and non-empty)
    # In protobuf, accessing an unset field returns empty string, not None
    if hasattr(message, 'task_id') and message.task_id:
        task.id = message.task_id
    else:
        task.id = uuid4().hex

    task.context_id = message.context_id if message.context_id else uuid4().hex

    # Initialize status
    status = TaskStatus()
    status.state = TaskState.TASK_STATE_WORKING
    task.status.CopyFrom(status)

    return task


def new_agent_text_message(text: str, context_id: str = "", task_id: str = "") -> Message:
    """Create an agent text message.

    Args:
        text: The message text content
        context_id: Optional context ID to associate with the message
        task_id: Optional task ID to associate with the message

    Returns:
        A new Message with ROLE_AGENT and the provided text
    """
    msg = Message()
    msg.role = Role.ROLE_AGENT
    msg.message_id = uuid4().hex

    if context_id:
        msg.context_id = context_id
    if task_id:
        msg.task_id = task_id

    # Add text part
    part = Part()
    part.text = text
    msg.parts.append(part)

    return msg
