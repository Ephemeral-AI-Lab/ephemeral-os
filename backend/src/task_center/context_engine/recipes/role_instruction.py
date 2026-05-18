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

**Tag mention convention.** Role text references context structure via XML
tag mentions (``<attempt_plan>``, ``<iteration status="prior">``) instead of
markdown heading text. Tool parameters keep their backtick form
(``next_iteration_handoff_goal``) so the planner can tell a context section
from a tool argument at a glance.
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
    iteration_no: int,
    has_failed_attempts: bool,
) -> ContextBlock:
    """Hint for the planner role.

    Branches on (iteration_no == 1 vs >= 2) × (has_failed_attempts).
    """
    if iteration_no == 1 and not has_failed_attempts:
        text = (
            "You are planning the first attempt for this iteration's goal. "
            "No prior attempts exist in this iteration. Propose a plan that "
            "decomposes the iteration goal into generator tasks with a clear "
            "evaluation contract. If you cannot solve the iteration in one "
            "attempt, submit a partial plan with a `next_iteration_handoff_goal` "
            "so the next iteration can pick up where this one ends. "
            "When the iteration goal is a list of independent items (for "
            "example a PR-description changelog of features and fixes), "
            "prefer a wide parallel DAG with one sibling generator task per "
            "item and one criterion per item; coalescing into a single "
            "'all items done' criterion turns partial progress into total "
            "failure. If one attempt cannot fit every item, bind a tighter "
            "set of items here. If you defer work via `next_iteration_handoff_goal`, "
            "make that handoff the next bounded slice only; do not dump "
            "the entire remaining backlog into it."
        )
    elif iteration_no == 1 and has_failed_attempts:
        text = (
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
    elif iteration_no >= 2 and not has_failed_attempts:
        text = (
            "You are planning the first attempt for a later iteration. The "
            "prior iteration produced concrete results (see "
            '`<iteration status="prior">` blocks). Your decomposition should '
            "continue from where the prior iteration ended — build on prior "
            "outputs, do not redo their work. The `<iteration_goal>` inside "
            '`<iteration status="current">` is the authoritative scope for '
            "this planner; use the standalone `<goal>` only for orientation "
            "and do not add backlog items that the current iteration's goal "
            "did not explicitly name. When the iteration goal is a list of "
            'independent items, consult `<iteration status="prior">` for '
            "which items already passed and plan only the remaining items, "
            "keeping one criterion per item so the evaluator can report "
            "per-item pass/fail rather than a single coarse verdict."
        )
    else:
        text = (
            "You are planning a follow-up attempt for a later iteration. "
            'Earlier iterations produced results (see `<iteration status="prior">` '
            'blocks) and one or more attempts in the current iteration have '
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
    return _block(text)


def generator_instruction(*, has_deps: bool) -> ContextBlock:
    """Hint for one generator task.

    Branches on whether the assigned task has dependency outputs available.
    """
    if has_deps:
        text = (
            "You are executing one generator task with one or more "
            "dependency outputs already available (see `<dependency_results>`). "
            "Treat the dependency outputs as fixed inputs; do not redo their "
            "work. Read the `<assigned_task>` and produce the deliverable, "
            "then submit per your role's contract."
        )
    else:
        text = (
            "You are executing one generator task. This task has no "
            "dependencies on other generator tasks in the same attempt. "
            "Read the `<assigned_task>` below and produce the deliverable, "
            "then submit per your role's contract."
        )
    return _block(text)


def evaluator_instruction(*, is_partial: bool) -> ContextBlock:
    """Hint for the evaluator role.

    Branches on whether the attempt is a partial (continuation) plan. The
    structural signal — that the attempt declared a handoff — travels via the
    ``<next_iteration_handoff_goal>`` child of ``<attempt_plan>``. The
    behavioral guidance below is the single source of truth for partial-plan
    semantics after the PARTIAL_PLAN_BOUNDARY block was removed.
    """
    if is_partial:
        text = (
            "You are evaluating an intentionally partial attempt (see the "
            "`<next_iteration_handoff_goal>` child of `<attempt_plan>`). "
            "This attempt is not expected to solve the full iteration goal "
            "— it is expected to make progress and hand off remaining work "
            "via `next_iteration_handoff_goal`. Pass/fail against "
            "`<evaluation_criteria>` for what this attempt promised; "
            "do not penalize for incomplete work that was explicitly deferred."
        )
    else:
        text = (
            "You are evaluating a complete attempt. Use `<attempt_plan>` "
            "and `<evaluation_criteria>` as your authority — pass/fail the "
            "attempt against the criteria, not against your own preferences. "
            "Treat the iteration goal as the scope; do not penalize the "
            "attempt for work outside the iteration goal."
        )
    return _block(text)


def explorer_instruction() -> ContextBlock:
    """Identity + format role instruction for the explorer subagent.

    Subagents have no ContextScope and no composer involvement (isolation
    contract — see ``tools/subagent/run_subagent.py``). The explorer launches
    in the two-user-message shape by passing this block's text as the spawn
    prompt and the caller's free-text task prompt as
    ``initial_messages[0]``. Text mirrors the
    ``submit_exploration_result`` advisor entry: emphasizes concrete
    findings (file paths, line numbers, specific symbols).
    """
    text = (
        "You are the explorer subagent. Investigate the task in the parent's "
        "user message and deliver concrete findings — file paths, line "
        "numbers, and specific symbols — not vague hand-waves. Surface any "
        "missing context the parent will need to act on the findings, and "
        "call out obvious areas you skipped. Finish by calling your terminal "
        "tool submit_exploration_result."
    )
    return _block(text)
