"""Helpers projecting a :class:`ChangesetResult` onto guarded tool API results.

Tool verbs and in-sandbox runtime handlers both translate the gate's per-file
``FileResult`` outcomes into public result shapes, so the projection stays with
the tool API instead of living in private package utilities.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.api.utils.models import ConflictInfo
from sandbox.occ.changeset.types import (
    FileResult,
    is_published_status,
    is_success_status,
)


def committed_paths(
    files: Sequence[FileResult],
    *,
    fallback_path: str,
) -> tuple[str, ...]:
    """Return paths of every COMMITTED ``FileResult``, or a single-path fallback."""
    committed = tuple(f.path for f in files if is_published_status(f.status) and f.path)
    if committed:
        return committed
    aborted = next(
        (f for f in files if not is_published_status(f.status) and f.path),
        None,
    )
    if aborted is not None:
        return (aborted.path,)
    return (fallback_path,) if not files else ()


def published_paths(files: Sequence[FileResult]) -> tuple[str, ...]:
    """Return paths of every published ``FileResult``."""
    return tuple(f.path for f in files if is_published_status(f.status) and f.path)


def conflict_and_status(
    files: Sequence[FileResult],
) -> tuple[ConflictInfo | None, str]:
    """Surface the first non-COMMITTED ``FileResult`` as a ``ConflictInfo`` + status."""
    if not files:
        return None, "committed"
    bad = next((f for f in files if not is_success_status(f.status)), None)
    if bad is None:
        return None, "committed"
    status = bad.status.value
    return (
        ConflictInfo(
            reason=status,
            conflict_file=bad.path or None,
            message=bad.message or status,
        ),
        status,
    )


__all__ = ["committed_paths", "conflict_and_status", "published_paths"]
