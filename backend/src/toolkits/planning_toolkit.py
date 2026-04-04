"""Planning toolkit — plan mode and todo tools."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.enter_plan_mode_tool import EnterPlanModeTool
from ephemeralos.tools.exit_plan_mode_tool import ExitPlanModeTool
from ephemeralos.tools.todo_write_tool import TodoWriteTool


class PlanningToolkit(BaseToolkit):
    """Plan mode: enter, exit, and todo management."""

    def __init__(self) -> None:
        super().__init__(
            name="planning",
            description="Plan mode: enter, exit, and todo management",
            tools=[EnterPlanModeTool(), ExitPlanModeTool(), TodoWriteTool()],
        )
