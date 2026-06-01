"""Task guidance derived from :class:`AgentContext`."""

from __future__ import annotations

from workflow.context_engine.context import AgentContext


def render_task_guidance(context: AgentContext) -> str:
    return "\n\n".join(
        part
        for part in (
            _render_context_contents(context),
            _render_context_limits(context),
            _render_what_to_do(context),
        )
        if part
    )


def _render_context_contents(context: AgentContext) -> str:
    rows = {
        "planner": (
            "- <workflow>: workflow goal and current planning frame",
            "- <prior_iterations>: reducer outcomes from prior iterations",
            "- <current_iteration>: current goal and previous attempt evidence",
        ),
        "generator": (
            "- <dependencies>: outcomes produced by dependency tasks",
            "- <assigned_task>: your assigned task",
        ),
        "reducer": (
            "- <dependencies>: outcomes produced by dependency tasks",
            "- <assigned_task>: your assigned task",
        ),
    }[context.role]
    return "What's in context:\n" + "\n".join(rows)


def _render_context_limits(context: AgentContext) -> str | None:
    if not context.context_limits:
        return None
    return "Context limits:\n" + "\n".join(f"- {item}" for item in context.context_limits)


def _render_what_to_do(context: AgentContext) -> str:
    return f"What to do:\n- {context.directive}"


__all__ = ["render_task_guidance"]
