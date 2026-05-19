"""Single-directive table for ``<Task Guidance>``'s "What to do" section.

Each agent name maps to exactly one line — the imperative the agent should
read after the ``What's in context`` outline. Situational nuance is fully
encoded by the ``<context>`` shape (presence of
``<attempt status="prior" verdict="fail">``, etc.); the directive stays
constant.

Operational heuristics (e.g. "diagnose first after failure", "treat
``<dependency>`` outputs as fixed inputs") live in role skill files
(``backend/src/agents/skills/``) — not here.
"""

from __future__ import annotations


ROLE_DIRECTIVES: dict[str, str] = {
    "planner": "Plan for <iteration_goal>.",
    "planner_full_only": (
        "Plan for <iteration_goal>. No defer option — must close in one attempt."
    ),
    "executor": "Complete <assigned_task>.",
    "executor_success_handoff": "Complete <assigned_task>.",
    "executor_success_failure": (
        "Complete <assigned_task>. No handoff option."
    ),
    "verifier": "Complete <assigned_task>.",
    "evaluator": "Verify the current attempt against <evaluation_criteria>.",
    "advisor": "Review the parent's pending terminal call.",
    "resolver": "Resolve the issues listed in <issues>.",
    "explorer": (
        "Investigate the parent's question and return concrete findings."
    ),
}


__all__ = ["ROLE_DIRECTIVES"]
