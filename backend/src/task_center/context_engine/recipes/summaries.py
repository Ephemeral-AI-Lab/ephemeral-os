"""Shared helpers for reading task summaries.

Generator, evaluator, and ``attempts`` recipes all need to project the most
recent summary off a task row. Living in a free-standing helper keeps every
consuming recipe independent of any other.
"""

from __future__ import annotations

from typing import Any


def latest_summary_text(summaries: list[Any] | None) -> str:
    """Return the most recent summary string from a task's summaries list.

    Tasks carry a ``summaries`` list of dicts; both generator (dependency
    summaries) and evaluator (completed-task summaries) want the latest entry,
    preferring ``summary`` then ``outcome``, falling back to a placeholder.
    """
    if not summaries:
        return "(no summary recorded)"
    last = summaries[-1]
    if not isinstance(last, dict):
        return str(last)
    return str(last.get("summary") or last.get("outcome") or "(empty)")
