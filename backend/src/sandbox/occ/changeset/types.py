"""Typed OCC changeset objects.

Phase 03 introduces source-tagged mutation intent objects for the layer-stack
OCC preparation path. The current runtime still has legacy direct/gated apply
coordinators until the cutover phase, so these values keep compatibility
properties such as ``base_existed`` and ``edits`` while exposing the new
``source`` and byte-oriented write contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from sandbox.occ.patching.patcher import SearchReplaceEdit

ChangeSource = Literal["api_write", "api_edit", "shell_capture"]


class UpperChangeLike(Protocol):
    """Duck-typed legacy overlay upperdir change carried into ``builders.py``."""

    rel: str
    kind: str
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


@dataclass(frozen=True, init=False)
class Change:
    """Base mutation intent entering OCC."""

    path: str
    source: ChangeSource

    def __init__(self, path: str, *, source: ChangeSource) -> None:
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, init=False)
class WriteChange(Change):
    """Whole-file write intent.

    ``final_content`` is bytes for the layer-stack path. Legacy callers may
    still pass a UTF-8 string and ``base_existed``; the compatibility properties
    are removed once Phase 04/06 replace the old live-root gate.
    """

    final_content: bytes
    base_hash: str | None
    create_only: bool

    def __init__(
        self,
        path: str,
        final_content: bytes | str,
        base_hash: str | None = None,
        create_only: bool = False,
        *,
        source: ChangeSource = "api_write",
        base_existed: bool | None = None,
    ) -> None:
        Change.__init__(self, path, source=source)
        if base_existed is not None:
            create_only = not base_existed
        payload = final_content if isinstance(final_content, bytes) else final_content.encode("utf-8")
        object.__setattr__(self, "final_content", payload)
        object.__setattr__(self, "base_hash", base_hash)
        object.__setattr__(self, "create_only", bool(create_only))

    @property
    def base_existed(self) -> bool:
        return not self.create_only

    @property
    def final_text(self) -> str:
        return self.final_content.decode("utf-8")

    def with_base_hash(self, base_hash: str | None) -> "WriteChange":
        return WriteChange(
            path=self.path,
            source=self.source,
            final_content=self.final_content,
            base_hash=base_hash,
            create_only=self.create_only,
        )


@dataclass(frozen=True, init=False)
class EditChange(Change):
    """Search/replace edit intent.

    The new public shape is one anchor per ``EditChange``. Legacy callers may
    still pass ``edits=(SearchReplaceEdit(...), ...)``; the ``edits`` property
    preserves the old coordinator contract.
    """

    old_text: str
    new_text: str
    expected_occurrences: int
    _edits: tuple[object, ...] = field(repr=False, compare=True)

    def __init__(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str | None = None,
        expected_occurrences: int = 1,
        *,
        source: ChangeSource = "api_edit",
        edits: tuple[object, ...] | None = None,
    ) -> None:
        Change.__init__(self, path, source=source)
        if edits is None:
            if old_text is None:
                raise ValueError("EditChange requires old_text")
            if new_text is None:
                raise ValueError("EditChange requires new_text")
            edits = (SearchReplaceEdit(old_text=old_text, new_text=new_text),)
        elif not edits:
            raise ValueError("EditChange requires at least one edit")

        first = edits[0]
        first_old = str(getattr(first, "old_text", ""))
        first_new = str(getattr(first, "new_text", ""))
        object.__setattr__(self, "old_text", first_old)
        object.__setattr__(self, "new_text", first_new)
        object.__setattr__(self, "expected_occurrences", int(expected_occurrences))
        object.__setattr__(self, "_edits", tuple(edits))

    @property
    def edits(self) -> tuple[object, ...]:
        return self._edits


@dataclass(frozen=True, init=False)
class DeleteChange(Change):
    """Delete intent pinned to a base hash when known."""

    base_hash: str | None

    def __init__(
        self,
        path: str,
        base_hash: str | None = None,
        *,
        source: ChangeSource = "api_write",
    ) -> None:
        Change.__init__(self, path, source=source)
        object.__setattr__(self, "base_hash", base_hash)

    def with_base_hash(self, base_hash: str | None) -> "DeleteChange":
        return DeleteChange(path=self.path, source=self.source, base_hash=base_hash)


GatedChange = WriteChange | EditChange | DeleteChange


@dataclass(frozen=True, init=False)
class SymlinkChange(Change):
    """Replace path with symlink to target."""

    target: str

    def __init__(
        self,
        path: str,
        target: str,
        *,
        source: ChangeSource = "shell_capture",
    ) -> None:
        Change.__init__(self, path, source=source)
        object.__setattr__(self, "target", str(target))


@dataclass(frozen=True, init=False)
class OpaqueDirChange(Change):
    """Prune children of path not in ``kept_children``."""

    kept_children: frozenset[str]

    def __init__(
        self,
        path: str,
        kept_children: frozenset[str],
        *,
        source: ChangeSource = "shell_capture",
    ) -> None:
        Change.__init__(self, path, source=source)
        object.__setattr__(self, "kept_children", frozenset(kept_children))


@dataclass(frozen=True, init=False)
class BinaryChange(Change):
    """Legacy binary write/delete carried until the live-root gate is removed."""

    final_bytes: bytes | None

    def __init__(
        self,
        path: str,
        final_bytes: bytes | None,
        *,
        source: ChangeSource = "shell_capture",
    ) -> None:
        Change.__init__(self, path, source=source)
        object.__setattr__(self, "final_bytes", final_bytes)


DirectChange = SymlinkChange | OpaqueDirChange | BinaryChange


class FileStatus(StrEnum):
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    ABORTED_VERSION = "aborted_version"
    ABORTED_OVERLAP = "aborted_overlap"
    DROPPED = "dropped"
    REJECTED = "rejected"
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
    published_manifest_version: int | None = None

    @property
    def success(self) -> bool:
        accepted = {FileStatus.ACCEPTED, FileStatus.COMMITTED, FileStatus.DROPPED}
        return all(f.status in accepted for f in self.files)


__all__ = [
    "BinaryChange",
    "Change",
    "ChangeSource",
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
