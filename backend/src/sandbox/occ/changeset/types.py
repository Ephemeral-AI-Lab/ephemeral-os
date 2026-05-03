"""Types for the OCC search/replace gate.

See ``.omc/plans/occ-changeset-gate-simplification.md`` §Data types. The gate's
conflict primitive is a search/replace anchor on text content; the hash CAS on
``WriteChange``/``DeleteChange`` is the version pin for whole-file replaces and
deletes. ``EditChange`` carries no ``base_hash`` by design — ``old_text in
current`` is the conflict signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sandbox.occ.patching.patcher import SearchReplaceEdit


class UpperChangeLike(Protocol):
    """Duck-typed overlay upperdir change carried into ``builders.py``."""

    rel: str
    kind: str
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


# --- Gated changes: routed by gitignore, conflict-checked, may abort ---


@dataclass(frozen=True)
class WriteChange:
    """Whole-file replace. Strict CAS by hash."""

    path: str
    base_hash: str
    base_existed: bool
    final_content: str


@dataclass(frozen=True)
class EditChange:
    """Search/replace. Conflict iff any ``old_text`` is not exactly-once in ``current``."""

    path: str
    edits: tuple[SearchReplaceEdit, ...]


@dataclass(frozen=True)
class DeleteChange:
    """Strict CAS by hash."""

    path: str
    base_hash: str


GatedChange = WriteChange | EditChange | DeleteChange


# --- Direct-only changes: always routed to DirectMergeCoordinator regardless
# --- of gitignore. Last-writer-wins; no conflict detection.


@dataclass(frozen=True)
class SymlinkChange:
    """Replace path with symlink to target."""

    path: str
    target: str


@dataclass(frozen=True)
class OpaqueDirChange:
    """Prune children of path not in ``kept_children`` (overlay opaque dir)."""

    path: str
    kept_children: frozenset[str]


@dataclass(frozen=True)
class BinaryChange:
    """Non-UTF-8 regular file write (or delete if ``final_bytes is None``).

    The gate's search/replace primitive only operates on text; binary content
    is direct-merged regardless of gitignore. The orchestrator never gates a
    ``BinaryChange``.
    """

    path: str
    final_bytes: bytes | None


DirectChange = SymlinkChange | OpaqueDirChange | BinaryChange


Change = GatedChange | DirectChange


class FileStatus(StrEnum):
    COMMITTED = "committed"
    ABORTED_VERSION = "aborted_version"
    ABORTED_OVERLAP = "aborted_overlap"
    FAILED = "failed"


@dataclass(frozen=True)
class FileResult:
    path: str
    status: FileStatus
    message: str = ""
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangesetResult:
    files: tuple[FileResult, ...]
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return all(f.status is FileStatus.COMMITTED for f in self.files)


__all__ = [
    "BinaryChange",
    "Change",
    "ChangesetResult",
    "DeleteChange",
    "DirectChange",
    "EditChange",
    "FileResult",
    "FileStatus",
    "GatedChange",
    "OpaqueDirChange",
    "SymlinkChange",
    "UpperChangeLike",
    "WriteChange",
]
