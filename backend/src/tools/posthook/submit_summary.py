"""``submit_summary`` tool — stashes a worker's summary+artifact in metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from team.context.canonicalize import scope_of_artifact
from tools.core.base import ToolExecutionContext
from tools.posthook.base import SubmitPosthookTool


@dataclass
class SubmittedSummary:
    """Posthook-validated worker output.

    ``summary`` is the 1-3 sentence gloss peers and the orchestrator
    consume. ``artifact`` is optional structured output (files changed,
    findings, etc.).
    """

    summary: str
    artifact: dict[str, Any] | None = None


class SubmitSummaryInput(BaseModel):
    summary: str = Field(
        ...,
        description=(
            "1-3 sentences describing what you accomplished on this "
            "WorkItem. Peer agents and the orchestrator will read this."
        ),
        min_length=1,
    )
    artifact: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional structured output (files changed, findings, ...). "
            "Omit if this work item has nothing structured to return."
        ),
    )


class SubmitSummaryTool(SubmitPosthookTool):
    name: str = "submit_summary"
    description: str = (
        "Submit your WorkItem result. MUST be called exactly once with a "
        "concise summary of what you accomplished; artifact is optional "
        "structured output. The team run fails this WorkItem if you do "
        "not call submit_summary."
    )
    input_model = SubmitSummaryInput
    default_metadata_key: str = "submitted_summary"

    def _build_payload(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> tuple[Any, str | None]:
        assert isinstance(arguments, SubmitSummaryInput)
        summary = arguments.summary.strip()
        if not summary:
            return None, "summary must be non-empty"
        artifact = _inject_snapshot_time(arguments.artifact, context)
        artifact = _inject_canonical_scope(artifact)
        return (
            SubmittedSummary(summary=summary, artifact=artifact),
            None,
        )

    def _accepted_message(self, payload: Any) -> str:
        assert isinstance(payload, SubmittedSummary)
        return f"Summary accepted ({len(payload.summary)} chars)."


def _inject_canonical_scope(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    """Idempotently derive ``canonical_scope`` from ``target_paths``.

    Generic and applies to any artifact dict carrying ``target_paths``,
    not just scout output. A scout that already supplied
    ``canonical_scope`` is a no-op; one that forgot still lands a
    correctly-keyed brief for ``shared_briefings`` dedup (§13).
    """
    if not isinstance(artifact, dict) or "canonical_scope" in artifact:
        return artifact
    derived = scope_of_artifact(artifact)
    if not derived:
        return artifact
    return {**artifact, "canonical_scope": derived}


def _inject_snapshot_time(
    artifact: dict[str, Any] | None,
    context: ToolExecutionContext,
) -> dict[str, Any] | None:
    """Stamp scout-like artifacts with a pre-work snapshot cutoff.

    ``build_work_item_metadata`` records ``work_item_started_at`` before
    the agent begins its work phase. Reusing that timestamp here is
    conservative and lets atlas freshness see edits that landed while a
    scout was reading files, instead of only those that happened after
    the scout finished composing its artifact.
    """
    if not isinstance(artifact, dict) or "snapshot_time" in artifact:
        return artifact
    target_paths = artifact.get("target_paths")
    if not isinstance(target_paths, list):
        return artifact
    started_at = context.metadata.get("work_item_started_at")
    if not isinstance(started_at, (int, float)) or started_at <= 0:
        return artifact
    return {**artifact, "snapshot_time": float(started_at)}
