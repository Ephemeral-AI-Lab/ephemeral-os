"""``TerminalToolDescriptor`` registry — terminal-tool semantics in one place.

Each entry pairs the parent-facing ``selection_guidance`` ("Call when …")
with the advisor-facing ``advisor_review_focus`` ("Verify … Flag …"). The
parent's ``user_msg_2`` and the advisor's ``user_msg_2`` both render from
this registry so the two prompts never drift.

Catalog rendering is consumed in two places:

* ``AgentEntryComposer.compose()`` produces two byte-equal
  ``<terminal_tool_selection>`` blocks at launch time: one inside
  ``task_guidance`` (row 3) and one inside ``skill`` (row 4) whenever the
  resolved ``agent_def`` declares both task-guidance prose and a skill.
* ``ask_advisor`` calls :func:`render_terminal_catalog` with
  ``focus="advisor_review_focus"`` keyed on the parent's terminals, so the
  advisor sees the same set viewed through the auditor's lens.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CatalogFocus = Literal["selection_guidance", "advisor_review_focus"]


class TerminalToolDescriptor(BaseModel):
    """Two views on one terminal tool: parent-facing + advisor-facing."""

    name: str = Field(..., min_length=1)
    selection_guidance: str = Field(..., min_length=1)
    advisor_review_focus: str = Field(..., min_length=1)


TERMINAL_DESCRIPTORS: dict[str, TerminalToolDescriptor] = {
    "submit_generator_success": TerminalToolDescriptor(
        name="submit_generator_success",
        selection_guidance=(
            "Call when the `<assigned_task>` deliverable is complete, exists "
            "at the claimed location, satisfies the task specification, and "
            "any verification the criteria specify has been run and passed."
        ),
        advisor_review_focus=(
            "Verify the `<assigned_task>` deliverable actually exists at the "
            "claimed location, satisfies the task specification, and is "
            "consistent with the `<dependency>` outputs. Flag stub "
            "deliverables, TODO markers, and any divergence from the task "
            "contract."
        ),
    ),
    "submit_generator_failure": TerminalToolDescriptor(
        name="submit_generator_failure",
        selection_guidance=(
            "Call when the `<assigned_task>` cannot be completed in this "
            "attempt. Summarize the failure and the evidence."
        ),
        advisor_review_focus=(
            "Confirm the failure is real and specific, not a premature "
            "give-up. Verify the generator tried the obvious remediation "
            "paths and did not hide solvable work behind a vague failure."
        ),
    ),
    "submit_workflow_handoff": TerminalToolDescriptor(
        name="submit_workflow_handoff",
        selection_guidance=(
            "Call when the tool is available, you have not started edits, "
            "and the `<assigned_task>` is too broad or complex for one "
            "executor pass. Name the delegated goal and why planner "
            "decomposition is needed."
        ),
        advisor_review_focus=(
            "Verify the generator has not started edits and the handoff scope "
            "is specific and actionable. Flag vague handoffs that kick the "
            "problem downstream without naming the delegated goal, findings, "
            "or why decomposition is needed."
        ),
    ),
    "submit_plan_closes_goal": TerminalToolDescriptor(
        name="submit_plan_closes_goal",
        selection_guidance=(
            "Call when this iteration's generator work and reducer outcomes "
            "are enough to finish the current `<iteration_goal>`. After those "
            "outcomes exist, no known follow-up planner pass is needed."
        ),
        advisor_review_focus=(
            "The planner proposes to CLOSE the current `<iteration_goal>` in "
            "this iteration. Review the DAG against `<iteration_goal>`: does "
            "every required item have generator work, do reducer outcomes "
            "cover the goal, and is any follow-up planner pass still known to "
            "be needed? Flag missing items, mis-scoped tasks, and dependency "
            "mistakes."
        ),
    ),
    "submit_plan_defers_goal": TerminalToolDescriptor(
        name="submit_plan_defers_goal",
        selection_guidance=(
            "Call when this attempt has a concrete plan for a bounded "
            "iteration and the next useful step is another planner pass after "
            "the reducer outcomes exist. The "
            "`deferred_goal_for_next_iteration` is the next planner's scope, "
            "not a backlog dump."
        ),
        advisor_review_focus=(
            "The planner DEFERS remaining work via a "
            "`deferred_goal_for_next_iteration`. Confirm the current DAG is a "
            "finished bounded iteration, its reducer outcomes will be useful "
            "context for the next planner, and the deferred goal is a "
            "self-contained next scope rather than a backlog dump. Flag "
            "unfinished current-iteration work hidden as deferral."
        ),
    ),
    "submit_reduction_success": TerminalToolDescriptor(
        name="submit_reduction_success",
        selection_guidance=(
            "Call when you have finished the work in `<assigned_task>` using "
            "the `<dependencies>` outcomes as context. The `outcome` must "
            "summarize the completed reducer work and the context/result it "
            "produces."
        ),
        advisor_review_focus=(
            "The reducer proposes success. Re-read `<assigned_task>` and the "
            "`<dependencies>` outcomes; verify the assigned reducer work is "
            "actually complete and the `outcome` states what was produced. "
            "Flag submissions that only reviewed dependencies, omit requested "
            "work, or claim success despite missing context."
        ),
    ),
    "submit_reduction_failure": TerminalToolDescriptor(
        name="submit_reduction_failure",
        selection_guidance=(
            "Call when you cannot finish the work in `<assigned_task>` from "
            "the current `<dependencies>` outcomes. Name the blocker, gap, or "
            "missing context."
        ),
        advisor_review_focus=(
            "The reducer proposes failure. Confirm the named blocker prevents "
            "completing `<assigned_task>` from the current context and that "
            "the failure outcome is specific enough for retry or replanning. "
            "Flag premature failures and failures based on work outside the "
            "assigned task or current iteration."
        ),
    ),
}


def render_terminal_catalog(
    terminals: list[str],
    *,
    focus: CatalogFocus,
) -> str:
    """Render the bulleted catalog for the given terminals.

    Returns the catalog text without the outer ``# ...`` heading — callers
    add the context-appropriate heading. Unknown terminals get a generic
    fallback bullet (drift between profile MD and registry is caught by
    the static completeness test in test_descriptor_registry.py).
    """
    if not terminals:
        return ""
    lines: list[str] = []
    for terminal in terminals:
        descriptor = TERMINAL_DESCRIPTORS.get(terminal)
        if descriptor is None:
            lines.append(
                f"- `{terminal}` — (no descriptor registered for this terminal)"
            )
            continue
        text = getattr(descriptor, focus)
        lines.append(f"- `{descriptor.name}` — {text}")
    return "\n\n".join(lines)


__all__ = [
    "CatalogFocus",
    "TERMINAL_DESCRIPTORS",
    "TerminalToolDescriptor",
    "render_terminal_catalog",
]
