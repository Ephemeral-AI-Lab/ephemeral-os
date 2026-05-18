"""Exact-name dispatch for per-agent task-guidance builders.

The composer looks up the builder by ``agent_def.name`` after variant
resolution — variant targets (``planner_full_only``,
``executor_success_handoff``, ``executor_success_failure``) appear in this
table directly, not by way of their base profile.

Names absent from this table get no row 3. That includes ``entry_executor``
(2-row launch shape) and the ``executor`` router profile (purely a variant
parent, never a launch target).

Helpers and subagents (``advisor``, ``resolver``, ``explorer``) bypass the
composer entirely — they live in ``tools/ask_helper/`` and
``tools/subagent/run_subagent.py``. The explorer's identity/format prose
still lives in :func:`task_guidance.builders.build_explorer_task_guidance`,
read directly by ``tools/subagent/run_subagent.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from task_center.task_guidance.builders import (
    build_evaluator_task_guidance,
    build_generator_task_guidance,
    build_planner_task_guidance,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition
    from task_center.context_engine.packet import ContextPacket
    from task_center.context_engine.scope import ContextScope


TaskGuidanceBuilder = Callable[..., str]


TASK_GUIDANCE_BUILDERS: dict[str, TaskGuidanceBuilder] = {
    "planner": build_planner_task_guidance,
    "planner_full_only": build_planner_task_guidance,
    "executor_success_handoff": build_generator_task_guidance,
    "executor_success_failure": build_generator_task_guidance,
    # ``generator_verifier.md`` registers as ``name: verifier`` in its
    # frontmatter; the dispatch key matches the registered agent name, not
    # the source filename.
    "verifier": build_generator_task_guidance,
    "evaluator": build_evaluator_task_guidance,
}


def task_guidance_builder_for(agent_name: str) -> TaskGuidanceBuilder | None:
    """Look up a task-guidance builder by exact agent name.

    ``None`` means "no row 3" — the launcher collapses to a 2-row entry
    shape or skips ``<Task Guidance>`` entirely.
    """
    return TASK_GUIDANCE_BUILDERS.get(agent_name)


__all__ = ["TASK_GUIDANCE_BUILDERS", "task_guidance_builder_for"]
