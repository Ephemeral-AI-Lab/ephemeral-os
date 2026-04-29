"""Pre-hook layer for advisor-gated terminals (Stage 4 of the four-role roadmap).

The advisor approves a `(terminal_tool, input)` pair via `ask_advisor`.
``record_accept`` stores the verdict on the calling task's ``AdvisorAccept``
slot in TaskCenter; ``check_advisor_accept`` runs immediately before a
gated terminal fires and rejects calls that lack a matching accept.

Phase 1 / Phase 2 boundary
--------------------------

Phase 1 (this stage):
- Strict match on serialized ``proposed_input`` for the gated terminal.
- Lenient consumption: the accept token survives a ``MaterializationFailure``
  (planner can retry without re-consulting).
- Intervening-tool-call check is intentionally NOT enforced — Phase 2 ships
  it once tool-call counters live alongside the AdvisorAccept token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from task_center.errors import TaskCenterError
from task_center.model import TaskId

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


@dataclass(frozen=True)
class AdvisorAccept:
    """Per-caller record of the most recent advisor verdict."""

    caller_id: TaskId
    terminal_tool: str
    proposed_input: dict[str, Any] = field(default_factory=dict)
    verdict: str = "accept"  # "accept" | "reject"
    reason: str = ""


class BlockedTerminal(TaskCenterError):
    """Raised by ``check_advisor_accept`` when a gated terminal is blocked."""


def record_accept(
    tc: "TaskCenter",
    caller_id: TaskId,
    terminal_tool: str,
    proposed_input: dict[str, Any],
    verdict: str,
    reason: str,
) -> None:
    """Stash the advisor's verdict on the calling task's slot.

    Idempotent — overwrites any prior accept for the same caller. ``ask_advisor``
    calls this once the advisor task terminates.
    """
    accepts = _get_accepts_dict(tc)
    accepts[caller_id] = AdvisorAccept(
        caller_id=caller_id,
        terminal_tool=terminal_tool,
        proposed_input=dict(proposed_input),
        verdict=verdict,
        reason=reason,
    )


def get_accept(tc: "TaskCenter", caller_id: TaskId) -> AdvisorAccept | None:
    return _get_accepts_dict(tc).get(caller_id)


def check_advisor_accept(
    tc: "TaskCenter",
    caller_id: TaskId,
    terminal_tool: str,
    proposed_input: dict[str, Any],
) -> None:
    """Raise ``BlockedTerminal`` unless the caller's last accept matches.

    Match conditions:
    - An ``AdvisorAccept`` exists for this caller.
    - It approved the same ``terminal_tool``.
    - It approved exactly this ``proposed_input`` (strict equality).
    - Its verdict is ``"accept"``.
    """
    accept = get_accept(tc, caller_id)
    if accept is None:
        raise BlockedTerminal(
            f"{terminal_tool}: must consult advisor (ask_advisor) first"
        )
    if accept.verdict != "accept":
        raise BlockedTerminal(
            f"{terminal_tool}: advisor rejected this proposal "
            f"(reason: {accept.reason!r}); call a different terminal"
        )
    if accept.terminal_tool != terminal_tool:
        raise BlockedTerminal(
            f"{terminal_tool}: advisor approved a different terminal "
            f"({accept.terminal_tool!r})"
        )
    if accept.proposed_input != proposed_input:
        raise BlockedTerminal(
            f"{terminal_tool}: payload differs from advisor-approved input"
        )


def _get_accepts_dict(tc: "TaskCenter") -> dict[TaskId, AdvisorAccept]:
    """TaskCenter holds the per-caller AdvisorAccept dict on a private slot."""
    accepts = getattr(tc, "_advisor_accepts", None)
    if accepts is None:
        accepts = {}
        tc._advisor_accepts = accepts  # type: ignore[attr-defined]
    return accepts


__all__ = [
    "AdvisorAccept",
    "BlockedTerminal",
    "check_advisor_accept",
    "get_accept",
    "record_accept",
]
