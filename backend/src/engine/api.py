"""Public engine API surface."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from engine.agent.factory import spawn_agent
    from engine.agent.lifecycle import EphemeralRunResult, run_ephemeral_agent
    from engine.query.context import QueryContext
    from engine.query.loop import run_query

__all__ = [
    "EphemeralRunResult",
    "QueryContext",
    "run_ephemeral_agent",
    "run_query",
    "spawn_agent",
]

_SUBMODULES = {
    "spawn_agent": ("engine.agent.factory", "spawn_agent"),
    "EphemeralRunResult": ("engine.agent.lifecycle", "EphemeralRunResult"),
    "run_ephemeral_agent": ("engine.agent.lifecycle", "run_ephemeral_agent"),
    "QueryContext": ("engine.query.context", "QueryContext"),
    "run_query": ("engine.query.loop", "run_query"),
}


def __getattr__(name: str) -> Any:
    if entry := _SUBMODULES.get(name):
        module_path, attr_name = entry
        mod = import_module(module_path)
        return getattr(mod, attr_name)
    raise AttributeError(name)
