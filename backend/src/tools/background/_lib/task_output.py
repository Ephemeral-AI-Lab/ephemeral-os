"""Background task output rendering shared by wait/check/cancel tools."""

from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import Field

BACKGROUND_TASK_ID_FIELD_DESCRIPTION = (
    "REQUIRED. The exact `task_id` string (e.g. \"bg_1\") shown in the "
    "`[BACKGROUND LAUNCHED]` message. Never pass null/None and never "
    "omit this field."
)

BACKGROUND_TASK_ID_FIELD = Field(
    ...,
    min_length=1,
    description=BACKGROUND_TASK_ID_FIELD_DESCRIPTION,
)


def render_background_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Render a tool invocation as ``name(v1, v2, ...)``.

    Values are stringified verbatim and joined by ``, ``. No truncation —
    the wait/check tools surface this so the model can recognise which
    background task is which.
    """
    if not tool_input:
        return f"{tool_name}()"
    parts = [str(v) for v in tool_input.values()]
    return f"{tool_name}({', '.join(parts)})"


def background_task_display_status(raw_status: Any) -> str:
    """Collapse internal task statuses to {running, finished, failed}."""
    s = str(raw_status)
    if s == "running":
        return "running"
    if s in ("completed", "delivered"):
        return "finished"
    return "failed"


def render_background_snapshot(
    kind: str,
    statuses: list[dict[str, Any]],
    *,
    elapsed_seconds: float | None = None,
) -> str:
    """Render a background status snapshot exactly as the tools return it."""
    # "progress" is no longer emitted by any tool, but provider-history
    # preparation must still rebuild historical blocks from saved requests.
    if kind == "progress":
        return json.dumps(statuses, indent=2)

    if kind == "wait_completed":
        hint = (
            "All background tasks are terminal. Do not call "
            "wait_background_tasks again; use check_background_task_result "
            "for any task_id whose result you need."
        )
        return f"[COMPLETED]\n{json.dumps(statuses, indent=2)}\n{hint}"

    if kind == "wait_timed_out":
        elapsed = elapsed_seconds or 0.0
        hint = (
            "Call wait_background_tasks again to continue waiting, "
            "or cancel_background_task to stop a specific task."
        )
        return (
            f"[TIMED_OUT after {elapsed:.1f}s]\n"
            f"{json.dumps(statuses, indent=2)}\n"
            f"{hint}"
        )

    if kind == "wait_no_tasks":
        return (
            "[NO TASKS] No background tasks have been launched in this "
            "request, or all are already delivered. Do not poll again."
        )

    raise ValueError(f"Unknown background snapshot kind: {kind}")


def build_background_snapshot_metadata(
    kind: str,
    scope: str,
    statuses: list[dict[str, Any]],
    *,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Build internal metadata used by API-view reduction."""
    snapshot: dict[str, Any] = {
        "kind": kind,
        "scope": scope,
        "statuses": copy.deepcopy(statuses),
    }
    if elapsed_seconds is not None:
        snapshot["elapsed_seconds"] = round(elapsed_seconds, 1)
    return {"background_snapshot": snapshot}
