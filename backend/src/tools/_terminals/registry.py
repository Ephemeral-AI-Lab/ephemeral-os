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
    "submit_execution_success": TerminalToolDescriptor(
        name="submit_execution_success",
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
    "submit_execution_blocker": TerminalToolDescriptor(
        name="submit_execution_blocker",
        selection_guidance=(
            "Call when the `<assigned_task>` cannot proceed because of a "
            "concrete blocker. Summarize the blocker and the evidence."
        ),
        advisor_review_focus=(
            "Confirm the blocker is real and specific, not a premature "
            "give-up. Verify the executor tried the obvious remediation "
            "paths and did not hide solvable work behind a blocker."
        ),
    ),
    "submit_workflow_handoff": TerminalToolDescriptor(
        name="submit_workflow_handoff",
        selection_guidance=(
            "Call when bounded progress is made but further work is needed. "
            "Name the next bounded slice; do not kick the problem downstream "
            "without specifying what's needed."
        ),
        advisor_review_focus=(
            "Verify the handoff scope is specific and actionable. Flag vague "
            "handoffs that just kick the problem downstream without naming "
            "what's needed."
        ),
    ),
    "submit_plan_closes_goal": TerminalToolDescriptor(
        name="submit_plan_closes_goal",
        selection_guidance=(
            "Call when this attempt's tasks fully cover the current "
            "`<iteration_goal>`. When every plan task is done, the iteration "
            "closes terminally and the workflow can succeed."
        ),
        advisor_review_focus=(
            "The planner proposes to CLOSE the current `<iteration_goal>` in "
            "this attempt. Review the proposed decomposition against "
            "`<iteration_goal>`: does every required item have a generator "
            "task, are evaluation criteria one-per-item where the goal is a "
            "list, and does the plan avoid a coarse 'all items done' "
            "criterion that turns partial progress into total failure? Flag "
            "missing items, mis-scoped tasks, and dependency mistakes."
        ),
    ),
    "submit_plan_defers_goal": TerminalToolDescriptor(
        name="submit_plan_defers_goal",
        selection_guidance=(
            "Call when this attempt delivers a complete, coherent, bounded "
            "slice of the current `<iteration_goal>` and a clear remainder "
            "exists. The `deferred_goal_for_next_iteration` is the next "
            "iteration's whole scope, not a backlog dump."
        ),
        advisor_review_focus=(
            "The planner DEFERS remaining work via a "
            "`deferred_goal_for_next_iteration`. Confirm the partial scope is "
            "genuinely smaller than `<iteration_goal>` and that the "
            "`deferred_goal_for_next_iteration` is the next bounded slice — NOT a "
            "dump of the entire remaining backlog. Verify the in-scope items "
            "have one evaluation criterion each and the deferred items are "
            "clearly named so the next iteration can pick up cleanly."
        ),
    ),
    "submit_reduction_success": TerminalToolDescriptor(
        name="submit_reduction_success",
        selection_guidance=(
            "Call when your `<needs>` outcomes satisfy your "
            "`<assigned_prompt>`; this reducer task closes successfully and "
            "the attempt passes once every plan task is done."
        ),
        advisor_review_focus=(
            "The reducer proposes to PASS the slice it gates. Re-read its "
            "`<assigned_prompt>`; verify the `<needs>` outcomes actually "
            "satisfy it. Flag any requirement the reducer is glossing over "
            "and any outcome that satisfies the letter but not the intent of "
            "the prompt."
        ),
    ),
    "submit_reduction_failure": TerminalToolDescriptor(
        name="submit_reduction_failure",
        selection_guidance=(
            "Call when your `<needs>` outcomes do not satisfy your "
            "`<assigned_prompt>`. The graph enters retry or failure handling."
        ),
        advisor_review_focus=(
            "The reducer proposes to FAIL the slice it gates. Confirm the "
            "failing requirement is accurately named and that the failure is "
            "on the slice's promised scope (NOT on work deferred via "
            "`deferred_goal_for_next_iteration`). Flag failures that punish "
            "the attempt for items outside the current `<iteration_goal>`."
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
