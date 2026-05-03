"""Logging helpers for write-coordinator performance and abort summaries."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sandbox.code_intelligence.core.types import OperationResult
from sandbox.code_intelligence.mutations.write_coordinator.models import CommitOperation

logger = logging.getLogger(__name__)

_PATH_SAMPLE_LIMIT = 5
_SLOW_LOCK_WAIT_SECONDS = 1.0
_SLOW_WRITE_PHASE_SECONDS = 1.0
_SLOW_WRITE_TOTAL_SECONDS = 2.0


def _change_count(ops: Sequence[CommitOperation]) -> int:
    return sum(len(op.changes) for op in ops)


def _strict_base_count(ops: Sequence[CommitOperation]) -> int:
    return sum(1 for op in ops for change in op.changes if change.strict_base)


def _path_sample(paths: Sequence[str]) -> str:
    if not paths:
        return ""
    sample = list(paths[:_PATH_SAMPLE_LIMIT])
    suffix = ""
    remaining = len(paths) - len(sample)
    if remaining > 0:
        suffix = f", +{remaining} more"
    return ", ".join(sample) + suffix


def _status_counts(results: Sequence[OperationResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def log_slow_phase(
    phase: str,
    elapsed: float,
    ops: Sequence[CommitOperation],
    paths: Sequence[str],
    timings: dict[str, float],
) -> None:
    threshold = (
        _SLOW_LOCK_WAIT_SECONDS if phase == "lock_wait" else _SLOW_WRITE_PHASE_SECONDS
    )
    if elapsed < threshold:
        return
    logger.warning(
        "code intelligence write phase slow: phase=%s elapsed=%.3fs ops=%d "
        "changes=%d paths=%d strict_base_changes=%d path_sample=%s timings=%s",
        phase,
        elapsed,
        len(ops),
        _change_count(ops),
        len(paths),
        _strict_base_count(ops),
        _path_sample(paths),
        dict(timings),
    )


def log_lock_timeout(
    *,
    timings: dict[str, float],
    lock_conflict: str,
    ops: Sequence[CommitOperation],
    all_paths: Sequence[str],
) -> None:
    logger.warning(
        "code intelligence write lock timeout: wait=%.3fs total=%.3fs "
        "conflict_file=%s ops=%d changes=%d paths=%d "
        "strict_base_changes=%d path_sample=%s timings=%s",
        timings["lock_wait"],
        timings["total"],
        lock_conflict,
        len(ops),
        _change_count(ops),
        len(all_paths),
        _strict_base_count(ops),
        _path_sample(all_paths),
        dict(timings),
    )


def log_resolve_conflict(
    *,
    status: str,
    conflict_file: str,
    reason: str,
    elapsed: float,
    ops: Sequence[CommitOperation],
    all_paths: Sequence[str],
    timings: dict[str, float],
) -> None:
    logger.warning(
        "code intelligence write conflict after resolve: "
        "status=%s conflict_file=%s reason=%s elapsed=%.3fs "
        "ops=%d changes=%d paths=%d strict_base_changes=%d "
        "path_sample=%s timings=%s",
        status,
        conflict_file,
        reason,
        elapsed,
        len(ops),
        _change_count(ops),
        len(all_paths),
        _strict_base_count(ops),
        _path_sample(all_paths),
        dict(timings),
    )


def log_checked_apply_fallback(
    *,
    reason: str,
    conflict_file: str | None,
    message: str,
    checked_apply: float,
    ops: Sequence[CommitOperation],
    all_paths: Sequence[str],
    timings: dict[str, float],
) -> None:
    logger.warning(
        "code intelligence checked apply fell back: reason=%s "
        "conflict_file=%s message=%s checked_apply=%.3fs ops=%d "
        "changes=%d paths=%d strict_base_changes=%d path_sample=%s timings=%s",
        reason,
        conflict_file,
        message,
        checked_apply,
        len(ops),
        _change_count(ops),
        len(all_paths),
        _strict_base_count(ops),
        _path_sample(all_paths),
        dict(timings),
    )


def log_commit_summary(
    ops: Sequence[CommitOperation],
    paths: Sequence[str],
    results: Sequence[OperationResult],
    timings: dict[str, float],
) -> None:
    total = timings.get("total", 0.0)
    has_abort = any(not result.success for result in results)
    if total < _SLOW_WRITE_TOTAL_SECONDS and not has_abort:
        return
    logger.warning(
        "code intelligence write commit summary: total=%.3fs ops=%d changes=%d "
        "paths=%d strict_base_changes=%d statuses=%s path_sample=%s timings=%s",
        total,
        len(ops),
        _change_count(ops),
        len(paths),
        _strict_base_count(ops),
        _status_counts(results),
        _path_sample(paths),
        dict(timings),
    )
