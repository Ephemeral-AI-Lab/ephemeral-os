"""Newest-first merged reads for layer-stack manifests."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path, PurePosixPath

from sandbox.layer_stack.changes import normalize_layer_path
from sandbox.layer_stack.layer_index import (
    OPAQUE_MARKER,
    WHITEOUT_PREFIX,
    LayerIndex,
    build_layer_index,
    has_ancestor_in,
)
from sandbox.layer_stack.manifest import LayerRef, Manifest


__all__ = ["LayerStackStorageError", "MergedView", "OPAQUE_MARKER", "WHITEOUT_PREFIX"]


class LayerStackStorageError(RuntimeError):
    """Raised when a manifest references missing or invalid layer storage."""


class MergedView:
    """Reads paths through a frozen manifest without mutating layer state."""

    def __init__(self, storage_root: str | Path) -> None:
        self._storage_root = Path(storage_root)
        self._layer_index_cache: dict[str, LayerIndex] = {}

    def _layer_index(self, layer: LayerRef) -> LayerIndex:
        cached = self._layer_index_cache.get(layer.layer_id)
        if cached is not None:
            return cached
        index = build_layer_index(self._layer_dir(layer))
        return self._layer_index_cache.setdefault(layer.layer_id, index)

    def evict_layer_index(self, layer_id: str) -> None:
        """Drop the cached presence index for ``layer_id``.

        Called by ``LayerStackManager`` after a layer dir is removed; without
        this the cache grows unboundedly on long-running daemons.
        """
        self._layer_index_cache.pop(layer_id, None)

    def read_bytes(self, path: str, manifest: Manifest) -> tuple[bytes | None, bool]:
        rel = normalize_layer_path(path)
        for layer in manifest.layers:
            index = self._layer_index(layer)
            if rel in index.whiteouts:
                return None, False
            if rel in index.files:
                layer_dir = self._layer_dir(layer)
                candidate = _join_rel(layer_dir, rel)
                if candidate.is_symlink():
                    return os.readlink(candidate).encode("utf-8"), True
                if candidate.is_file():
                    return candidate.read_bytes(), True
            if has_ancestor_in(rel, index.files):
                return None, False
            if has_ancestor_in(rel, index.opaque_dirs):
                return None, False
        return None, False

    def read_text(self, path: str, manifest: Manifest) -> tuple[str, bool]:
        content, exists = self.read_bytes(path, manifest)
        if not exists:
            return "", False
        if content is None:
            return "", True
        return content.decode("utf-8"), True

    def read_symlink(self, path: str, manifest: Manifest) -> tuple[str, bool]:
        rel = normalize_layer_path(path)
        for layer in manifest.layers:
            layer_dir = self._layer_dir(layer)
            if _whiteout_path(layer_dir, rel).exists():
                return "", False
            candidate = _join_rel(layer_dir, rel)
            if candidate.is_symlink():
                return os.readlink(candidate), True
            if candidate.exists():
                return "", False
            if _has_file_ancestor(layer_dir, rel):
                return "", False
            if _has_opaque_ancestor(layer_dir, rel):
                return "", False
        return "", False

    def list_dir(self, path: str, manifest: Manifest) -> tuple[str, ...]:
        rel = normalize_layer_path(path, allow_root=True)
        names: dict[str, None] = {}
        hidden: set[str] = set()

        for layer in manifest.layers:
            layer_dir = self._layer_dir(layer)
            base = _join_rel(layer_dir, rel)
            if rel and _whiteout_path(layer_dir, rel).exists() and not base.is_dir():
                return tuple(sorted(names))
            if base.is_symlink() or base.is_file() or _has_file_ancestor(layer_dir, rel):
                return tuple(sorted(names))
            if base.is_dir():
                for child in sorted(base.iterdir(), key=lambda item: item.name):
                    if child.name == OPAQUE_MARKER:
                        continue
                    if _is_whiteout(child.name):
                        hidden.add(child.name[len(WHITEOUT_PREFIX) :])
                        continue
                    if child.name not in hidden and child.name not in names:
                        names[child.name] = None

            if _opaque_marker_path(layer_dir, rel).exists():
                return tuple(sorted(names))

        return tuple(sorted(names))

    def materialize(
        self,
        destination: str | Path,
        manifest: Manifest,
        *,
        link_ok: bool = False,
    ) -> None:
        """Materialise *manifest* into *destination*.

        ``link_ok=True`` hardlinks regular files from source layers. Only safe
        when the caller treats *destination* as read-only (e.g. the overlay
        lowerdir from :meth:`LayerStackManager.prepare_workspace_snapshot`);
        a writer would corrupt the source layer through the shared inode.
        """
        dest = Path(destination)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        for layer in reversed(manifest.layers):
            self._apply_layer(self._layer_dir(layer), dest, link_ok=link_ok)

    def _layer_dir(self, layer: LayerRef) -> Path:
        layer_path = Path(layer.path)
        if not layer_path.is_absolute():
            layer_path = self._storage_root / layer_path
        if not layer_path.is_dir():
            raise LayerStackStorageError(
                f"manifest references missing layer {layer.layer_id}: {layer.path}"
            )
        return layer_path

    def _apply_layer(
        self,
        layer_dir: Path,
        dest: Path,
        *,
        link_ok: bool = False,
    ) -> None:
        entries = tuple(sorted(layer_dir.rglob("*"), key=lambda item: item.as_posix()))

        for marker in entries:
            if marker.name != OPAQUE_MARKER:
                continue
            target = dest / marker.parent.relative_to(layer_dir)
            _clear_directory(target)

        for whiteout in entries:
            if not _is_whiteout(whiteout.name):
                continue
            rel = whiteout.relative_to(layer_dir)
            target = dest / rel.parent / whiteout.name[len(WHITEOUT_PREFIX) :]
            _remove_path(target)

        for entry in entries:
            if entry.name == OPAQUE_MARKER or _is_whiteout(entry.name):
                continue
            rel = entry.relative_to(layer_dir)
            target = dest / rel
            if entry.is_symlink():
                _replace_symlink(target, os.readlink(entry))
            elif entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                _remove_path(target)
                if link_ok:
                    _link_or_copy(entry, target)
                else:
                    shutil.copy2(entry, target)


def _join_rel(root: Path, rel: str) -> Path:
    if not rel:
        return root
    return root.joinpath(*PurePosixPath(rel).parts)


def _whiteout_path(layer_dir: Path, rel: str) -> Path:
    target = PurePosixPath(rel)
    parent_parts = tuple(part for part in target.parent.parts if part != ".")
    return layer_dir.joinpath(*parent_parts, f"{WHITEOUT_PREFIX}{target.name}")


def _opaque_marker_path(layer_dir: Path, rel: str) -> Path:
    return _join_rel(layer_dir, rel) / OPAQUE_MARKER


def _has_opaque_ancestor(layer_dir: Path, rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    for index in range(1, len(parts)):
        ancestor = "/".join(parts[:index])
        if _opaque_marker_path(layer_dir, ancestor).exists():
            return True
    return False


def _has_file_ancestor(layer_dir: Path, rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    for index in range(1, len(parts)):
        ancestor = _join_rel(layer_dir, "/".join(parts[:index]))
        if ancestor.is_symlink() or ancestor.is_file():
            return True
    return False


def _is_whiteout(name: str) -> bool:
    return name.startswith(WHITEOUT_PREFIX) and name != OPAQUE_MARKER


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        _remove_path(child)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` into ``dst``; copy on EXDEV (cross-FS) or EPERM."""
    try:
        os.link(src, dst)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM):
            raise
        shutil.copy2(src, dst)


def _replace_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(path)
    os.symlink(target, path)
