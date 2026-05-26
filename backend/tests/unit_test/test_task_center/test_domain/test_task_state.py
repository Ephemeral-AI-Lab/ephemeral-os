"""Task row status domain contracts."""

from __future__ import annotations

from task_center.task_state import TaskCenterBackgroundTaskStatus, TaskCenterTaskStatus


def test_task_status_preserves_background_task_import_alias() -> None:
    assert TaskCenterBackgroundTaskStatus is TaskCenterTaskStatus
    assert TaskCenterBackgroundTaskStatus.RUNNING.value == "running"
