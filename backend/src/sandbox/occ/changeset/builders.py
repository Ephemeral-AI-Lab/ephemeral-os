"""Source-to-changeset converters for OCC mutation sources."""

from __future__ import annotations

from sandbox.occ.changeset.types import (
    DeleteChange,
    EditChange,
    WriteChange,
)


def build_api_write_change(
    *,
    path: str,
    final_content: bytes | str,
    base_hash: str | None = None,
    create_only: bool = False,
) -> WriteChange:
    """Build a source-tagged write change from the host write API."""
    return WriteChange(
        path=path,
        source="api_write",
        final_content=final_content,
        base_hash=base_hash,
        create_only=create_only,
    )


def build_api_edit_change(
    *,
    path: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
) -> EditChange:
    """Build a source-tagged edit change from the host edit API."""
    return EditChange(
        path=path,
        source="api_edit",
        old_text=old_text,
        new_text=new_text,
        expected_occurrences=expected_occurrences,
    )


def build_api_delete_change(*, path: str, base_hash: str) -> DeleteChange:
    """Build a source-tagged delete change from a host delete API."""
    return DeleteChange(path=path, source="api_write", base_hash=base_hash)


def build_overlay_write_change(*, path: str, final_content: bytes) -> WriteChange:
    """Build an overlay-captured full-file write without a caller base hash."""
    return WriteChange(
        path=path,
        source="overlay_capture",
        final_content=final_content,
        base_hash=None,
    )


def build_overlay_delete_change(
    *,
    path: str,
    base_hash: str | None = None,
) -> DeleteChange:
    """Build an overlay-captured delete whose base hash can be inferred later."""
    return DeleteChange(path=path, source="overlay_capture", base_hash=base_hash)


__all__ = [
    "build_api_delete_change",
    "build_api_edit_change",
    "build_api_write_change",
    "build_overlay_delete_change",
    "build_overlay_write_change",
]
