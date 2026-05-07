"""cwd and environment policy for workspace-replaced commands."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_workspace_cwd(
    *,
    declared_workspace_root: str | Path,
    mounted_workspace_root: str | Path,
    cwd: str,
) -> Path:
    """Resolve *cwd* after replacing the declared workspace path.

    Absolute paths must stay under the declared workspace root. Relative paths
    resolve inside the mounted workspace. The returned path is inside
    ``mounted_workspace_root`` so copy-backed test mounts and real namespace
    mounts share the same policy.
    """
    declared_root = Path(declared_workspace_root)
    mounted_root = Path(mounted_workspace_root)
    raw = str(cwd or ".").strip() or "."
    candidate = Path(raw)
    if candidate.is_absolute():
        rel = _relative_to_declared_workspace(candidate, declared_root)
        resolved = mounted_root / rel
    else:
        resolved = mounted_root / candidate
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def command_environment(extra: dict[str, str]) -> dict[str, str]:
    """Return the subprocess environment for a guarded command."""
    return {**os.environ, **extra, "GIT_OPTIONAL_LOCKS": "0"}


def _relative_to_declared_workspace(candidate: Path, declared_root: Path) -> Path:
    candidate_text = os.path.normpath(candidate.as_posix())
    root_text = os.path.normpath(declared_root.as_posix())
    if os.path.commonpath([root_text, candidate_text]) != root_text:
        raise ValueError(f"cwd escapes workspace replacement root: {candidate}")
    try:
        return Path(candidate_text).relative_to(root_text)
    except ValueError as exc:  # pragma: no cover - commonpath guards this.
        raise ValueError(f"cwd escapes workspace replacement root: {candidate}") from exc


__all__ = [
    "command_environment",
    "resolve_workspace_cwd",
]
