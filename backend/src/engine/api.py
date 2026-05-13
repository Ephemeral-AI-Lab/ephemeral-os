"""Public engine API surface."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from engine.agent.factory import EphemeralAgent, spawn_agent
    from engine.agent.lifecycle import EphemeralRunResult, run_ephemeral_agent
    from engine.query.context import QueryContext, QueryExitReason
    from engine.query.loop import run_query

__all__ = [
    "EphemeralAgent",
    "EphemeralRunResult",
    "QueryContext",
    "QueryExitReason",
    "run_ephemeral_agent",
    "run_query",
    "spawn_agent",
]

_SUBMODULES = {
    "EphemeralAgent": ("engine.agent.factory", "EphemeralAgent"),
    "spawn_agent": ("engine.agent.factory", "spawn_agent"),
    "EphemeralRunResult": ("engine.agent.lifecycle", "EphemeralRunResult"),
    "run_ephemeral_agent": ("engine.agent.lifecycle", "run_ephemeral_agent"),
    "QueryContext": ("engine.query.context", "QueryContext"),
    "QueryExitReason": ("engine.query.context", "QueryExitReason"),
    "run_query": ("engine.query.loop", "run_query"),
}


def __getattr__(name: str) -> Any:
    if entry := _SUBMODULES.get(name):
        module_path, attr_name = entry
        mod = import_module(module_path)
        return getattr(mod, attr_name)
    raise AttributeError(name)
