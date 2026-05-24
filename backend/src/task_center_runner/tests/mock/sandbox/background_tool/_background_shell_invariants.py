"""Shared invariant helper for background-shell live tests."""

from __future__ import annotations

import json
from pathlib import Path

_ERROR_NEEDLES = (
    "internal_error",
    "stale lowerdir",
    "mount_failed",
    "manifest references missing layer",
)


def _read_rows(jsonl_path: Path) -> list[dict[str, object]]:
    if not jsonl_path.exists():
        return []
    rows: list[dict[str, object]] = []
    raw = jsonl_path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Truncated JSON at the engine-kill cut point is expected in T4.
            continue
    return rows


def assert_shell_audit_invariants(
    jsonl_path: Path,
    *,
    expect_truncated: bool = False,
) -> None:
    """Assert background-shell runs did not emit known sandbox failure text."""
    del expect_truncated
    _read_rows(jsonl_path)
    if jsonl_path.exists():
        raw_text = jsonl_path.read_text(encoding="utf-8", errors="replace")
        for needle in _ERROR_NEEDLES:
            assert needle not in raw_text, (
                f"AC-11 violation: '{needle}' appears in {jsonl_path}"
            )


__all__ = ["assert_shell_audit_invariants"]
