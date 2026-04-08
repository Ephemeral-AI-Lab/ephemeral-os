"""Posthook submit tools.

A posthook submit tool is the single exit point of a serializer agent
run by ``hooks.agent_posthook.execute_with_posthook``. It validates the
work-phase output and stashes the validated payload in
``context.metadata`` under the slot named by ``posthook_metadata_key``.

``SubmitPosthookTool`` is the abstract base; concrete tools like
``SubmitPlanTool`` and ``SubmitSummaryTool`` implement domain-specific
validation in ``_build_payload``. Atlas-specific symbols are imported
lazily so non-atlas code paths do not require SQLAlchemy during import.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "SubmitAtlasInput",
    "SubmitAtlasTool",
    "SubmitPosthookTool",
    "SubmitPlanInput",
    "SubmitPlanTool",
    "SubmittedSummary",
    "SubmitSummaryInput",
    "SubmitSummaryTool",
]


def __getattr__(name: str):
    if name == "SubmitPosthookTool":
        return import_module("tools.posthook.base").SubmitPosthookTool
    if name in {"SubmitPlanInput", "SubmitPlanTool"}:
        return getattr(import_module("tools.posthook.submit_plan"), name)
    if name in {"SubmittedSummary", "SubmitSummaryInput", "SubmitSummaryTool"}:
        return getattr(import_module("tools.posthook.submit_summary"), name)
    if name in {"SubmitAtlasInput", "SubmitAtlasTool"}:
        return getattr(import_module("tools.posthook.submit_atlas"), name)
    raise AttributeError(name)
