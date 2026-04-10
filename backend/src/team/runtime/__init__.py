"""Team-mode runtime exports.

Keep package imports light so unit tests can import narrow runtime helpers
without pulling the full dispatcher / persistence stack at module import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["Dispatcher", "Executor", "TeamRun", "TeamRunCheckpoint"]


def __getattr__(name: str) -> Any:
    if name == "Dispatcher":
        return import_module("team.runtime.dispatcher").Dispatcher
    if name == "Executor":
        return import_module("team.runtime.executor").Executor
    if name == "TeamRun":
        return import_module("team.runtime.team_run").TeamRun
    if name == "TeamRunCheckpoint":
        return import_module("team.runtime.checkpoint").TeamRunCheckpoint
    raise AttributeError(name)
