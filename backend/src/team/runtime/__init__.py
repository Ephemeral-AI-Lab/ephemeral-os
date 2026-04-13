"""Team-mode runtime exports.

Keep package imports light so unit tests can import narrow runtime helpers
without pulling the full persistence stack at module import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["Executor", "TeamRun", "TeamRunCheckpoint", "DispatchQueue", "Conductor"]


def __getattr__(name: str) -> Any:
    if name == "Executor":
        return import_module("team.runtime.executor").Executor
    if name == "TeamRun":
        return import_module("team.runtime.team_run").TeamRun
    if name == "TeamRunCheckpoint":
        return import_module("team.runtime.checkpoint").TeamRunCheckpoint
    if name == "DispatchQueue":
        return import_module("team.runtime.dispatch_queue").DispatchQueue
    if name == "Conductor":
        return import_module("team.runtime.conductor").Conductor
    raise AttributeError(name)
