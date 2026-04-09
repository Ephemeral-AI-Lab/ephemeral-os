"""Dedicated toolkits for posthook serializer and decision agents."""

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


# --- Decision posthook toolkits (multi-tool) ---


class SubmitRetryPosthookToolkit(BaseToolkit):
    """Decision posthook: submit_summary + request_retry + request_replan."""

    def __init__(self) -> None:
        from tools.posthook.request_replan import RequestReplanTool
        from tools.posthook.request_retry import RequestRetryTool

        super().__init__(
            name="posthook_submit_retry",
            description="Decision posthook for agents that may submit, retry, or request replanning.",
            tools=[SubmitSummaryTool(), RequestRetryTool(), RequestReplanTool()],
        )


class SubmitReplanPosthookToolkit(BaseToolkit):
    """Decision posthook: submit_summary + request_retry + request_replan."""

    def __init__(self) -> None:
        from tools.posthook.request_replan import RequestReplanTool
        from tools.posthook.request_retry import RequestRetryTool

        super().__init__(
            name="posthook_submit_replan",
            description="Decision posthook for agents that may submit, retry, or request replanning.",
            tools=[SubmitSummaryTool(), RequestRetryTool(), RequestReplanTool()],
        )


class SubmitReplanPlanToolkit(BaseToolkit):
    """Single-tool toolkit for replanner serializer agents."""

    def __init__(self) -> None:
        from tools.posthook.submit_replan import SubmitReplanTool

        super().__init__(
            name="submit_replan_posthook",
            description="Single-tool toolkit for replanner agents that submit corrective plans.",
            tools=[SubmitReplanTool()],
        )
