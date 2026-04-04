"""Planning toolkit — todo and plan mode tools."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.planning.enter_plan_mode_tool import EnterPlanModeTool
from ephemeralos.tools.planning.exit_plan_mode_tool import ExitPlanModeTool
from ephemeralos.tools.planning.todo_write_tool import TodoWriteTool


class PlanningToolkit(BaseToolkit):
    """Todo management and plan mode."""

    def __init__(self) -> None:
        super().__init__(
            name="planning",
            description="Todo management and plan mode",
            tools=[TodoWriteTool(), EnterPlanModeTool(), ExitPlanModeTool()],
        )


__all__ = [
    "PlanningToolkit",
    "TodoWriteTool",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
]
