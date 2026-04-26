"""Shared path helpers for workspace-scoped code intelligence operations."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(file_path: str, workspace_root: str = "") -> str:
    """Resolve *file_path* against *workspace_root* without relative escape.

    Absolute paths are preserved because Daytona tools commonly pass canonical
    sandbox paths. Relative paths are normalized under ``workspace_root`` and
    rejected if they traverse outside that root.
    """
    path = Path(str(file_path))
    if path.is_absolute() or not workspace_root:
        return str(path)

    root = Path(workspace_root).resolve(strict=False)
    resolved = (root / path).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace root: {file_path}") from exc
    return str(resolved)
