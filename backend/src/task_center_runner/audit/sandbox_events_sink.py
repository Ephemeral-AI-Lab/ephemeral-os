"""Rotating + gzipping writer for ``sandbox_events.jsonl``.

Per [README §Disk & log persistence contract], the daemon never writes audit
to disk — this host-side sink owns the canonical artifact. Rotation at
64 MiB, gzip on rotation, ``EOS_AUDIT_ARTIFACT_RETENTION_FILES`` historical
files kept (default 8). Files live under the EOS_TIER_RUN_ID-stable path.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROTATION_BYTES_DEFAULT = 64 * 1024 * 1024
RETENTION_FILES_ENV = "EOS_AUDIT_ARTIFACT_RETENTION_FILES"
RETENTION_FILES_DEFAULT = 8


def _retention_files() -> int:
    raw = os.environ.get(RETENTION_FILES_ENV)
    if not raw:
        return RETENTION_FILES_DEFAULT
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return RETENTION_FILES_DEFAULT


class RotatingJsonlSink:
    """Append-only JSONL sink with size-based rotation and gzip compression.

    Thread-safe: a single lock guards write + rotation. Rotation is synchronous
    (the gzip happens on the calling thread); the previous design's background
    thread+queue is unnecessary at our event rate and would complicate teardown.
    The 64 MiB roll is rare and gzip of a 64 MiB JSONL completes in well under
    a second on the runner host.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        rotation_bytes: int = ROTATION_BYTES_DEFAULT,
        retention_files: int | None = None,
    ) -> None:
        self._path = Path(path)
        self._rotation_bytes = rotation_bytes
        self._retention_files = retention_files if retention_files is not None else _retention_files()
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def live_path(self) -> Path:
        return self._path

    def append_event(self, event: Mapping[str, Any]) -> None:
        encoded = (
            json.dumps(event, default=_json_default, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        with self._lock:
            self._maybe_rotate_locked(extra=len(encoded))
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)

    def append_many(self, events: Iterable[Mapping[str, Any]]) -> None:
        for event in events:
            self.append_event(event)

    # ------------------------------------------------------------------

    def _maybe_rotate_locked(self, *, extra: int) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size + extra <= self._rotation_bytes:
            return
        self._rotate_locked()

    def _rotate_locked(self) -> None:
        next_index = self._next_rotation_index()
        rotated_plain = self._path.with_suffix(
            self._path.suffix + f".{next_index}"
        )
        rotated_gz = rotated_plain.with_suffix(rotated_plain.suffix + ".gz")
        try:
            os.replace(self._path, rotated_plain)
        except FileNotFoundError:
            return
        try:
            with open(rotated_plain, "rb") as src, gzip.open(rotated_gz, "wb") as dst:
                shutil.copyfileobj(src, dst)
            rotated_plain.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — rotation failures must not break ingest
            logger.warning("sandbox_events rotation gzip failed", exc_info=True)
        self._enforce_retention_locked()

    def _next_rotation_index(self) -> int:
        existing = list(self._existing_rotations())
        max_n = 0
        for path in existing:
            n = _parse_rotation_index(path.name, self._path.name)
            if n is not None and n > max_n:
                max_n = n
        return max_n + 1

    def _existing_rotations(self) -> Iterable[Path]:
        parent = self._path.parent
        if not parent.is_dir():
            return ()
        prefix = self._path.name + "."
        return sorted(
            p for p in parent.iterdir() if p.name.startswith(prefix) and p.name.endswith(".gz")
        )

    def _enforce_retention_locked(self) -> None:
        existing = list(self._existing_rotations())
        if len(existing) <= self._retention_files:
            return
        ordered = sorted(
            existing,
            key=lambda p: _parse_rotation_index(p.name, self._path.name) or 0,
        )
        surplus = len(ordered) - self._retention_files
        for path in ordered[:surplus]:
            try:
                path.unlink()
            except OSError:
                logger.warning("failed to evict rotated audit log %s", path, exc_info=True)


def _parse_rotation_index(name: str, base_name: str) -> int | None:
    prefix = base_name + "."
    if not name.startswith(prefix) or not name.endswith(".gz"):
        return None
    middle = name[len(prefix) : -len(".gz")]
    try:
        return int(middle)
    except ValueError:
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def iter_rotated_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Yield events from ``path`` plus any rotated ``.<N>.gz`` history.

    Ascending N order followed by the live file (newest events last).
    Compatible with ``performance_report._iter_jsonl`` semantics.
    """
    base = Path(path)
    parent = base.parent
    if parent.is_dir():
        rotated = []
        for child in parent.iterdir():
            idx = _parse_rotation_index(child.name, base.name)
            if idx is not None:
                rotated.append((idx, child))
        rotated.sort(key=lambda item: item[0])
        for _, child in rotated:
            yield from _read_gz_jsonl(child)
    if base.exists():
        yield from _read_text_jsonl(base)


def _read_text_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _read_gz_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        logger.warning("failed to read rotated audit log %s", path, exc_info=True)


__all__ = [
    "RETENTION_FILES_DEFAULT",
    "ROTATION_BYTES_DEFAULT",
    "RotatingJsonlSink",
    "iter_rotated_jsonl",
]
