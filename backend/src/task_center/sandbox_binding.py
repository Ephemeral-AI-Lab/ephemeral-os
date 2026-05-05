"""TaskCenter sandbox ownership metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskCenterSandboxBinding:
    sandbox_id: str
    task_center_run_id: str
    owned_by_task_center: bool


__all__ = ["TaskCenterSandboxBinding"]
