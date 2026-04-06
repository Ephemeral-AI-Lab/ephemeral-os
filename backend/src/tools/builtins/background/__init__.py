"""Background task management toolkit.

Provides tools to monitor and cancel long-running background tasks,
plus a factory to assemble them into a toolkit with instructions.
"""

from __future__ import annotations

from tools.base import BaseToolkit
from tools.builtins.background.check_background_progress import CheckBackgroundProgressTool
from tools.builtins.background.cancel_background_task import CancelBackgroundTaskTool


def make_background_toolkit(bg_tool_names: list[str]) -> BaseToolkit:
    """Create the background task management toolkit.

    Args:
        bg_tool_names: Names of tools that support background execution.
    """
    tools_list = ", ".join(f"`{n}`" for n in bg_tool_names)
    return BaseToolkit(
        name="background",
        description="Background task management — launch, monitor, and cancel long-running tools.",
        tools=[CheckBackgroundProgressTool(), CancelBackgroundTaskTool()],
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
            "- When you need the result immediately for your next step"
        ),
    )
