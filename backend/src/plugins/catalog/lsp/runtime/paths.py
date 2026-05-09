"""Map agent-facing paths to projection-snapshot URIs and back.

Pyright sees files only via ``file://<lowerdir>/<repo_path>`` URIs over
the snapshot lowerdir; the agent always speaks in repo-relative or
workspace-absolute paths. This module is the only place that knows about
both encodings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

__all__ = [
    "PathMappingError",
    "PathMapper",
    "snapshot_uri_from_repo_path",
    "repo_path_from_snapshot_uri",
]


class PathMappingError(ValueError):
    """Raised when a path or URI cannot be mapped through the projection."""


@dataclass(frozen=True)
class PathMapper:
    """Resolve repo-relative or absolute paths against a snapshot lowerdir.

    Concretely: when Pyright is rooted at ``<lowerdir>``, a repo path
    like ``pkg/mod.py`` becomes ``file://<lowerdir>/pkg/mod.py``; an absolute
    workspace path like ``/testbed/pkg/mod.py`` is rewritten to the
    same projection-relative form when ``workspace_root`` is set, otherwise
    we treat the path as already projection-relative.
    """

    lowerdir: str
    workspace_root: str = ""

    def to_snapshot_uri(self, path: str) -> str:
        rel = self._to_projection_relative(path)
        return snapshot_uri_from_repo_path(self.lowerdir, rel)

    def to_full_path(self, path: str) -> str:
        """Return the absolute on-disk path under the projection lowerdir."""
        rel = self._to_projection_relative(path)
        return str(PurePosixPath(self.lowerdir.rstrip("/")) / rel)

    def from_snapshot_uri(self, uri: str) -> str:
        return repo_path_from_snapshot_uri(self.lowerdir, uri)

    def _to_projection_relative(self, path: str) -> str:
        normalized = (path or "").strip()
        if not normalized:
            raise PathMappingError("empty file_path")
        if normalized.startswith("/"):
            workspace = self.workspace_root.rstrip("/")
            if workspace and normalized.startswith(f"{workspace}/"):
                return normalized[len(workspace) + 1 :]
            # Absolute paths outside workspace_root pass through; Pyright
            # rejects out-of-root URIs so this surfaces as a clear LSP error.
            return normalized.lstrip("/")
        return normalized


def snapshot_uri_from_repo_path(lowerdir: str, repo_path: str) -> str:
    """Build a ``file://`` URI under the projection lowerdir."""
    if not lowerdir:
        raise PathMappingError("lowerdir is required to build a snapshot URI")
    if not repo_path:
        raise PathMappingError("repo_path is required to build a snapshot URI")
    rel_posix = PurePosixPath(repo_path).as_posix().lstrip("/")
    full = PurePosixPath(lowerdir.rstrip("/")) / rel_posix
    return "file://" + quote(str(full), safe="/")


def repo_path_from_snapshot_uri(lowerdir: str, uri: str) -> str:
    """Reverse the mapping. Rejects URIs that escape the projection."""
    if not uri:
        raise PathMappingError("empty uri")
    parsed = urlparse(uri)
    if parsed.scheme not in ("", "file"):
        raise PathMappingError(f"unsupported uri scheme: {parsed.scheme!r}")
    full_path = unquote(parsed.path) or unquote(uri[len("file://") :]) if uri.startswith(
        "file://"
    ) else unquote(uri)
    full_path = full_path or unquote(parsed.path)
    base = PurePosixPath(lowerdir.rstrip("/"))
    target = PurePosixPath(full_path)
    try:
        rel = target.relative_to(base)
    except ValueError as exc:
        raise PathMappingError(
            f"uri {uri!r} is not under projection lowerdir {lowerdir!r}"
        ) from exc
    return rel.as_posix()
