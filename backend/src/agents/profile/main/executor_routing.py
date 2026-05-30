"""Launch-time terminal routing for the executor profile.

Returns the terminal subset permitted for a given launch context (``None`` = no
filtering); the router intersects a non-``None`` result with the executor's
declared ``terminals``. See ``task_center/_core/terminal_tool_routing.py``.
"""

from __future__ import annotations


def select_terminals(*, is_nested: bool, has_workflow: bool) -> frozenset[str] | None:
    # Outside a workflow: keep the full frontmatter terminal set (no filtering).
    if not has_workflow:
        return None
    # Nested executors cannot hand off; only succeed or block.
    if is_nested:
        return frozenset({"submit_execution_success", "submit_execution_blocker"})
    return frozenset(
        {
            "submit_workflow_handoff",
            "submit_execution_success",
            "submit_execution_blocker",
        }
    )
