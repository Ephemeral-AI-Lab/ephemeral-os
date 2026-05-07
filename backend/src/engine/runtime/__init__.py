"""Agent runtime exports.

Avoid eager imports so tests can pull in background-task helpers without
instantiating the full agent / tool factory stack at import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "BackgroundTaskManager",
    "EphemeralAgent",
    "TrackedBackgroundTask",
    "spawn_agent",
]


def __getattr__(name: str) -> Any:
    if name in {"BackgroundTaskManager", "TrackedBackgroundTask"}:
        module = import_module("engine.background.manager")
        return getattr(module, name)
    if name in {"EphemeralAgent", "spawn_agent"}:
        module = import_module("engine.agent.factory")
        return getattr(module, name)
    raise AttributeError(name)
