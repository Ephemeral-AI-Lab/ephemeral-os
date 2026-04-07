"""Shared helpers for background task tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

TASK_ID_FIELD_DESCRIPTION = (
    "REQUIRED. Either the exact `task_id` string (e.g. \"bg_1\") shown "
    "in the `[BACKGROUND LAUNCHED]` message / `check_background_progress` "
    "output, OR the literal string \"all\" to target every pending "
    "background task. Never pass null/None and never omit this field."
)

TASK_ID_FIELD = Field(..., min_length=1, description=TASK_ID_FIELD_DESCRIPTION)


# Total char budget for `output` fields in a single tool response,
# summed across every status entry. ~1k tokens, chosen to keep batched
# `task_id="all"` responses within the agent's context budget.
MAX_TOTAL_OUTPUT_CHARS = 4000
# Floor on per-entry budget so a many-task response still leaves each
# entry with enough tail to be useful.
MIN_PER_ENTRY_CHARS = 200


def apply_last_n_lines(status: list[dict[str, Any]], last_n_lines: int) -> None:
    """Trim 'output' field in each status entry, in-place.

    Two-stage trim:
      1. Keep only the *last* ``last_n_lines`` lines per entry.
      2. Split ``MAX_TOTAL_OUTPUT_CHARS`` evenly across all entries that
         still have output, char-capping each entry to its share (with
         a floor of ``MIN_PER_ENTRY_CHARS``).

    The per-entry char-cap keeps the tail and prepends a
    ``... (head truncated)`` marker so the reader sees the marker before
    the kept content. After char-capping, any leading partial line is
    dropped so the first visible line is complete.

    Caller must own the list — this mutates entries in place.
    """
    # Stage 1: line trim.
    for entry in status:
        if "output" in entry and isinstance(entry["output"], str):
            lines = entry["output"].splitlines()
            if len(lines) > last_n_lines:
                entry["output"] = "\n".join(lines[-last_n_lines:])

    # Stage 2: total char budget, split per entry.
    entries_with_output = [
        e for e in status
        if "output" in e and isinstance(e["output"], str) and e["output"]
    ]
    if not entries_with_output:
        return
    per_entry_budget = max(
        MIN_PER_ENTRY_CHARS,
        MAX_TOTAL_OUTPUT_CHARS // len(entries_with_output),
    )
    for entry in entries_with_output:
        text = entry["output"]
        if len(text) <= per_entry_budget:
            continue
        tail = text[-per_entry_budget:]
        # Drop the leading partial line so the first visible line is whole.
        nl = tail.find("\n")
        if nl != -1:
            tail = tail[nl + 1:]
        entry["output"] = "... (head truncated)\n" + tail
