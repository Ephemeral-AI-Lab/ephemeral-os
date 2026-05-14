"""Shared edit-anchor application logic for direct + gated stagers."""

from __future__ import annotations

from sandbox.occ.changeset.types import EditChange, FileResult, FileStatus


def apply_edit_content(
    path: str,
    content: bytes,
    exists: bool,
    change: EditChange,
) -> bytes | FileResult:
    if not exists:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="file does not exist",
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="file is not utf-8 text",
        )
    count = text.count(change.old_text)
    if count == 0:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="anchor not found",
        )
    if count != change.expected_occurrences:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="anchor occurrence count mismatch",
        )
    text = text.replace(change.old_text, change.new_text, change.expected_occurrences)
    return text.encode("utf-8")


__all__ = ["apply_edit_content"]
