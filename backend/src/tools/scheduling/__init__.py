"""Scheduling toolkit — cron job management tools."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.scheduling.cron_create_tool import CronCreateTool
from ephemeralos.tools.scheduling.cron_delete_tool import CronDeleteTool
from ephemeralos.tools.scheduling.cron_list_tool import CronListTool
from ephemeralos.tools.scheduling.cron_toggle_tool import CronToggleTool


class SchedulingToolkit(BaseToolkit):
    """Cron job management: create, list, delete, toggle."""

    def __init__(self) -> None:
        super().__init__(
            name="scheduling",
            description="Cron job management: create, list, delete, toggle",
            tools=[
                CronCreateTool(),
                CronListTool(),
                CronDeleteTool(),
                CronToggleTool(),
            ],
        )


__all__ = [
    "SchedulingToolkit",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "CronToggleTool",
]
