"""Path helpers for sandbox file-oriented tools."""

from __future__ import annotations

from sandbox.daytona import _SandboxContext


def _path_error(exc: Exception, path: str) -> str | None:
    """Return a human-readable message if *exc* is a path-not-found error, else None."""
    msg = str(exc)
    if isinstance(exc, FileNotFoundError) or "No such file or directory" in msg:
        return f"Path does not exist: {path}"
    # The sandbox SDK wraps errors and may lose the inner message.
    _sdk_prefixes = ("Failed to list files", "Failed to upload files", "Failed to download")
    if any(msg.startswith(p) for p in _sdk_prefixes) and msg.rstrip().endswith(":"):
        return f"Path does not exist: {path}"
    return None


def _get_repo_root(context: _SandboxContext) -> str | None:
    """Return the canonical sandbox repo root for file-oriented tools."""
    return context.get("repo_root")


def _resolve_path(path: str, context: _SandboxContext) -> str:
    """Resolve a relative path against the sandbox repo root."""
    if path.startswith("/"):
        return path
    repo_root = _get_repo_root(context)
    if repo_root:
        return f"{repo_root}/{path}"
    return path


def _normalized_path(path: str) -> str:
    """Return a stable absolute-or-relative path without trailing separators."""
    if path == "/":
        return path
    return path.rstrip("/") or path


__all__ = ["_get_repo_root", "_normalized_path", "_path_error", "_resolve_path"]
