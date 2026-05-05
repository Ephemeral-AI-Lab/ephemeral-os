"""Failed attempt landscape blocks for planner context."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.attempt import Attempt, AttemptStatus

MAX_FAILED_ATTEMPTS_RENDERED = 6


def failed_attempt_landscape_blocks(
    *,
    current_attempt_id: str | None,
    attempts: list[Attempt],
) -> list[ContextBlock]:
    failed = sorted(
        (
            g
            for g in attempts
            if g.status == AttemptStatus.FAILED
            and g.id != current_attempt_id
        ),
        key=lambda g: g.attempt_sequence_no,
    )
    if not failed:
        return []

    if len(failed) <= MAX_FAILED_ATTEMPTS_RENDERED:
        rendered = failed
        truncated: list[Attempt] = []
    else:
        rendered = failed[-MAX_FAILED_ATTEMPTS_RENDERED:]
        truncated = failed[:-MAX_FAILED_ATTEMPTS_RENDERED]

    blocks: list[ContextBlock] = [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt(g),
            source_id=g.id,
            source_kind="attempt",
            metadata={
                "attempt_sequence_no": str(g.attempt_sequence_no),
                "group_heading": "# Failed Attempts",
                "subheading": f"Attempt {g.attempt_sequence_no}",
            },
        )
        for g in rendered
    ]

    if truncated:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE,
                priority=ContextPriority.MEDIUM,
                text=(
                    f"{len(truncated)} earlier failed attempts omitted "
                    f"(attempt_sequence_no "
                    f"{truncated[0].attempt_sequence_no}-"
                    f"{truncated[-1].attempt_sequence_no}). "
                    f"Most recent {MAX_FAILED_ATTEMPTS_RENDERED} attempts "
                    f"shown above."
                ),
                source_id=None,
                source_kind=None,
                metadata={
                    "truncated_count": str(len(truncated)),
                    "group_heading": "# Failed Attempts",
                    "subheading": "Earlier attempts omitted",
                },
            )
        )
    return blocks


def _render_failed_attempt(attempt: Attempt) -> str:
    criteria_block = (
        "\n".join(f"  - {c}" for c in attempt.evaluation_criteria) or "  (none)"
    )
    return (
        f"task_specification: {attempt.task_specification or '(missing)'}\n"
        f"evaluation_criteria:\n{criteria_block}\n"
        f"fail_reason: {attempt.fail_reason.value if attempt.fail_reason else 'unknown'}"
    )


__all__ = [
    "MAX_FAILED_ATTEMPTS_RENDERED",
    "failed_attempt_landscape_blocks",
]
