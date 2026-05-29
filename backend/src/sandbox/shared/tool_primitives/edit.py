"""Edit primitive for namespace-mounted workspaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sandbox.shared.edit_apply import apply_search_replace
from sandbox.shared.models import EditFileResult
from sandbox.shared.tool_primitives.workspace_filesystem import (
    read_bytes_no_follow,
    required_workspace_path,
    write_bytes_no_follow,
)


def edit_file(args: Mapping[str, object]) -> EditFileResult:
    path, edits = _normalize_args(args)
    current = read_bytes_no_follow(path).decode("utf-8")
    for old, new, replace_all in edits:
        current = apply_search_replace(current, old, new, replace_all=replace_all)
    write_bytes_no_follow(path, current.encode("utf-8"))
    return EditFileResult(changed_paths=(path,), status="ok", applied_edits=len(edits))


def _normalize_args(
    args: Mapping[str, object],
) -> tuple[str, tuple[tuple[str, str, bool], ...]]:
    path = required_workspace_path(args.get("path"))
    edits_raw = args.get("edits")
    if not isinstance(edits_raw, Sequence) or isinstance(edits_raw, (str, bytes)):
        raise ValueError("edits must be a list of search/replace objects")
    edits: list[tuple[str, str, bool]] = []
    for raw in edits_raw:
        if not isinstance(raw, Mapping):
            raise ValueError("each edit must be an object")
        edits.append(
            (
                str(raw.get("old_text") or ""),
                str(raw.get("new_text") or ""),
                bool(raw.get("replace_all", False)),
            )
        )
    return path, tuple(edits)


__all__ = ["edit_file"]
