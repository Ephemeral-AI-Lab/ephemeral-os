"""Public engine API surface."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from engine.agent.factory import EphemeralAgent, spawn_agent
    from engine.agent.lifecycle import EphemeralRunResult, run_ephemeral_agent
    from engine.background.manager import BackgroundTaskManager, TrackedBackgroundTask
    from engine.query.context import QueryContext, QueryExitReason
    from engine.query.loop import run_query
    from engine.tool_call.streaming import StreamingToolExecutor, TrackedTool

__all__ = [
    "BackgroundTaskManager",
    "EphemeralAgent",
    "EphemeralRunResult",
    "QueryContext",
    "QueryExitReason",
    "StreamingToolExecutor",
    "TrackedBackgroundTask",
    "TrackedTool",
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
    "BackgroundTaskManager": ("engine.background.manager", "BackgroundTaskManager"),
    "TrackedBackgroundTask": ("engine.background.manager", "TrackedBackgroundTask"),
    "StreamingToolExecutor": ("engine.tool_call.streaming", "StreamingToolExecutor"),
    "TrackedTool": ("engine.tool_call.streaming", "TrackedTool"),
}


def __getattr__(name: str) -> Any:
    if entry := _SUBMODULES.get(name):
        module_path, attr_name = entry
        mod = import_module(module_path)
        return getattr(mod, attr_name)
    raise AttributeError(name)
