"""Launch contexts passed to planner, executor, and evaluator agents.

Each context is a typed dataclass with a ``to_*_prompt`` method that renders
the structural context as labeled-heading text (the envelope format from
``docs/architecture/agent-system-prompts.md``). The renderer is the single
wire-format source of truth — agents see exactly what these methods emit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from task_center.model import HarnessGraphId, TaskId, TaskSummary


def _render_summaries(summaries: list[TaskSummary]) -> str:
    if not summaries:
        return "(none)"
    return "\n".join(
        f"- [{s.kind}] {s.source_task_id}: {s.text}" for s in summaries
    )


@dataclass
class PlannerLaunchContext:
    """Structural input for a planner task.

    Built once by ``build_planner_launch_context`` at handoff time and frozen
    as the planner's ``task.input`` — the planner has no separate work payload.
    """

    task_detail: str
    caller_task_id: TaskId
    caller_role: Literal["executor", "evaluator"]
    caller_input: str
    requested_goal: str
    upstream_handoff_summaries: list[TaskSummary] = field(default_factory=list)
    prior_planner_handoff: list[TaskSummary] = field(default_factory=list)
    completed_child_summaries: list[TaskSummary] = field(default_factory=list)
    failed_child_summaries: list[TaskSummary] = field(default_factory=list)
    dependency_blocked_summaries: list[TaskSummary] = field(default_factory=list)

    def to_planner_input(self) -> str:
        return "\n\n".join(
            [
                f"## CALLER_ROLE\n{self.caller_role}",
                f"## CALLER_INPUT\n{self.caller_input}",
                f"## PARENT_GOAL\n{self.requested_goal}",
                f"## REQUESTED_GAP\n{self.task_detail}",
                f"## PRIOR_PLANNER_HANDOFFS\n{_render_summaries(self.prior_planner_handoff)}",
                f"## COMPLETED_CHILD_SUMMARIES\n{_render_summaries(self.completed_child_summaries)}",
                f"## FAILED_CHILD_SUMMARIES\n{_render_summaries(self.failed_child_summaries)}",
                f"## DEPENDENCY_BLOCKED_SUMMARIES\n{_render_summaries(self.dependency_blocked_summaries)}",
            ]
        )


@dataclass
class DependencyBundle:
    """One DONE dependency's input + summaries, packaged for an executor."""

    task_id: TaskId
    task_input: str
    summaries: list[TaskSummary]


@dataclass
class ExecutorLaunchContext:
    """Structural context for an executor task at dispatch time.

    Unlike ``PlannerLaunchContext``, this is rebuilt on every spawn because the
    set of DONE dependencies changes asynchronously. ``task.input`` stays the
    work payload; this wraps it with the dependency summaries the executor is
    allowed to see.
    """

    task_id: TaskId
    task_input: str
    harness_graph_id: HarnessGraphId | None
    completed_dependencies: list[DependencyBundle] = field(default_factory=list)

    def to_executor_prompt(self) -> str:
        if not self.completed_dependencies:
            deps_block = "(none)"
        else:
            parts: list[str] = []
            for dep in self.completed_dependencies:
                summary_lines = "\n".join(
                    f"  - [{s.kind}] {s.text}" for s in dep.summaries
                ) or "  (no summaries)"
                parts.append(
                    f"### {dep.task_id}\n"
                    f"input: {dep.task_input}\n"
                    f"summaries:\n{summary_lines}"
                )
            deps_block = "\n\n".join(parts)
        return (
            f"## TASK_INPUT\n{self.task_input}\n\n"
            f"## DEPENDENCY_SUMMARIES\n{deps_block}"
        )


@dataclass
class EvaluatorLaunchContext:
    """Structural context for an evaluator task at dispatch time.

    Rebuilt on every spawn (child summaries arrive asynchronously). Includes
    the parent goal and planner handoff so the evaluator can grade against
    intent, not just child self-reports.
    """

    task_id: TaskId
    task_input: str
    harness_graph_id: HarnessGraphId
    parent_goal: str
    planner_handoff: list[TaskSummary] = field(default_factory=list)
    completed_child_summaries: list[TaskSummary] = field(default_factory=list)
    failed_child_summaries: list[TaskSummary] = field(default_factory=list)

    def to_evaluator_prompt(self) -> str:
        return "\n\n".join(
            [
                f"## PARENT_GOAL\n{self.parent_goal}",
                f"## PLANNER_HANDOFF\n{_render_summaries(self.planner_handoff)}",
                f"## COMPLETED_CHILD_SUMMARIES\n{_render_summaries(self.completed_child_summaries)}",
                f"## FAILED_CHILD_SUMMARIES\n{_render_summaries(self.failed_child_summaries)}",
                f"## TASK_INPUT\n{self.task_input}",
            ]
        )
