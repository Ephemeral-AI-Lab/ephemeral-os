"""Background task management tools.

Provides tools to wait for, check the result of, and cancel long-running
background tasks, plus a factory to assemble them for registration.
"""

from __future__ import annotations

from tools._framework.core.base import BaseTool
from tools.background.cancel_background_task import CancelBackgroundTaskTool
from tools.background.check_background_task_result import (
    CheckBackgroundTaskResultTool,
)
from tools.background.wait_background_tasks import WaitBackgroundTasksTool


def make_background_tools() -> list[BaseTool]:
    """Create background task management tools."""
    return [
        CancelBackgroundTaskTool(),
        CheckBackgroundTaskResultTool(),
        WaitBackgroundTasksTool(),
    ]
