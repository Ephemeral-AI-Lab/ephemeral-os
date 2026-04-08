"""Agent team orchestration layer.

A minimal wrapper on top of ``engine.core.query.run_query`` that adds a DAG
of ``WorkItem`` nodes, dependency-aware scheduling, and planner agents that
extend the DAG via ``submit_plan``. Non-team mode (direct ``run_query``) is
untouched — deleting ``backend/src/team/`` leaves the single-agent flow
fully functional.
"""

from __future__ import annotations

from importlib import import_module

from team.errors import (
    ArtifactTooLarge,
    BudgetExceeded,
    CheckpointNotFound,
    InvalidPlan,
    NoPosthookOutput,
)
from team.models import (
    AgentResult,
    BudgetConfig,
    BudgetState,
    Plan,
    TeamDefinition,
    TeamRunStatus,
    WorkItem,
    WorkItemKind,
    WorkItemSpec,
    WorkItemStatus,
)

__all__ = [
    "AgentResult",
    "ArtifactTooLarge",
    "BudgetConfig",
    "BudgetExceeded",
    "BudgetState",
    "CheckpointNotFound",
    "InvalidPlan",
    "NoPosthookOutput",
    "Plan",
    "TeamDefinition",
    "TeamRun",
    "TeamRunStatus",
    "WorkItem",
    "WorkItemKind",
    "WorkItemSpec",
    "WorkItemStatus",
]


def __getattr__(name: str):
    if name == "TeamRun":
        return import_module("team.runtime.team_run").TeamRun
    raise AttributeError(name)
