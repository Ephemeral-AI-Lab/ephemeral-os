"""Hierarchical NodeId breadcrumb attached to every audit Event.

Per plan §8. Most-specific fields are populated at emission time; nullable
fields default to ``None`` so emitters can fill in only what they know.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PrimaryRole = Literal[
    "entry_executor",
    "planner",
    "executor",
    "verifier",
    "evaluator",
]


@dataclass(frozen=True, slots=True)
class NodeId:
    """Hierarchical breadcrumb identifying where in the run an event occurred."""

    task_center_run_id: str
    mission_id: str | None = None
    mission_seq: int | None = None
    episode_id: str | None = None
    episode_seq: int | None = None
    attempt_id: str | None = None
    attempt_seq: int | None = None
    agent_role: PrimaryRole | None = None
    agent_name: str | None = None
    agent_run_id: str | None = None
    tool_name: str | None = None


__all__ = ["NodeId", "PrimaryRole"]
