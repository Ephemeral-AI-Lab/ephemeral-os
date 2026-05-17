"""``TerminalToolDescriptor`` registry — terminal-tool semantics in one place.

Each entry pairs the parent-facing ``selection_guidance`` ("Call when …")
with the advisor-facing ``advisor_review_focus`` ("Verify … Flag …"). The
parent's ``user_msg_2`` and the advisor's ``user_msg_2`` both render from
this registry so the two prompts never drift.

Catalog rendering is consumed in two places:

* ``ContextComposer.compose()`` appends the parent-facing catalog to
  ``role_instruction_message`` whenever the resolved ``agent_def`` declares
  terminals — that lands the catalog in the parent's ``user_msg_2``.
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
            "Call when the assigned task's deliverable is complete, exists "
            "at the claimed location, satisfies the task specification, and "
            "any verification the criteria specify has been run and passed."
        ),
        advisor_review_focus=(
            "Verify the assigned task's deliverable actually exists at the "
            "claimed location, satisfies the task specification, and is "
            "consistent with the dependency outputs. Flag stub deliverables, "
            "TODO markers, and any divergence from the task contract."
        ),
    ),
    "submit_execution_failure": TerminalToolDescriptor(
        name="submit_execution_failure",
        selection_guidance=(
            "Call when the task cannot be completed after exhausting the "
            "obvious remediation paths. Name the failure mode concretely."
        ),
        advisor_review_focus=(
            "Confirm the failure mode is real, not a misdiagnosis. Verify "
            "the executor has tried the obvious remediation paths before "
            "giving up. Flag premature failures and failures that hide a "
            "fixable bug."
        ),
    ),
    "submit_execution_handoff": TerminalToolDescriptor(
        name="submit_execution_handoff",
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
            "Call when this attempt's tasks fully cover Current Iteration. "
            "On evaluator PASS, the iteration closes terminally and the goal "
            "can succeed."
        ),
        advisor_review_focus=(
            "The planner proposes to CLOSE the iteration goal in this "
            "attempt. Review the proposed decomposition against Current "
            "Iteration: does every required item have a generator task, are "
            "evaluation criteria one-per-item where the goal is a list, and "
            "does the plan avoid a coarse 'all items done' criterion that "
            "turns partial progress into total failure? Flag missing items, "
            "mis-scoped tasks, and dependency mistakes."
        ),
    ),
    "submit_plan_continues_goal": TerminalToolDescriptor(
        name="submit_plan_continues_goal",
        selection_guidance=(
            "Call when this attempt delivers a complete, coherent, bounded "
            "slice of Current Iteration and a clear remainder exists. The "
            "continuation_goal is the next iteration's whole scope, not a "
            "backlog dump."
        ),
        advisor_review_focus=(
            "The planner DEFERS remaining work via a continuation_goal. "
            "Confirm the partial scope is genuinely smaller than the "
            "iteration goal and that the continuation_goal is the next "
            "bounded slice — NOT a dump of the entire remaining backlog. "
            "Verify the in-scope items have one evaluation criterion each "
            "and the deferred items are clearly named so the next iteration "
            "can pick up cleanly."
        ),
    ),
    "submit_evaluation_success": TerminalToolDescriptor(
        name="submit_evaluation_success",
        selection_guidance=(
            "Call when every entry in Evaluation Criteria is satisfied; the "
            "attempt closes successfully and the planner's submission kind "
            "determines whether the goal closes or continues."
        ),
        advisor_review_focus=(
            "The evaluator proposes to PASS the attempt. Re-read the "
            "Evaluation Criteria; for each criterion, verify the attempt's "
            "deliverables actually satisfy it. Flag any criterion the "
            "evaluator is glossing over and any deliverable that satisfies "
            "the letter but not the intent of the criterion."
        ),
    ),
    "submit_evaluation_failure": TerminalToolDescriptor(
        name="submit_evaluation_failure",
        selection_guidance=(
            "Call when one or more criteria fail. The graph enters retry or "
            "failure handling."
        ),
        advisor_review_focus=(
            "The evaluator proposes to FAIL the attempt. Confirm the failing "
            "criteria are accurately named and that the failure is on the "
            "attempt's promised scope (NOT on work deferred via "
            "continuation_goal). Flag failures that punish the attempt for "
            "items outside the iteration goal."
        ),
    ),
    "submit_verification_success": TerminalToolDescriptor(
        name="submit_verification_success",
        selection_guidance=(
            "Call when the generator output passes verification. Closes this "
            "verifier task with a passing outcome."
        ),
        advisor_review_focus=(
            "The verifier proposes the deliverable PASSES verification. "
            "Re-check the verification criteria against the actual "
            "deliverable; flag missed checks and any claim that the "
            "deliverable passes solely because the verifier didn't look hard "
            "enough."
        ),
    ),
    "submit_verification_failure": TerminalToolDescriptor(
        name="submit_verification_failure",
        selection_guidance=(
            "Call when unresolved issues remain after the resolver-edit "
            "cycle. The attempt's failure handling reads the outcome."
        ),
        advisor_review_focus=(
            "The verifier proposes the deliverable FAILS verification. "
            "Confirm the failing checks are real and accurately described. "
            "Flag failures that are not the verifier's responsibility (e.g. "
            "issues that belong to a different task) so the failure routes "
            "to the right resolver."
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
