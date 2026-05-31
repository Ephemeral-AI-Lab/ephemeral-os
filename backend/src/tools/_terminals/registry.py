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
    "submit_generator_outcome": TerminalToolDescriptor(
        name="submit_generator_outcome",
        selection_guidance=(
            'Call with status="success" when the `<assigned_task>` deliverable '
            'is complete and verified; call with status="failed" when the '
            "task cannot be completed in this attempt. The outcome must carry "
            "the concrete result, evidence, and artifact references."
        ),
        advisor_review_focus=(
            "Verify the chosen status matches the work. For success, confirm "
            "the deliverable exists, satisfies the task specification, and is "
            "consistent with dependencies. For failure, confirm the blocker is "
            "real, specific, and not a premature give-up."
        ),
    ),
    "submit_workflow_handoff": TerminalToolDescriptor(
        name="submit_workflow_handoff",
        selection_guidance=(
            "Call when you have not started edits, the `<assigned_task>` is "
            "too broad or complex for one executor pass, and this is not a "
            "nested workflow. Name the delegated goal and why planner "
            "decomposition is needed."
        ),
        advisor_review_focus=(
            "Verify the generator has not started edits and the handoff scope "
            "is specific and actionable. Flag vague handoffs that kick the "
            "problem downstream without naming the delegated goal, findings, "
            "or why decomposition is needed."
        ),
    ),
    "submit_planner_outcome": TerminalToolDescriptor(
        name="submit_planner_outcome",
        selection_guidance=(
            "Call with a generator/reducer DAG for this attempt. Omit "
            "`deferred_goal_for_next_iteration` when the plan covers all "
            "current-iteration goal items and leaves no remaining items; set "
            "it only for concrete current-iteration goal items intentionally "
            "deferred to the next iteration."
        ),
        advisor_review_focus=(
            "Review the DAG against `<iteration_goal>`: every required current "
            "item must have generator work or be explicitly listed in "
            "`deferred_goal_for_next_iteration`. Flag missing items, vague "
            "deferred goals, backlog dumps, mis-scoped tasks, and dependency "
            "mistakes."
        ),
    ),
    "submit_reducer_outcome": TerminalToolDescriptor(
        name="submit_reducer_outcome",
        selection_guidance=(
            'Call with status="success" when the assigned reducer work is '
            'finished from `<dependencies>` context; call with status="failed" '
            "when the reducer work cannot be completed from the current context. "
            "The outcome must summarize the result or blocker."
        ),
        advisor_review_focus=(
            "Verify the chosen status matches `<assigned_task>` and "
            "`<dependencies>`. For success, confirm the assigned reducer work "
            "is actually complete. For failure, confirm the blocker prevents "
            "completion and is specific enough for retry or replanning."
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
            lines.append(f"- `{terminal}` — (no descriptor registered for this terminal)")
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
