"""Shared path utilities for scope overlap and path normalization."""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Scope path utilities
# ---------------------------------------------------------------------------


class ScopePath:
    """Utility class for path normalization and overlap detection.

    Provides consistent behavior across NoteManager, Conductor, and other
    team components that need to check whether file/directory scopes overlap.
    """

    @staticmethod
    def normalize(paths: list[str] | tuple[str, ...] | None) -> list[str]:
        """Normalize a list of paths: strip, remove ./, trailing slashes, sort."""
        out: list[str] = []
        seen: set[str] = set()
        for raw in paths or ():
            if not isinstance(raw, str):
                continue
            for part in raw.split("|"):
                cleaned = part.strip().replace("\\", "/").removeprefix("./").rstrip("/")
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                out.append(cleaned)
        out.sort()
        return out

    @staticmethod
    def overlaps(path_a: str, path_b: str) -> bool:
        """Return True when two file or directory paths overlap.

        Overlap means one is a prefix of the other, they are equal, or one
        contains the other as a path segment (e.g., /a/b and /a/b/c overlap
        via prefix check; /a/b and /b/a don't overlap).
        """
        left = (path_a or "").strip().rstrip("/")
        right = (path_b or "").strip().rstrip("/")
        if not left or not right:
            return False
        if left == right:
            return True
        if left.startswith(right + "/") or right.startswith(left + "/"):
            return True
        return (
            left.endswith("/" + right)
            or right.endswith("/" + left)
            or ("/" + right + "/") in (left + "/")
            or ("/" + left + "/") in (right + "/")
        )

    @staticmethod
    def any_overlap(paths_a: list[str], paths_b: list[str]) -> bool:
        """Return True if any path in paths_a overlaps any path in paths_b."""
        for a in paths_a:
            for b in paths_b:
                if ScopePath.overlaps(a, b):
                    return True
        return False

    @staticmethod
    def matches_scopes(note_paths: list[str], query_paths: list[str]) -> bool:
        """Return True if note_paths match the query_paths.

        If note_paths is empty, returns True (no restriction).
        Otherwise returns True if any note_path overlaps any query_path.
        """
        if not note_paths:
            return True
        normalized = [s.rstrip("/") for s in query_paths if s]
        return any(ScopePath.overlaps(np, qp) for np in note_paths for qp in normalized)


# ---------------------------------------------------------------------------
# Legacy function exports (for backwards compatibility)
# ---------------------------------------------------------------------------


def normalize_scope_paths(paths: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize scope paths. Use ScopePath.normalize() for new code."""
    return ScopePath.normalize(paths)


def scope_paths_overlap(path_a: str, path_b: str) -> bool:
    """Check if two paths overlap. Use ScopePath.overlaps() for new code."""
    return ScopePath.overlaps(path_a, path_b)


def scopes_overlap(path_a: str, path_b: str) -> bool:
    """Alias for scope_paths_overlap."""
    return ScopePath.overlaps(path_a, path_b)


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
    if not path_a or not path_b:
        return False
    return ScopePath.overlaps(path_a, path_b)


def _normalise_path(path: str | None) -> str:
    """Normalize a path: strip, remove ./, trailing slashes, backslash to forward."""
    return str(path or "").strip().replace("\\", "/").removeprefix("./").rstrip("/")


def scope_paths_from_payload(payload: Any) -> list[str]:
    """Extract the most likely scope paths from a work-item payload."""
    if not isinstance(payload, dict):
        return []
    collected: list[str] = []
    for key in (
        "touches_paths",
        "target_paths",
        "stale_subsystems",
        "paths",
        "files",
        "owned_files",
    ):
        raw = payload.get(key)
        if isinstance(raw, list):
            collected.extend(str(item) for item in raw if isinstance(item, str))
    raw_verify = payload.get("verify")
    if isinstance(raw_verify, list):
        for item in raw_verify:
            if isinstance(item, str):
                collected.extend(
                    path.split("::", 1)[0].strip() for path in _PY_PATH_RE.findall(item)
                )
    elif isinstance(raw_verify, str):
        collected.extend(path.split("::", 1)[0].strip() for path in _PY_PATH_RE.findall(raw_verify))
    for key in ("file_path", "path", "subsystem"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            collected.append(raw)
    return ScopePath.normalize(collected)


_PY_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.py)(?![A-Za-z0-9_./-])")
