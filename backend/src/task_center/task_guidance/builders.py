"""Role-specific task-guidance prose builders.

Each builder takes ``(agent_def, packet, scope)`` and returns the prose body
the composer wraps in ``<Task Guidance>``. Builders read ``block.kind`` and
discrete ``block.metadata`` keys — never kwargs, never stores, never the
agent definition's frontmatter beyond what the dispatch needs to route.

Discrete metadata signals (added in goal_iteration_frame.py / attempt_landscape.py):

* ``metadata["iteration_no"]`` — set on the ``<iteration_goal>`` /
  ``<goal_current_iteration>`` block carrying the current iteration's
  sequence number (str). Read as ``int(...)`` here.
* ``metadata["has_deferred_goal_for_next_iteration"] == "true"`` — set on the
  ``<deferred_goal_for_next_iteration>`` child of ``<attempt_plan>`` when the
  current attempt is a continues-goal (partial) plan.
* ``block.kind == "failed_attempt_landscape"`` — any block of this kind in
  the packet means the planner is retrying after a failed attempt.
* ``block.kind == "dependency_summary"`` — any block of this kind in the
  packet means the generator's assigned task has at least one dependency.

**Tag mention convention.** Prose references context structure via XML tag
mentions (``<attempt_plan>``, ``<iteration status="prior">``) instead of
markdown heading text. Tool parameter names keep their backtick form
(``deferred_goal_for_next_iteration``) so the planner can tell a context section
from a tool argument at a glance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.packet import ContextBlock, ContextPacket

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition
    from task_center.context_engine.scope import ContextScope


def _current_iteration_no(packet: ContextPacket) -> int:
    """Pull the current iteration's sequence number from packet metadata.

    Reads the first block carrying ``metadata["iteration_no"]`` (set on the
    ``<goal_current_iteration>`` block for iteration 1 and on the
    ``<iteration_goal>`` child for iteration N≥2). The frame recipes guarantee
    exactly one such block.
    """
    for block in packet.blocks:
        raw = block.metadata.get("iteration_no")
        if raw:
            return int(raw)
    return 1


def _has_failed_attempts(packet: ContextPacket) -> bool:
    return any(b.kind == "failed_attempt_landscape" for b in packet.blocks)


def _has_dependency_results(packet: ContextPacket) -> bool:
    return any(b.kind == "dependency_summary" for b in packet.blocks)


def _has_deferred_goal_for_next_iteration_plan(packet: ContextPacket) -> bool:
    for block in packet.blocks:
        if block.metadata.get("has_deferred_goal_for_next_iteration") == "true":
            return True
    return False


def build_planner_task_guidance(
    *,
    agent_def: AgentDefinition,  # noqa: ARG001 - dispatch signature
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - dispatch signature
) -> str:
    """Planner task-guidance.

    Branches on ``(iteration_no == 1 vs >= 2) × (has_failed_attempts)``.
    """
    iteration_no = _current_iteration_no(packet)
    has_failed_attempts = _has_failed_attempts(packet)
    if iteration_no == 1 and not has_failed_attempts:
        return (
            "You are planning the first attempt for this iteration's goal. "
            "No prior attempts exist in this iteration. Propose a plan that "
            "decomposes the iteration goal into generator tasks with a clear "
            "evaluation contract. If you cannot solve the iteration in one "
            "attempt, submit a partial plan with a `deferred_goal_for_next_iteration` "
            "so the next iteration can pick up where this one ends. "
            "When the iteration goal is a list of independent items (for "
            "example a PR-description changelog of features and fixes), "
            "prefer a wide parallel DAG with one sibling generator task per "
            "item and one criterion per item; coalescing into a single "
            "'all items done' criterion turns partial progress into total "
            "failure. If one attempt cannot fit every item, bind a tighter "
            "set of items here. If you defer work via `deferred_goal_for_next_iteration`, "
            "make that handoff the next bounded slice only; do not dump "
            "the entire remaining backlog into it."
        )
    if iteration_no == 1 and has_failed_attempts:
        return (
            "You are planning a follow-up attempt for this iteration's goal. "
            "One or more prior attempts in this iteration failed (see the "
            '`<attempt status="failed">` blocks inside '
            '`<iteration status="current">`). Diagnose why earlier attempts '
            "failed and choose a meaningfully different decomposition, "
            "scope, or evaluation contract — do not repeat a failing "
            "strategy. When the iteration goal is a list of independent "
            "items, the prior failure landscape tells you which items "
            "already passed their criterion and which did not; keep one "
            "criterion per item and narrow this attempt's scope to the "
            "failing or skipped items rather than re-running the full list."
        )
    if iteration_no >= 2 and not has_failed_attempts:
        return (
            "You are planning the first attempt for a later iteration. The "
            "prior iteration produced concrete results (see "
            '`<iteration status="prior">` blocks). Your decomposition should '
            "continue from where the prior iteration ended — build on prior "
            "outputs, do not redo their work. The `<iteration_goal>` inside "
            '`<iteration status="current">` is the authoritative scope for '
            "this planner; use the standalone `<goal>` only for orientation "
            "and do not add backlog items that the current iteration's goal "
            "did not explicitly name. When the iteration goal is a list of "
            "independent items, consult `<iteration status=\"prior\">` for "
            "which items already passed and plan only the remaining items, "
            "keeping one criterion per item so the evaluator can report "
            "per-item pass/fail rather than a single coarse verdict."
        )
    return (
        "You are planning a follow-up attempt for a later iteration. "
        'Earlier iterations produced results (see `<iteration status="prior">` '
        "blocks) and one or more attempts in the current iteration have "
        'failed (see the `<attempt status="failed">` blocks inside '
        '`<iteration status="current">`). Build on prior-iteration '
        "outputs and avoid repeating the failure modes from the current "
        "iteration. The `<iteration_goal>` inside "
        '`<iteration status="current">` is the authoritative scope for '
        "this planner; use the standalone `<goal>` only for orientation "
        "and do not add backlog items that the current iteration's goal "
        "did not explicitly name. When the iteration goal is a list of "
        'independent items, lean on `<iteration status="prior">` for '
        'done items and on the `<attempt status="failed">` blocks for '
        "items the current iteration has already tried unsuccessfully; "
        "keep one criterion per item and narrow scope to items with a "
        "credible path to passing this attempt."
    )


def build_generator_task_guidance(
    *,
    agent_def: AgentDefinition,  # noqa: ARG001 - dispatch signature
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - dispatch signature
) -> str:
    """Generator task-guidance. Branches on whether deps are present."""
    if _has_dependency_results(packet):
        return (
            "You are executing one generator task with one or more "
            "dependency outputs already available (see `<dependency_results>`). "
            "Treat the dependency outputs as fixed inputs; do not redo their "
            "work. Read the `<assigned_task>` and produce the deliverable, "
            "then submit per your role's contract."
        )
    return (
        "You are executing one generator task. This task has no "
        "dependencies on other generator tasks in the same attempt. "
        "Read the `<assigned_task>` below and produce the deliverable, "
        "then submit per your role's contract."
    )


def build_evaluator_task_guidance(
    *,
    agent_def: AgentDefinition,  # noqa: ARG001 - dispatch signature
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - dispatch signature
) -> str:
    """Evaluator task-guidance. Branches on partial-plan signal."""
    if _has_deferred_goal_for_next_iteration_plan(packet):
        return (
            "You are evaluating an intentionally partial attempt (see the "
            "`<deferred_goal_for_next_iteration>` child of `<attempt_plan>`). "
            "This attempt is not expected to solve the full iteration goal "
            "— it is expected to make progress and hand off remaining work "
            "via `deferred_goal_for_next_iteration`. Pass/fail against "
            "`<evaluation_criteria>` for what this attempt promised; "
            "do not penalize for incomplete work that was explicitly deferred."
        )
    return (
        "You are evaluating a complete attempt. Use `<attempt_plan>` "
        "and `<evaluation_criteria>` as your authority — pass/fail the "
        "attempt against the criteria, not against your own preferences. "
        "Treat the iteration goal as the scope; do not penalize the "
        "attempt for work outside the iteration goal."
    )


def build_explorer_task_guidance() -> str:
    """Identity + format prose for the explorer subagent.

    Subagents have no ContextScope and no composer involvement (isolation
    contract — see ``tools/subagent/run_subagent.py``). The explorer launches
    in the two-user-message shape by passing this prose as the spawn prompt
    and the caller's free-text task prompt as ``initial_messages[0]``.

    Returned as a plain string (no ``<Task Guidance>`` wrapping) — the
    subagent caller embeds it directly.
    """
    return (
        "You are the explorer subagent. Investigate the task in the parent's "
        "user message and deliver concrete findings — file paths, line "
        "numbers, and specific symbols — not vague hand-waves. Surface any "
        "missing context the parent will need to act on the findings, and "
        "call out obvious areas you skipped. Finish by calling your terminal "
        "tool submit_exploration_result."
    )


__all__ = [
    "build_evaluator_task_guidance",
    "build_explorer_task_guidance",
    "build_generator_task_guidance",
    "build_planner_task_guidance",
]
