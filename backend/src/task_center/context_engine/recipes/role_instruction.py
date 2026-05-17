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
            "next iteration can pick up where this one ends. "
            "When the iteration goal is a list of independent items (for "
            "example a PR-description changelog of features and fixes), "
            "prefer a wide parallel DAG with one sibling generator task per "
            "item and one criterion per item; coalescing into a single "
            "'all items done' criterion turns partial progress into total "
            "failure. If one attempt cannot fit every item, bind a tighter "
            "set of items here. If you defer work via continuation_goal, make "
            "that continuation_goal the next bounded slice only; do not dump "
            "the entire remaining backlog into it."
        )
    elif iteration_sequence_no == 1 and has_failed_attempts:
        text = (
            "You are planning a follow-up attempt for this iteration's goal. "
            "One or more prior attempts in this iteration failed (see "
            "Prior Failed Attempts). Diagnose why earlier attempts failed and "
            "choose a meaningfully different decomposition, scope, or "
            "evaluation contract — do not repeat a failing strategy. "
            "When the iteration goal is a list of independent items, the "
            "prior failure landscape tells you which items already passed "
            "their criterion and which did not; keep one criterion per item "
            "and narrow this attempt's scope to the failing or skipped "
            "items rather than re-running the full list."
        )
    elif iteration_sequence_no >= 2 and not has_failed_attempts:
        text = (
            "You are planning the first attempt for a later iteration. The "
            "prior iteration produced concrete results (see Previous "
            "Iteration Results). Your decomposition should continue from "
            "where the prior iteration ended — build on prior outputs, do "
            "not redo their work. The Current Iteration text is the "
            "authoritative scope for this planner; use the original Goal only "
            "for orientation and do not add backlog items that Current "
            "Iteration did not explicitly name. "
            "When the iteration goal is a list of independent items, consult "
            "Previous Iteration Results for which items already passed and "
            "plan only the remaining items, keeping one criterion per item "
            "so the evaluator can report per-item pass/fail rather than a "
            "single coarse verdict."
        )
    else:
        text = (
            "You are planning a follow-up attempt for a later iteration. "
            "Earlier iterations produced results (see Previous Iteration "
            "Results) and one or more attempts in the current iteration have "
            "failed (see Prior Failed Attempts). Build on prior-iteration "
            "outputs and avoid repeating the failure modes from the current "
            "iteration. The Current Iteration text is the authoritative scope "
            "for this planner; use the original Goal only for orientation and "
            "do not add backlog items that Current Iteration did not "
            "explicitly name. "
            "When the iteration goal is a list of independent items, lean "
            "on Previous Iteration Results for done items and on Prior "
            "Failed Attempts for items the current iteration has already "
            "tried unsuccessfully; keep one criterion per item and narrow "
            "scope to items with a credible path to passing this attempt."
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


# Per-terminal-tool advisor instructions. The advisor is launched by a parent
# agent that is ABOUT to submit one specific terminal; the dispatch keys are
# the terminal tool names under ``backend/src/tools/submission/submit_*.py``.
# Keep each entry distinct so the advisor's review prompt is grounded in the
# specific call shape, not a generic "look at the submission" instruction.
_ADVISOR_INSTRUCTIONS: dict[str, str] = {
    "submit_plan_closes_goal": (
        "You are advising on a planner submission that proposes to CLOSE the "
        "iteration goal in this attempt (a full plan). Review the proposed "
        "decomposition against the iteration goal: does every required item "
        "have a generator task, are evaluation criteria one-per-item where "
        "the goal is a list, and does the plan avoid a coarse 'all items "
        "done' criterion that turns partial progress into total failure? "
        "Flag missing items, mis-scoped tasks, and dependency mistakes."
    ),
    "submit_plan_continues_goal": (
        "You are advising on a planner submission that DEFERS remaining work "
        "via a continuation_goal (a partial plan). Confirm the partial scope "
        "is genuinely smaller than the iteration goal and that the "
        "continuation_goal is the next bounded slice — NOT a dump of the "
        "entire remaining backlog. Verify the in-scope items have one "
        "evaluation criterion each and the deferred items are clearly "
        "named so the next iteration can pick up cleanly."
    ),
    "submit_evaluation_success": (
        "You are advising on an evaluator submission that proposes to PASS "
        "the attempt. Re-read the Evaluation Criteria; for each criterion, "
        "verify the attempt's deliverables actually satisfy it. Flag any "
        "criterion the evaluator is glossing over and any deliverable that "
        "satisfies the letter but not the intent of the criterion."
    ),
    "submit_evaluation_failure": (
        "You are advising on an evaluator submission that proposes to FAIL "
        "the attempt. Confirm the failing criteria are accurately named and "
        "that the failure is on the attempt's promised scope (NOT on work "
        "deferred via continuation_goal). Flag failures that punish the "
        "attempt for items outside the iteration goal."
    ),
    "submit_execution_success": (
        "You are advising on an executor submission that proposes SUCCESS. "
        "Verify the assigned task's deliverable actually exists at the "
        "claimed location, satisfies the task's specification, and is "
        "consistent with the dependency outputs. Flag stub deliverables, "
        "TODO markers, and any divergence from the task contract."
    ),
    "submit_execution_failure": (
        "You are advising on an executor submission that proposes FAILURE. "
        "Confirm the failure mode is real, not a misdiagnosis. Verify the "
        "executor has tried the obvious remediation paths before giving up. "
        "Flag premature failures and failures that hide a fixable bug."
    ),
    "submit_execution_handoff": (
        "You are advising on an executor submission that HANDS OFF further "
        "work to a follow-up task (handoff, not failure). Verify the handoff "
        "scope is specific and actionable; flag vague handoffs that just "
        "kick the problem downstream without naming what's needed."
    ),
    "submit_verification_success": (
        "You are advising on a verifier submission that proposes the "
        "deliverable PASSES verification. Re-check the verification criteria "
        "against the actual deliverable; flag missed checks and any claim "
        "that the deliverable passes solely because the verifier didn't "
        "look hard enough."
    ),
    "submit_verification_failure": (
        "You are advising on a verifier submission that proposes the "
        "deliverable FAILS verification. Confirm the failing checks are "
        "real and accurately described. Flag failures that are not the "
        "verifier's responsibility (e.g. issues that belong to a different "
        "task) so the failure routes to the right resolver."
    ),
    "submit_exploration_result": (
        "You are advising on an explorer subagent's finding submission. "
        "Confirm the findings are concrete (file paths, line numbers, "
        "specific symbols), not vague hand-waves. Flag missing context the "
        "parent will need to act on the findings, and any obvious areas the "
        "explorer skipped."
    ),
}


_ADVISOR_DEFAULT: str = (
    "You are advising on a parent agent's pending terminal submission. "
    "Review the proposed tool name and payload against the parent's "
    "inherited context. Return a concise verdict, the strongest reason "
    "for or against, and any risks the parent should weigh before "
    "submitting."
)


def advisor_instruction(*, tool_name: str) -> ContextBlock:
    """Per-terminal-tool role instruction for the advisor helper.

    Branches on ``tool_name`` (the terminal the parent is about to call).
    Unknown / future terminals fall back to ``_ADVISOR_DEFAULT``.
    """
    text = _ADVISOR_INSTRUCTIONS.get(tool_name, _ADVISOR_DEFAULT)
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


def resolver_instruction() -> ContextBlock:
    """General role instruction for the resolver helper.

    Resolver is transcript-mode: it consults the parent transcript block to
    see the failing tool calls / verifier or evaluator issues that triggered
    its launch, then edits files to resolve them.
    """
    text = (
        "You are the resolver. The parent agent's verifier or evaluator "
        "flagged issues it could not resolve itself. Read the issues passed "
        "in the resolver request, consult the parent transcript (see "
        "# Parent transcript) for the failing tool calls and context, and "
        "edit files as needed to resolve every issue. When done, summarize "
        "what you changed and which issues you resolved via "
        "submit_resolver_result."
    )
    return _block(text)
