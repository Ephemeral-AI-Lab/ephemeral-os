"""Shared path utilities for scope overlap, path normalization, and ltree conversion."""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# ltree conversion for PostgreSQL hierarchical queries
# ---------------------------------------------------------------------------

_LTREE_UNSAFE = re.compile(r"[^a-zA-Z0-9_]")


def _escape_ltree_char(ch: str) -> str:
    """Reversible character escaping for ltree labels.

    Uses a consistent X{hex} scheme for all unsafe characters.
    This avoids ambiguity — unescaped labels never contain 'X' followed
    by two hex digits because 'X' itself is escaped when present in input.
    """
    return f"X{ord(ch):02x}"


# Characters that are valid in ltree labels but need escaping when they
# could collide with our X{hex} escape sequences.
_LTREE_ESCAPE_PREFIX = re.compile(r"X([0-9a-fA-F]{2})")


def path_to_ltree(path: str) -> str:
    """Convert a file path to a PostgreSQL ltree label path.

    Examples:
        "src/auth/"           -> "src.auth"
        "src/auth/session.py" -> "src.auth.sessionX2epy"
        "src/my-module/foo.py"-> "src.myX2dmodule.fooX2epy"

    Raises ValueError if the path produces an empty ltree.
    """
    parts = path.strip("/").split("/")
    labels = []
    for part in parts:
        # First escape any existing X{hex} patterns to prevent ambiguity
        escaped = _LTREE_ESCAPE_PREFIX.sub(lambda m: f"X58{m.group(1)}", part)
        # Then escape all non-label-safe characters
        label = _LTREE_UNSAFE.sub(lambda m: _escape_ltree_char(m.group()), escaped)
        if label:
            labels.append(label)
    if not labels:
        raise ValueError(f"path {path!r} produced an empty ltree label")
    return ".".join(labels)


# ---------------------------------------------------------------------------
# Path normalization and overlap
# ---------------------------------------------------------------------------


def normalize_path_list(raw: Any) -> list[str]:
    """Normalize a list of paths to a cleaned string list."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
    return out


def paths_overlap(path_a: str | None, path_b: str | None) -> bool:
    """Check if two paths overlap (one is a prefix of the other)."""
    left = _normalise_path(path_a) if path_a else ""
    right = _normalise_path(path_b) if path_b else ""
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _normalise_path(path: str | None) -> str:
    """Normalize a path: strip, remove ./, trailing slashes, backslash to forward."""
    return str(path or "").strip().replace("\\", "/").removeprefix("./").rstrip("/")
