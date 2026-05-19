"""Per-agent launch-message composition.

Owns the four-row launch wire shape (system + ``<context>`` +
``<Task Guidance>`` + skill) and the builder dispatch by exact agent name.
``context_engine/`` is now context-only; this package wraps the rendered
context in its envelope and threads the role-specific prose through
``task_guidance/``.
"""

from __future__ import annotations

from task_center.agent_launch.composer import AgentEntryComposer
from task_center.agent_launch.entry_messages import AgentEntryMessages
from task_center.agent_launch.skill_message import (
    build_skill_message,
    _wrap_task_guidance,
)
from task_center.agent_launch.task_guidance_dispatch import (
    task_guidance_builder_for,
)

__all__ = [
    "AgentEntryComposer",
    "AgentEntryMessages",
    "build_skill_message",
    "task_guidance_builder_for",
    "_wrap_task_guidance",
]
