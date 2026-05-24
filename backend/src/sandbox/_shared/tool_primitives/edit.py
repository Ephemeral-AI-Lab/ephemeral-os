"""Edit primitive for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox._shared.models import EditFileResult
from sandbox._shared.tool_primitives.file_ops import (
    read_bytes_no_follow,
    write_bytes_no_follow,
)


def compute(
    path: str,
    *,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
) -> EditFileResult:
    text = read_bytes_no_follow(path).decode("utf-8")
    occurrences = text.count(old_text)
    if occurrences != expected_occurrences:
        raise ValueError(
            f"edit anchor occurrence mismatch for {path}: "
            f"expected {expected_occurrences}, found {occurrences}"
        )
    write_bytes_no_follow(path, text.replace(old_text, new_text).encode("utf-8"))
    return EditFileResult(changed_paths=(path,), status="ok")


__all__ = ["compute"]
