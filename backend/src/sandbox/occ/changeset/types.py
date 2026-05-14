"""Typed OCC changeset objects.

Source-tagged mutation intent objects for the layer-stack OCC path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Literal

ChangeSource = Literal["api_write", "api_edit", "overlay_capture"]


@dataclass(frozen=True)
class Change:
    """Base mutation intent entering OCC."""

    path: str
    source: ChangeSource = "api_write"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))


@dataclass(frozen=True)
class EagerWritePayload:
    """In-memory write payload."""

    content: bytes

    def read_bytes(self) -> bytes:
        return self.content

    @property
    def content_path(self) -> str | None:
        return None

    @property
    def precomputed_hash(self) -> str | None:
        return None


@dataclass(frozen=True)
class DiskWritePayload:
    """On-disk write payload with optional cached bytes."""

    path: str
    content_hash: str | None
    _cached_content: bytes | None = field(
        default=None,
        init=False,
        compare=False,
        repr=False,
    )

    def read_bytes(self) -> bytes:
        cached = self._cached_content
        if cached is not None:
            return cached
        content = Path(self.path).read_bytes()
        object.__setattr__(self, "_cached_content", content)
        return content

    @property
    def content_path(self) -> str | None:
        return self.path

    @property
    def precomputed_hash(self) -> str | None:
        return self.content_hash


WritePayload = EagerWritePayload | DiskWritePayload


@dataclass(frozen=True, init=False)
class WriteChange(Change):
    """Whole-file write intent.

    ``payload`` keeps transport details out of the mutation intent. Source
    adapters are responsible for translating host/API inputs into eager or
    disk-backed payloads before constructing this value object.
    """

    source: ChangeSource = "api_write"
    base_hash: str | None = None
    payload: WritePayload

    def __init__(
        self,
        *,
        path: str,
        payload: WritePayload,
        source: ChangeSource = "api_write",
        base_hash: str | None = None,
    ) -> None:
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "base_hash", base_hash)
        object.__setattr__(self, "payload", payload)

    @property
    def final_content(self) -> bytes:
        """Return the write payload as bytes, materialising lazily.

        Eager (api_write / api_edit) instances return their stored bytes
        immediately. Lazy (overlay_capture) instances cache the first
        ``content_path`` read so chained hash/stage consumers share it.
        """
        return self.payload.read_bytes()

    @property
    def content_path(self) -> str | None:
        return self.payload.content_path

    @property
    def precomputed_hash(self) -> str | None:
        return self.payload.precomputed_hash

    def with_base_hash(self, base_hash: str | None) -> WriteChange:
        return WriteChange(
            path=self.path,
            source=self.source,
            payload=self.payload,
            base_hash=base_hash,
        )


@dataclass(frozen=True)
class EditChange(Change):
    """Search/replace edit intent."""

    source: ChangeSource = "api_edit"
    old_text: str | None = None
    new_text: str | None = None
    expected_occurrences: int = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.old_text is None:
            raise ValueError("EditChange requires old_text")
        if self.new_text is None:
            raise ValueError("EditChange requires new_text")
        object.__setattr__(self, "old_text", str(self.old_text))
        object.__setattr__(self, "new_text", str(self.new_text))
        object.__setattr__(
            self,
            "expected_occurrences",
            int(self.expected_occurrences),
        )


@dataclass(frozen=True)
class DeleteChange(Change):
    """Delete intent pinned to a base hash when known."""

    base_hash: str | None = None

    def with_base_hash(self, base_hash: str | None) -> DeleteChange:
        return replace(self, base_hash=base_hash)


@dataclass(frozen=True)
class SymlinkChange(Change):
    """Replace path with symlink to target."""

    source: ChangeSource = "overlay_capture"
    target: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "target", str(self.target))


@dataclass(frozen=True)
class OpaqueDirChange(Change):
    """Prune children of path not in ``kept_children``."""

    source: ChangeSource = "overlay_capture"
    kept_children: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(
            self,
            "kept_children",
            _normalize_kept_children(self.kept_children),
        )


def _normalize_kept_children(values: frozenset[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        child = str(value).strip("/")
        if not child or "/" in child or child in {".", ".."}:
            raise ValueError(f"opaque dir kept child must be direct: {value!r}")
        normalized.add(child)
    return frozenset(normalized)


class FileStatus(str, Enum):
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    ABORTED_VERSION = "aborted_version"
    ABORTED_OVERLAP = "aborted_overlap"
    DROPPED = "dropped"
    REJECTED = "rejected"
    FAILED = "failed"


def is_published_status(status: FileStatus) -> bool:
    return status in {FileStatus.ACCEPTED, FileStatus.COMMITTED}


def is_success_status(status: FileStatus) -> bool:
    return status in {
        FileStatus.ACCEPTED,
        FileStatus.COMMITTED,
        FileStatus.DROPPED,
    }


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
        return all(is_success_status(f.status) for f in self.files)


# ---- builders (folded in from former changeset/builders.py per W5c) ------


def _eager_payload(content: bytes | str) -> EagerWritePayload:
    if isinstance(content, bytes):
        return EagerWritePayload(content=content)
    return EagerWritePayload(content=content.encode("utf-8"))


def build_api_write_change(
    *,
    path: str,
    final_content: bytes | str,
    base_hash: str | None = None,
) -> WriteChange:
    """Build a source-tagged write change from the host write API."""
    return WriteChange(
        path=path,
        source="api_write",
        payload=_eager_payload(final_content),
        base_hash=base_hash,
    )


def build_overlay_write_change(
    *,
    path: str,
    final_content: bytes | None = None,
    content_path: str | None = None,
    precomputed_hash: str | None = None,
) -> WriteChange:
    """Build an overlay-captured full-file write without a caller base hash.

    When ``content_path`` and ``precomputed_hash`` are supplied, the
    bytes stay on disk and the OCC stager streams them kernel-to-kernel.
    ``final_content`` is the bytes-based fallback for callers that
    don't have a content path on disk.
    """
    if final_content is None and content_path is None:
        raise ValueError("build_overlay_write_change needs final_content or content_path")
    payload: WritePayload
    if content_path is not None and final_content is None:
        payload = DiskWritePayload(
            path=str(content_path),
            content_hash=precomputed_hash,
        )
    else:
        if final_content is None:
            raise ValueError("build_overlay_write_change needs final_content or content_path")
        payload = _eager_payload(final_content)
    return WriteChange(
        path=path,
        source="overlay_capture",
        payload=payload,
        base_hash=None,
    )


def build_overlay_delete_change(
    *,
    path: str,
    base_hash: str | None = None,
) -> DeleteChange:
    """Build an overlay-captured delete whose base hash can be inferred later."""
    return DeleteChange(path=path, source="overlay_capture", base_hash=base_hash)


__all__ = [
    "Change",
    "ChangeSource",
    "ChangesetResult",
    "DeleteChange",
    "DiskWritePayload",
    "EditChange",
    "EagerWritePayload",
    "FileResult",
    "FileStatus",
    "OpaqueDirChange",
    "SymlinkChange",
    "WriteChange",
    "WritePayload",
    "build_api_write_change",
    "build_overlay_delete_change",
    "build_overlay_write_change",
    "is_published_status",
    "is_success_status",
]
