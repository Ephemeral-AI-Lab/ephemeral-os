"""Posthook submit tools.

A posthook submit tool is the single exit point of a serializer agent
run by ``hooks.agent_posthook.execute_with_posthook``. It validates the
work-phase output and stashes the validated payload in
``context.metadata`` under the slot named by ``posthook_metadata_key``.

``SubmitPosthookTool`` is the abstract base; concrete tools like
``SubmitPlanTool`` and ``SubmitSummaryTool`` implement domain-specific
validation in ``_build_payload``.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "PosthookSubmission",
    "SubmitPosthookTool",
    "SubmitPlanInput",
    "SubmitPlanTool",
    "SubmitReplanInput",
    "SubmitReplanTool",
    "SubmittedSummary",
    "SubmitSummaryInput",
    "SubmitSummaryTool",
    "RequestRetryTool",
    "RequestReplanTool",
    "RetryRequest",
    "ReplanRequest",
]


def __getattr__(name: str):
    if name in {"PosthookSubmission", "SubmittedSummary", "RetryRequest", "ReplanRequest"}:
        return getattr(import_module("tools.posthook.types"), name)
    if name == "SubmitPosthookTool":
        return import_module("tools.posthook.base").SubmitPosthookTool
    if name in {"SubmitPlanInput", "SubmitPlanTool"}:
        return getattr(import_module("tools.posthook.submit_plan"), name)
    if name in {"SubmitReplanInput", "SubmitReplanTool"}:
        return getattr(import_module("tools.posthook.submit_replan"), name)
    if name in {"SubmitSummaryInput", "SubmitSummaryTool"}:
        return getattr(import_module("tools.posthook.submit_summary"), name)
    if name == "RequestRetryTool":
        return import_module("tools.posthook.request_retry").RequestRetryTool
    if name == "RequestReplanTool":
        return import_module("tools.posthook.request_replan").RequestReplanTool
    raise AttributeError(name)
