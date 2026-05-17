"""TaskCenter entry lifecycle facade.

External callers should prefer the top-level :mod:`task_center` package.
This package's public surface is the union of three internal submodules:

* :mod:`task_center.entry.controller` — :class:`EntryTaskController`
* :mod:`task_center.entry.sandbox_bridge` — :class:`TaskCenterSandboxBridge`
  and :class:`TaskCenterSandboxBinding`
* :mod:`task_center.entry.coordinator` — :func:`start_task_center_entry_run`
"""

from __future__ import annotations

from task_center.entry.controller import EntryTaskController
from task_center.entry.coordinator import start_task_center_entry_run
from task_center.entry.sandbox_bridge import (
    TaskCenterSandboxBinding,
    TaskCenterSandboxBridge,
)

__all__ = [
    "EntryTaskController",
    "TaskCenterSandboxBinding",
    "TaskCenterSandboxBridge",
    "start_task_center_entry_run",
]
