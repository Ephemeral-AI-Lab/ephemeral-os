"""Materialized lowerdir cache for leased layer-stack snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.merged_view import MergedView
from sandbox.layer_stack.metrics import LowerdirCacheMetrics


MATERIALIZED_DIR = "materialized"
SNAPSHOT_METADATA_FILE = "snapshot.json"


@dataclass(frozen=True)
class MaterializedSnapshot:
    manifest_version: int
    root_hash: str
    lowerdir: str
    created_at: float
    byte_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "root_hash": self.root_hash,
            "lowerdir": self.lowerdir,
            "created_at": self.created_at,
            "byte_count": self.byte_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "MaterializedSnapshot":
        return cls(
            manifest_version=int(payload["manifest_version"]),
            root_hash=str(payload["root_hash"]),
            lowerdir=str(payload["lowerdir"]),
            created_at=float(payload["created_at"]),
            byte_count=int(payload["byte_count"]),
        )


@dataclass(frozen=True)
class SnapshotCacheLookup:
    snapshot: MaterializedSnapshot
    cache_hit: bool
    timings: dict[str, float]


class MaterializedSnapshotCache:
    """Builds and reuses read-only workspace lowerdirs for manifest snapshots."""

    def __init__(
        self,
        storage_root: str | Path,
        *,
        materializer: Callable[[Path, Manifest], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._storage_root = Path(storage_root)
        self._cache_root = self._storage_root / MATERIALIZED_DIR
        self._materializer = materializer or self._default_materializer
        self._clock = clock or time.time
        self._metrics = LowerdirCacheMetrics()

    @property
    def metrics(self) -> LowerdirCacheMetrics:
        return self._metrics

    def get_or_create(
        self,
        manifest: Manifest,
        *,
        root_hash: str,
    ) -> SnapshotCacheLookup:
        self._cache_root.mkdir(parents=True, exist_ok=True)
        target_dir = self._snapshot_dir(
            manifest_version=manifest.version,
            root_hash=root_hash,
        )

        lookup_start = time.perf_counter()
        cached = self._read_cached_snapshot(
            target_dir=target_dir,
            manifest_version=manifest.version,
            root_hash=root_hash,
        )
        lookup_elapsed = time.perf_counter() - lookup_start
        if cached is not None:
            self._metrics = self._metrics.record_hit(lookup_s=lookup_elapsed)
            return SnapshotCacheLookup(
                snapshot=cached,
                cache_hit=True,
                timings={
                    "layer_stack.snapshot_cache.hit": 1.0,
                    "layer_stack.snapshot_cache.lookup_s": lookup_elapsed,
                    "layer_stack.snapshot_cache.bytes": float(cached.byte_count),
                },
            )

        materialize_start = time.perf_counter()
        snapshot = self._materialize(
            target_dir=target_dir,
            manifest=manifest,
            root_hash=root_hash,
        )
        materialize_elapsed = time.perf_counter() - materialize_start
        self._metrics = self._metrics.record_miss(
            lookup_s=lookup_elapsed,
            materialize_s=materialize_elapsed,
            byte_count=snapshot.byte_count,
        )
        return SnapshotCacheLookup(
            snapshot=snapshot,
            cache_hit=False,
            timings={
                "layer_stack.snapshot_cache.hit": 0.0,
                "layer_stack.snapshot_cache.lookup_s": lookup_elapsed,
                "layer_stack.snapshot_cache.materialize_s": materialize_elapsed,
                "layer_stack.snapshot_cache.bytes": float(snapshot.byte_count),
            },
        )

    def materialized_count(self) -> int:
        if not self._cache_root.is_dir():
            return 0
        return sum(
            1
            for child in self._cache_root.iterdir()
            if child.is_dir() and child.name != ".staging"
        )

    def collect_unpinned(self, pinned_lowerdirs: Sequence[str | Path]) -> tuple[str, ...]:
        if not self._cache_root.is_dir():
            return ()
        pinned_snapshot_dirs = {
            Path(lowerdir).resolve(strict=False).parent
            for lowerdir in pinned_lowerdirs
            if str(lowerdir)
        }

        removed: list[str] = []
        staging_root = self._cache_root / ".staging"
        if staging_root.is_dir():
            for child in sorted(staging_root.iterdir(), key=lambda item: item.name):
                _remove_path(child)
                removed.append(f".staging/{child.name}")

        for child in sorted(self._cache_root.iterdir(), key=lambda item: item.name):
            if child.name == ".staging":
                continue
            if child.resolve(strict=False) in pinned_snapshot_dirs:
                continue
            _remove_path(child)
            removed.append(child.name)
        return tuple(removed)

    def _read_cached_snapshot(
        self,
        *,
        target_dir: Path,
        manifest_version: int,
        root_hash: str,
    ) -> MaterializedSnapshot | None:
        lowerdir = target_dir / "lower"
        metadata = target_dir / SNAPSHOT_METADATA_FILE
        if not lowerdir.is_dir() or not metadata.is_file():
            return None
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            snapshot = MaterializedSnapshot.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return None
        if snapshot.manifest_version != manifest_version:
            return None
        if snapshot.root_hash != root_hash:
            return None
        if Path(snapshot.lowerdir).resolve(strict=False) != lowerdir.resolve(
            strict=False,
        ):
            return None
        return snapshot

    def _materialize(
        self,
        *,
        target_dir: Path,
        manifest: Manifest,
        root_hash: str,
    ) -> MaterializedSnapshot:
        _remove_path(target_dir)
        staging_root = self._cache_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = staging_root / f"{target_dir.name}-{uuid.uuid4().hex}"
        lowerdir = staging_dir / "lower"
        try:
            self._materializer(lowerdir, manifest)
            byte_count = _byte_count(lowerdir)
            final_lowerdir = target_dir / "lower"
            snapshot = MaterializedSnapshot(
                manifest_version=manifest.version,
                root_hash=root_hash,
                lowerdir=final_lowerdir.as_posix(),
                created_at=self._clock(),
                byte_count=byte_count,
            )
            (staging_dir / SNAPSHOT_METADATA_FILE).write_text(
                json.dumps(snapshot.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_dir, target_dir)
        except Exception:
            _remove_path(staging_dir)
            raise
        return snapshot

    def _default_materializer(self, lowerdir: Path, manifest: Manifest) -> None:
        MergedView(self._storage_root).materialize(lowerdir, manifest)

    def _snapshot_dir(self, *, manifest_version: int, root_hash: str) -> Path:
        safe_hash = _safe_hash(root_hash)
        return self._cache_root / f"manifest-{manifest_version:06d}-{safe_hash[:16]}"


def manifest_root_hash(manifest: Manifest) -> str:
    """Return a stable identity hash for the manifest's root view."""
    payload = {
        "layers": [layer.to_dict() for layer in manifest.layers],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_hash(root_hash: str) -> str:
    value = str(root_hash).strip().lower()
    if not value:
        raise ValueError("root_hash must not be empty")
    return "".join(char if char.isalnum() else "-" for char in value)


def _byte_count(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() or entry.is_symlink():
            total += entry.lstat().st_size
    return total


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = [
    "MATERIALIZED_DIR",
    "MaterializedSnapshot",
    "MaterializedSnapshotCache",
    "SNAPSHOT_METADATA_FILE",
    "SnapshotCacheLookup",
    "manifest_root_hash",
]
