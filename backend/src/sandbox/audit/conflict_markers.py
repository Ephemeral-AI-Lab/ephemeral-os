"""Single source of truth for conflict-error message markers.

Both ``sandbox.audit.translation._conflict_reason_from_error`` and
``sandbox.api._tool_verbs._error_classification.is_edit_conflict``/``is_shell_conflict``
inspect raised-error messages to decide whether a failure is a recoverable
conflict or a hard error. They must agree — if they drift, a verb that
raises ``X`` can have its audit event reclassified as
``OPERATION_CONFLICTED`` while the caller sees a hard exception, or vice
versa. Keep the lists here and import from both sides.
"""

from __future__ import annotations

EDIT_CONFLICT_MARKERS: tuple[str, ...] = (
    "anchor not found",
    "anchor occurrence count mismatch",
    "aborted_overlap",
    "old_text_not_found",
)
SHELL_CONFLICT_MARKERS: tuple[str, ...] = (
    "overlay capture refuses escaping symlink target",
    "unsupported tracked change kind: symlinkchange",
)
# OCC-level markers represent commit-stage conflicts that are surfaced as
# `FileResult` statuses today, never raised — but the audit translator's
# fallback path still inspects error text in case future code paths do raise
# with these messages. Listed here so audit and api stay aligned.
OCC_CONFLICT_MARKERS: tuple[str, ...] = (
    "aborted_version",
    "content changed",
)
ALL_CONFLICT_MARKERS: tuple[str, ...] = (
    EDIT_CONFLICT_MARKERS + SHELL_CONFLICT_MARKERS + OCC_CONFLICT_MARKERS
)

__all__ = [
    "ALL_CONFLICT_MARKERS",
    "EDIT_CONFLICT_MARKERS",
    "OCC_CONFLICT_MARKERS",
    "SHELL_CONFLICT_MARKERS",
]
