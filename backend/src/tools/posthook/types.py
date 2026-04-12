"""Generic posthook submission types.

These types are the canonical output shapes of posthook submit tools.
They are intentionally decoupled from ``team.models`` so single-agent
mode can reuse the same posthook infrastructure without importing the
team DAG layer.

Team-specific consumption (e.g. converting a ``SubmittedSummary`` into
an ``AgentResult`` for the dispatcher) lives in the team runtime, not
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PosthookSubmission(Protocol):
    """Protocol for all posthook submission payloads.

    Every concrete submission type must declare a ``submission_kind``
    discriminator so consumers can dispatch without isinstance chains
    against an ever-growing set of concrete classes.
    """

    @property
    def submission_kind(self) -> str: ...


@dataclass
class SubmittedSummary:
    """Posthook-validated worker output.

    ``summary`` is the 1-3 sentence gloss peers and the orchestrator
    consume. ``artifact`` is optional structured output (files changed,
    findings, etc.).
    """

    summary: str
    artifact: dict[str, Any] | None = None

    @property
    def submission_kind(self) -> str:
        return "summary"


@dataclass
class RetryRequest:
    """Posthook decision: retry the current work item."""

    reason: str
    retry_count: int = 0
    max_retries: int = 2

    @property
    def submission_kind(self) -> str:
        return "retry"


@dataclass
class ReplanRequest:
    """Posthook decision: replan at the current node level."""

    reason: str
    context: str
    suggestion: str = ""

    @property
    def submission_kind(self) -> str:
        return "replan"
