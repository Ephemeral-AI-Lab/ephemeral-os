"""Replan toolkit — provides ``update_plan`` to replanner agents."""

from __future__ import annotations

from tools.core.base import BaseToolkit
from tools.replan.update_plan import UpdatePlanTool


class ReplanToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="replan_operations",
            description="DAG mutation tool for replanner agents.",
            tools=[UpdatePlanTool()],
        )
