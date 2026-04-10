"""Shared run_subagent policy constants."""

from __future__ import annotations

SCOUT_ONLY_CALLERS = frozenset({"team_planner", "team_replanner"})

__all__ = ["SCOUT_ONLY_CALLERS"]
