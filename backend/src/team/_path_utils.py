"""Shared path utilities for scope overlap detection and path normalization.

Extracted from scout_briefings.py to reduce duplication and enable reuse.
"""

from __future__ import annotations

from typing import Any


def normalize_path_list(raw: Any) -> list[str]:
    """Normalize a list of paths to a cleaned string list."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
    return out


def normalize_string_list(raw: Any) -> list[str]:
    """Normalize a mixed list to a deduplicated string set."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def coerce_str_set(raw: Any) -> set[str]:
    """Coerce various input types to a string set."""
    if isinstance(raw, set):
        return {item for item in raw if isinstance(item, str) and item}
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str) and item}
    return set()


def summarise_values(raw: Any, limit: int = 5) -> str:
    """Summarize a list of values as a comma-separated string."""
    values = sorted(
        coerce_str_set(raw) if not isinstance(raw, list) else set(normalize_string_list(raw))
    )
    if not values:
        return "none"
    limited = values[:limit]
    suffix = "…" if len(values) > limit else ""
    return ", ".join(limited) + suffix


def paths_overlap(path_a: str | None, path_b: str | None) -> bool:
    """Check if two paths overlap (one is a prefix of the other)."""
    left = _normalise_path(path_a) if path_a else ""
    right = _normalise_path(path_b) if path_b else ""
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


def scope_overlaps_file(scope: str, file_path: str, *, repo_root: str = "") -> bool:
    """Check if a scope ("|"-separated path list) overlaps with a file path."""
    scope_parts = [part for part in str(scope or "").split("|") if part.strip()]
    if not scope_parts:
        return False
    file_variants = path_variants(file_path, repo_root=repo_root)
    for part in scope_parts:
        scope_variants = path_variants(part, repo_root=repo_root)
        for candidate in file_variants:
            for target in scope_variants:
                if paths_overlap(candidate, target):
                    return True
    return False


def path_variants(path: str, *, repo_root: str = "") -> set[str]:
    """Get all path variants (relative, absolute) for a given path."""
    cleaned = _normalise_path(path)
    if not cleaned:
        return set()
    out = {cleaned}
    root = _normalise_path(repo_root)
    if not root:
        return out
    if cleaned.startswith(root + "/"):
        out.add(cleaned[len(root) + 1 :])
    elif not cleaned.startswith("/"):
        out.add(f"{root}/{cleaned}")
    return out


def _normalise_path(path: str | None) -> str:
    """Normalize a path: strip, remove ./, trailing slashes, backslash to forward."""
    return str(path or "").strip().replace("\\", "/").removeprefix("./").rstrip("/")
