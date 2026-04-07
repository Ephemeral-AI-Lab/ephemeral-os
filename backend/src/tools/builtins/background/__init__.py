"""Background task management toolkit.

Provides tools to monitor, wait for, and cancel long-running background tasks,
plus a factory to assemble them into a toolkit with instructions.
"""

from __future__ import annotations

from tools.core.base import BaseToolkit
from tools.builtins.background.check_background_progress import CheckBackgroundProgressTool
from tools.builtins.background.cancel_background_task import CancelBackgroundTaskTool
from tools.builtins.background.wait_for_background_task import WaitForBackgroundTaskTool


def make_background_toolkit(bg_tool_names: list[str]) -> BaseToolkit:
    """Create the background task management toolkit.

    Args:
        bg_tool_names: Names of tools that support background execution.
    """
    tools_list = ", ".join(f"`{n}`" for n in bg_tool_names)
    return BaseToolkit(
        name="background",
        description="Background task management — launch, monitor, and cancel long-running tools.",
        tools=[CheckBackgroundProgressTool(), CancelBackgroundTaskTool(), WaitForBackgroundTaskTool()],
        instructions=(
            "You can run long-running tools in the background by adding "
            '`"background": true` to the tool input JSON. '
            "This launches the tool asynchronously — you get an immediate acknowledgment "
            "and can continue with other work while it runs.\n\n"
            f"**Tools that support background execution:** {tools_list}\n\n"
            "**When to use background:**\n"
            "- Long-running operations: test suites, builds, installations, deployments\n"
            "- When you have other useful work to do in parallel\n\n"
            "**When NOT to use background:**\n"
            "- Quick commands (< 5 seconds)\n"
            "- When you need the result immediately for your next step\n\n"
            "**Monitoring and waiting for background tasks:**\n"
            "1. First call `check_background_progress` to get an instant status snapshot "
            "and note each task's `task_id` from the output.\n"
            "2. If you have more foreground work, do it and check progress again later.\n"
            "3. When you have NO foreground work left, call `wait_for_background_task` to "
            "block until tasks complete. To wait for a specific task, pass its `task_id` "
            "(from check_background_progress). Do NOT poll in a loop.\n"
            "4. Use `cancel_background_task` to stop tasks that are taking too long.\n\n"
            "**Shortcut:** `check_background_progress` and `wait_for_background_task` "
            "accept the literal string `\"all\"` as `task_id` to target every pending "
            "background task at once. `cancel_background_task` does NOT accept `\"all\"` — "
            "cancel each task explicitly to avoid accidental mass-cancellation."
        ),
    )
