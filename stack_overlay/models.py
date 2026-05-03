"""Shared model types for the experimental overlay stack."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Manifest:
    """Immutable layer manifest.

    ``layers`` are ordered newest to oldest, matching the current design doc.
    The mount helper can render them as relative ``lowerdir`` entries to avoid
    long option strings in Daytona.
    """

    version: int
    layers: tuple[str, ...]

    @property
    def depth(self) -> int:
        return len(self.layers)


@dataclass(frozen=True)
class Lease:
    """A held manifest snapshot."""

    lease_id: str
    manifest: Manifest


ChangeKind = Literal["write", "delete"]


@dataclass(frozen=True)
class LayerChange:
    """Accepted filesystem mutation written into a fresh layer."""

    path: str
    kind: ChangeKind
    content: str = ""


@dataclass(frozen=True)
class WriteChange:
    """OCC-gated write.

    ``base_existed=False`` is create-only. ``base_existed=True`` with a
    non-empty ``base_hash`` is a pinned modify. ``base_hash=""`` is intentionally
    a blind overwrite, matching the current API write path contract.
    """

    path: str
    final_content: str
    base_existed: bool
    base_hash: str = ""


@dataclass(frozen=True)
class DeleteChange:
    """OCC-gated delete."""

    path: str
    base_hash: str


class ChangeStatus(str, Enum):
    COMMITTED = "committed"
    ABORTED_VERSION = "aborted_version"
    FAILED = "failed"


@dataclass(frozen=True)
class FileResult:
    path: str
    status: ChangeStatus
    message: str = ""


@dataclass(frozen=True)
class CommitResult:
    manifest: Manifest
    files: tuple[FileResult, ...]
    committed_layer: str | None = None

    @property
    def success(self) -> bool:
        return all(item.status is ChangeStatus.COMMITTED for item in self.files)

    @property
    def committed_paths(self) -> tuple[str, ...]:
        return tuple(
            item.path for item in self.files if item.status is ChangeStatus.COMMITTED
        )


def normalize_rel_path(path: str | Path) -> str:
    rel = Path(path)
    if rel.is_absolute():
        raise ValueError(f"path must be relative: {path}")
    normalized = rel.as_posix().strip("/")
    if not normalized or normalized == ".":
        raise ValueError("path must not be empty")
    parts = Path(normalized).parts
    if ".." in parts:
        raise ValueError(f"path must stay inside workspace: {path}")
    return normalized
