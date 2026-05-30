"""Launch-time terminal routing for the planner profile.

Returns the terminal subset permitted for a given launch context; the router
intersects it with the planner's declared ``terminals``. See
``task_center/_core/terminal_tool_routing.py``.
"""

from __future__ import annotations


def select_terminals(*, is_nested: bool, has_workflow: bool) -> frozenset[str]:
    # A nested planner (its caller attempt is itself inside a workflow) may only
    # close its goal; a top-level planner may also defer.
    if is_nested:
        return frozenset({"submit_plan_closes_goal"})
    return frozenset({"submit_plan_closes_goal", "submit_plan_defers_goal"})
