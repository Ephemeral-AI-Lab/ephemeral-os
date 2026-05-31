"""Package for the `wait_background_tasks` tool."""

from . import wait_background_tasks as _impl

WaitBackgroundTasksInput = _impl.WaitBackgroundTasksInput
WaitBackgroundTasksTool = _impl.WaitBackgroundTasksTool

__all__ = ["WaitBackgroundTasksInput", "WaitBackgroundTasksTool"]
