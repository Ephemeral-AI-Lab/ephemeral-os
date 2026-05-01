"""Shared summary formatters for recipe modules.

Tasks carry a ``summaries`` list of dicts. Both the generator (dependency
summaries) and evaluator (completed-task summaries) want the latest entry,
preferring ``summary`` then ``outcome``, falling back to a placeholder.
Centralized here so the policy can't drift between recipes.
"""

from __future__ import annotations

from typing import Any


def latest_summary_text(summaries: list[Any] | None) -> str:
    if not summaries:
        return "(no summary recorded)"
    last = summaries[-1]
    if not isinstance(last, dict):
        return str(last)
    return str(last.get("summary") or last.get("outcome") or "(empty)")
