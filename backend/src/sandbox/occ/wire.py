"""Wire-format helpers for runtime OCC requests and responses."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from sandbox.occ.changeset.types import (
    BinaryChange,
    Change,
    ChangesetResult,
    DeleteChange,
    EditChange,
    FileResult,
    FileStatus,
    OpaqueDirChange,
    SymlinkChange,
    UpperChangeLike,
    WriteChange,
)


def _edit_to_dict(edit: Any) -> dict[str, Any]:
    if is_dataclass(edit) and not isinstance(edit, type):
        data = asdict(edit)
    elif isinstance(edit, dict):
        data = dict(edit)
    else:
        data = dict(vars(edit))
    if {"start_line", "end_line"} <= set(data):
        raise ValueError("unsupported edit kind: line_range")
    kind = str(data.get("kind") or "")
    if kind and kind != "search_replace":
        raise ValueError(f"unsupported edit kind: {kind}")
    if "old_text" not in data:
        raise ValueError("edit requires old_text")
    if "new_text" not in data:
        raise ValueError("edit requires new_text")
    data["kind"] = "search_replace"
    return data


def _edit_from_dict(d: dict[str, Any]) -> Any:
    from sandbox.occ.patching.patcher import SearchReplaceEdit

    kind = str(d.get("kind") or "")
    if {"start_line", "end_line"} <= set(d):
        raise ValueError("unsupported edit kind: line_range")
    if kind and kind != "search_replace":
        raise ValueError(f"unsupported edit kind: {kind}")
    if "old_text" not in d:
        raise ValueError("edit requires old_text")
    if "new_text" not in d:
        raise ValueError("edit requires new_text")
    return SearchReplaceEdit(
        old_text=str(d["old_text"]),
        new_text=str(d["new_text"]),
    )


@dataclass
class _UpperChange:
    rel: str
    kind: str
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


def _bytes_from_wire(value: Any) -> bytes | None:
    if value is None:
        return None
    return base64.b64decode(str(value).encode("ascii"))


def upper_change_from_dict(d: dict[str, Any]) -> UpperChangeLike:
    return _UpperChange(
        rel=str(d["rel"]),
        kind=str(d["kind"]),
        base_bytes=_bytes_from_wire(d.get("base_bytes")),
        upper_bytes=_bytes_from_wire(d.get("upper_bytes")),
        base_existed=bool(d.get("base_existed", True)),
    )


# -- Typed Change codecs ---------------------------------------------------


def change_to_dict(change: Change) -> dict[str, Any]:
    """Encode a typed :class:`Change` for the ``occ.apply_changeset`` wire op."""
    if isinstance(change, WriteChange):
        return {
            "kind": "write",
            "path": change.path,
            "base_hash": change.base_hash,
            "base_existed": change.base_existed,
            "final_content": change.final_content,
        }
    if isinstance(change, EditChange):
        return {
            "kind": "edit",
            "path": change.path,
            "edits": [_edit_to_dict(edit) for edit in change.edits],
        }
    if isinstance(change, DeleteChange):
        return {
            "kind": "delete",
            "path": change.path,
            "base_hash": change.base_hash,
        }
    if isinstance(change, SymlinkChange):
        return {
            "kind": "symlink",
            "path": change.path,
            "target": change.target,
        }
    if isinstance(change, OpaqueDirChange):
        return {
            "kind": "opaque_dir",
            "path": change.path,
            "kept_children": sorted(change.kept_children),
        }
    if isinstance(change, BinaryChange):
        final_bytes = change.final_bytes
        return {
            "kind": "binary",
            "path": change.path,
            "final_bytes": (
                base64.b64encode(final_bytes).decode("ascii") if final_bytes is not None else None
            ),
        }
    raise TypeError(f"unsupported Change kind: {type(change).__name__}")


def change_from_dict(d: dict[str, Any]) -> Change:
    """Decode a wire-format change record into the typed :class:`Change` union."""
    kind = str(d.get("kind") or "")
    path = str(d["path"])
    if kind == "write":
        return WriteChange(
            path=path,
            base_hash=str(d.get("base_hash", "")),
            base_existed=bool(d.get("base_existed", False)),
            final_content=str(d.get("final_content", "")),
        )
    if kind == "edit":
        edits = tuple(_edit_from_dict(edit) for edit in d.get("edits", ()))
        return EditChange(path=path, edits=edits)
    if kind == "delete":
        return DeleteChange(path=path, base_hash=str(d.get("base_hash", "")))
    if kind == "symlink":
        return SymlinkChange(path=path, target=str(d.get("target", "")))
    if kind == "opaque_dir":
        kept = frozenset(str(child) for child in d.get("kept_children", ()))
        return OpaqueDirChange(path=path, kept_children=kept)
    if kind == "binary":
        return BinaryChange(path=path, final_bytes=_bytes_from_wire(d.get("final_bytes")))
    raise ValueError(f"unsupported change kind: {kind!r}")


def file_result_to_dict(result: FileResult) -> dict[str, Any]:
    return {
        "path": result.path,
        "status": result.status.value,
        "message": result.message,
        "timings": dict(result.timings),
    }


def file_result_from_dict(d: dict[str, Any]) -> FileResult:
    return FileResult(
        path=str(d.get("path", "")),
        status=FileStatus(str(d.get("status", FileStatus.FAILED.value))),
        message=str(d.get("message", "")),
        timings=dict(d.get("timings") or {}),
    )


def changeset_result_to_dict(result: ChangesetResult) -> dict[str, Any]:
    return {
        "files": [file_result_to_dict(f) for f in result.files],
        "timings": dict(result.timings),
    }


def changeset_result_from_dict(d: dict[str, Any]) -> ChangesetResult:
    files = tuple(file_result_from_dict(f) for f in (d.get("files") or ()))
    return ChangesetResult(files=files, timings=dict(d.get("timings") or {}))
