"""Edit primitive for namespace-mounted workspaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from sandbox._shared.models import EditFileResult
from sandbox._shared.tool_primitives.file_ops import (
    read_bytes_no_follow,
    write_bytes_no_follow,
)


def compute(
    args: Mapping[str, object] | str,
    *,
    old_text: str | None = None,
    new_text: str | None = None,
    expected_occurrences: int = 1,
) -> EditFileResult:
    path, edits = _normalize_args(
        args,
        old_text=old_text,
        new_text=new_text,
        expected_occurrences=expected_occurrences,
    )
    text = read_bytes_no_follow(path).decode("utf-8")
    current = text
    for old, new, expected in edits:
        if not old:
            raise ValueError(f"edit anchor old_text must be non-empty for {path}")
        occurrences = current.count(old)
        if occurrences != expected:
            raise ValueError(
                f"anchor not found in {path}: expected {expected} "
                f"occurrences of {old!r}, found {occurrences}"
            )
        current = current.replace(old, new, expected)
    write_bytes_no_follow(path, current.encode("utf-8"))
    return EditFileResult(changed_paths=(path,), status="ok", applied_edits=len(edits))


def _normalize_args(
    args: Mapping[str, object] | str,
    *,
    old_text: str | None,
    new_text: str | None,
    expected_occurrences: int,
) -> tuple[str, tuple[tuple[str, str, int], ...]]:
    if not isinstance(args, Mapping):
        if old_text is None or new_text is None:
            raise ValueError("old_text and new_text are required")
        return (
            _absolute_no_escape(args),
            ((old_text, new_text, expected_occurrences),),
        )
    path = _absolute_no_escape(str(args.get("path") or ""))
    edits_raw = args.get("edits")
    if edits_raw is None and "old_text" in args:
        edits_raw = [args]
    if not isinstance(edits_raw, Sequence) or isinstance(edits_raw, (str, bytes)):
        raise ValueError("edits must be a list of search/replace objects")
    edits: list[tuple[str, str, int]] = []
    for raw in edits_raw:
        if not isinstance(raw, Mapping):
            raise ValueError("each edit must be an object")
        expected = raw.get("expected_occurrences")
        expected_count = 1 if expected is None else int(expected)
        if expected_count < 0:
            raise ValueError("expected_occurrences must be >= 0")
        edits.append(
            (
                str(raw.get("old_text") or ""),
                str(raw.get("new_text") or ""),
                expected_count,
            )
        )
    return path, tuple(edits)


def _absolute_no_escape(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        raise ValueError("path is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError(f"path escapes workspace via '..': {path}")
        candidate = Path.cwd() / candidate
    return candidate.as_posix()


__all__ = ["compute"]
