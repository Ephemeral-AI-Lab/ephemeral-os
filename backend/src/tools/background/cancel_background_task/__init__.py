"""Package for the `cancel_background_task` tool."""

from . import cancel_background_task as _impl

CancelBackgroundTaskInput = _impl.CancelBackgroundTaskInput
CancelBackgroundTaskTool = _impl.CancelBackgroundTaskTool

__all__ = ["CancelBackgroundTaskInput", "CancelBackgroundTaskTool"]
