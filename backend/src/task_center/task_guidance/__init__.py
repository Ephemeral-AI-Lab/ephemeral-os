"""Role-specific task-guidance prose builders.

A "task guidance" is the per-agent ``<Task Guidance>`` body assembled at launch
time from the rendered context packet and agent definition. Builders read
``packet.blocks`` (kind + metadata) only — they do not touch stores and they
take no kwargs.
"""

from __future__ import annotations

from task_center.task_guidance.builders import (
    build_evaluator_task_guidance,
    build_explorer_task_guidance,
    build_generator_task_guidance,
    build_planner_task_guidance,
)

__all__ = [
    "build_evaluator_task_guidance",
    "build_explorer_task_guidance",
    "build_generator_task_guidance",
    "build_planner_task_guidance",
]
