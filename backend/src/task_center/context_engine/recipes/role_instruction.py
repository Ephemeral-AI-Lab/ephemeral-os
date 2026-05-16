"""Context-aware ``# How to Proceed`` blocks for non-entry agents.

This is a helper module, NOT a recipe. ``recipes/__init__.py`` auto-registers
every module-level attribute whose name ends with ``_RECIPE`` and is a
:class:`ContextRecipe` instance. **Do not declare any ``*_RECIPE`` symbol in
this module** — a registration-safety test asserts that the public surface
exposes no such attribute.

Each helper returns a single :class:`ContextBlock` at REQUIRED priority. The
text branches on scope-derived facts the caller has already computed; the
helper never touches stores. Text is profile-variant agnostic — it never
names terminal submission tools, since "Generator" alone resolves to four
distinct profile variants and "Planner" to two.
"""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)


def _block(text: str) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.ROLE_INSTRUCTION,
        priority=ContextPriority.REQUIRED,
        text=text,
    )


def planner_instruction(
    *,
    iteration_sequence_no: int,
    has_failed_attempts: bool,
) -> ContextBlock:
    """Hint for the planner role.

    Branches on (iteration_sequence_no == 1 vs >= 2) × (has_failed_attempts).
    """
    if iteration_sequence_no == 1 and not has_failed_attempts:
        text = (
            "You are planning the first attempt for this iteration's goal. "
            "No prior attempts exist in this iteration. Propose a plan that "
            "decomposes the iteration goal into generator tasks with a clear "
            "evaluation contract. If you cannot solve the iteration in one "
            "attempt, submit a partial plan with a continuation_goal so the "
            "next iteration can pick up where this one ends."
        )
    elif iteration_sequence_no == 1 and has_failed_attempts:
        text = (
            "You are planning a follow-up attempt for this iteration's goal. "
            "One or more prior attempts in this iteration failed (see "
            "Prior Failed Attempts). Diagnose why earlier attempts failed and "
            "choose a meaningfully different decomposition, scope, or "
            "evaluation contract — do not repeat a failing strategy."
        )
    elif iteration_sequence_no >= 2 and not has_failed_attempts:
        text = (
            "You are planning the first attempt for a later iteration. The "
            "prior iteration produced concrete results (see Previous "
            "Iteration Results). Your decomposition should continue from "
            "where the prior iteration ended — build on prior outputs, do "
            "not redo their work."
        )
    else:
        text = (
            "You are planning a follow-up attempt for a later iteration. "
            "Earlier iterations produced results (see Previous Iteration "
            "Results) and one or more attempts in the current iteration have "
            "failed (see Prior Failed Attempts). Build on prior-iteration "
            "outputs and avoid repeating the failure modes from the current "
            "iteration."
        )
    return _block(text)


def generator_instruction(*, has_deps: bool) -> ContextBlock:
    """Hint for one generator task.

    Branches on whether the assigned task has dependency outputs available.
    """
    if has_deps:
        text = (
            "You are executing one generator task with one or more "
            "dependency outputs already available (see Dependency Results). "
            "Treat the dependency outputs as fixed inputs; do not redo their "
            "work. Read the assigned task and produce the deliverable, then "
            "submit per your role's contract."
        )
    else:
        text = (
            "You are executing one generator task. This task has no "
            "dependencies on other generator tasks in the same attempt. "
            "Read the assigned task below and produce the deliverable, then "
            "submit per your role's contract."
        )
    return _block(text)


def evaluator_instruction(*, is_partial: bool) -> ContextBlock:
    """Hint for the evaluator role.

    Branches on whether the attempt is a partial (continuation) plan.
    """
    if is_partial:
        text = (
            "You are evaluating an intentionally partial attempt (see "
            "Partial Plan Boundary). This attempt is not expected to solve "
            "the full iteration goal — it is expected to make progress and "
            "hand off remaining work via continuation_goal. Pass/fail "
            "against the Evaluation Criteria for what this attempt promised; "
            "do not penalize for incomplete work that was explicitly deferred."
        )
    else:
        text = (
            "You are evaluating a complete attempt. Use the Attempt Plan "
            "and the Evaluation Criteria as your authority — pass/fail the "
            "attempt against the criteria, not against your own preferences. "
            "Treat the iteration goal as the scope; do not penalize the "
            "attempt for work outside the iteration goal."
        )
    return _block(text)
