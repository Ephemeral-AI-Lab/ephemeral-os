"""Package for the `check_background_task_result` tool."""

from . import check_background_task_result as _impl

CheckBackgroundTaskResultInput = _impl.CheckBackgroundTaskResultInput
CheckBackgroundTaskResultTool = _impl.CheckBackgroundTaskResultTool

__all__ = ["CheckBackgroundTaskResultInput", "CheckBackgroundTaskResultTool"]
