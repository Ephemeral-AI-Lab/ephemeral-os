"""Builtin harness agent definitions."""

from __future__ import annotations

from agents.types import AgentDefinition
from task_center.harness_agents.advisor.definition import ADVISOR
from task_center.harness_agents.executor.definition import EXECUTOR
from task_center.harness_agents.explorer.definition import EXPLORER
from task_center.harness_agents.planner.definition import PLANNER
from task_center.harness_agents.verifier.definition import VERIFIER


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (
    EXECUTOR,
    PLANNER,
    EXPLORER,
    VERIFIER,
    ADVISOR,
)


def register_builtin_agents() -> None:
    """Register all built-in agent definitions used by the harness."""
    from agents.registry import register_definition

    for defn in BUILTIN_AGENTS:
        register_definition(defn)


__all__ = [
    "ADVISOR",
    "BUILTIN_AGENTS",
    "EXECUTOR",
    "EXPLORER",
    "PLANNER",
    "VERIFIER",
    "register_builtin_agents",
]
