"""Shared types for the daemon-owned ephemeral workspace pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox._shared.shell_contract import SnapshotManifest


@dataclass(frozen=True)
class _OverlaySnapshot:
    lease_id: str
    manifest: SnapshotManifest
    layer_paths: tuple[Path, ...]


__all__ = [
    "_OverlaySnapshot",
]
