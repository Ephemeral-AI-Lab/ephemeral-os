"""Dedicated toolkits for posthook serializer agents."""

from __future__ import annotations

from tools.core.base import BaseToolkit
from tools.posthook.submit_atlas import SubmitAtlasTool
from tools.posthook.submit_plan import SubmitPlanTool
from tools.posthook.submit_summary import SubmitSummaryTool


class SubmitPlanToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="submit_plan_posthook",
            description="Single-tool toolkit for serializer agents that submit Plans.",
            tools=[SubmitPlanTool()],
        )


class SubmitSummaryToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="submit_summary_posthook",
            description="Single-tool toolkit for serializer agents that submit summaries.",
            tools=[SubmitSummaryTool()],
        )


class SubmitAtlasToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="submit_atlas_posthook",
            description="Single-tool toolkit for serializer agents that submit atlas chunks.",
            tools=[SubmitAtlasTool()],
        )

