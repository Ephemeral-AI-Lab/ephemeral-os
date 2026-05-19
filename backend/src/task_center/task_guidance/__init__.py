"""Registry-driven ``<Task Guidance>`` body builder.

A "task guidance" is the per-agent ``<Task Guidance>`` body assembled at launch
time from the rendered context packet and agent definition. The single builder
reads ``packet.blocks`` (kind + metadata) and the agent's name only — it does
not touch stores and takes no kwargs beyond those.
"""

from __future__ import annotations

from task_center.task_guidance.builders import (
    build_explorer_task_guidance,
    build_task_guidance,
)

__all__ = [
    "build_explorer_task_guidance",
    "build_task_guidance",
]
