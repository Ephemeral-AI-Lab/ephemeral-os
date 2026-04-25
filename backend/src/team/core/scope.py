"""Shared path utilities for scope overlap and path normalization."""

from __future__ import annotations


def normalize_scope_paths(paths: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize paths: strip, remove ./, trailing slashes, dedupe, sort."""
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


def scope_paths_overlap(path_a: str, path_b: str) -> bool:
    """Return True when two file or directory paths overlap.

    Overlap means one is a prefix of the other, they are equal, or one
    contains the other as a path segment.
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


_TEST_PATH_COMPONENTS = {"test", "tests", "__tests__"}
_TEST_FILE_SUFFIXES = (
    "_test.py",
    "_spec.py",
    "-test.py",
    "-spec.py",
    "_test.go",
    "_test.rs",
)


def is_test_scope_path(path: str) -> bool:
    """Return True when *path* names a test file or test directory scope."""
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    if not parts:
        return False
    lowered_parts = [part.lower() for part in parts]
    if any(part in _TEST_PATH_COMPONENTS for part in lowered_parts):
        return True
    basename = lowered_parts[-1]
    return (
        basename == "conftest.py"
        or basename.startswith("test_")
        or basename.startswith("test-")
        or basename.endswith(_TEST_FILE_SUFFIXES)
        or ".test." in basename
        or ".spec." in basename
    )
