"""Typed OCC changeset objects.

Source-tagged mutation intent objects for the layer-stack OCC path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.content_hashing import ContentHasher


class ChangeSource(str, Enum):
    API_WRITE = "api_write"
    API_EDIT = "api_edit"
    OVERLAY_CAPTURE = "overlay_capture"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Change:
    """Base mutation intent entering OCC."""

    path: str
    source: ChangeSource = ChangeSource.API_WRITE

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))
        object.__setattr__(self, "source", ChangeSource(self.source))


@dataclass(frozen=True)
class WritePayload:
    """Write payload: eager bytes, an on-disk path, or both.

    At least one of ``content``/``content_path`` must be set; ``read_bytes``
    prefers the in-memory bytes and falls back to a disk read. Callers that
    need to avoid re-reads should cache the bytes themselves.
    """

    content: bytes | None = None
    content_path: str | None = None
    precomputed_hash: str | None = None

    def read_bytes(self) -> bytes:
        if self.content is not None:
            return self.content
        if self.content_path is None:
            raise ValueError("WritePayload requires content or content_path")
        return Path(self.content_path).read_bytes()


@dataclass(frozen=True, kw_only=True)
class WriteChange(Change):
    """Whole-file write intent.

    ``payload`` keeps transport details out of the mutation intent. Source
    adapters translate host/API inputs into in-memory or disk-backed payloads
    before constructing this value object.
    """

    payload: WritePayload
    base_hash: str | None = None

    @property
    def final_content(self) -> bytes:
        return self.payload.read_bytes()

    @property
    def content_path(self) -> str | None:
        return self.payload.content_path

    @property
    def precomputed_hash(self) -> str | None:
        return self.payload.precomputed_hash

    def with_base_hash(self, base_hash: str | None) -> WriteChange:
        return replace(self, base_hash=base_hash)


@dataclass(frozen=True)
class EditChange(Change):
    """Search/replace edit intent."""

    source: ChangeSource = ChangeSource.API_EDIT
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
        object.__setattr__(self, "expected_occurrences", int(self.expected_occurrences))


@dataclass(frozen=True)
class DeleteChange(Change):
    """Delete intent pinned to a base hash when known.

    Inherits ``source = ChangeSource.API_WRITE`` from :class:`Change`. There
    is no separate ``ChangeSource.API_DELETE`` value: deletes flow through
    the same routing branch as writes
    (see ``changeset_preparation._requires_base_hash``), so the API_WRITE
    label covers both write and delete intent.
    """

    base_hash: str | None = None

    def with_base_hash(self, base_hash: str | None) -> DeleteChange:
        return replace(self, base_hash=base_hash)


@dataclass(frozen=True)
class SymlinkChange(Change):
    """Replace path with symlink to target."""

    source: ChangeSource = ChangeSource.OVERLAY_CAPTURE
    target: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "target", str(self.target))


@dataclass(frozen=True)
class OpaqueDirChange(Change):
    """Prune lower-layer children of a directory."""

    source: ChangeSource = ChangeSource.OVERLAY_CAPTURE


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


# ---- prepared changesets ---------------------------------------------------


class RouteDecision(str, Enum):
    GATED = "gated"
    DIRECT = "direct"
    DROP = "drop"
    REJECT = "reject"


@dataclass(frozen=True)
class PreparedPathGroup:
    """Ordered changes for one normalized path and route decision."""

    path: str
    route: RouteDecision
    changes: tuple[Change, ...]
    message: str | None = None


@dataclass(frozen=True)
class CommitOptions:
    """Request-level OCC commit options.

    ``atomic`` defaults to ``True``: a multi-path changeset is published only
    if every path validates. If any path fails (ABORTED_OVERLAP,
    ABORTED_VERSION, FAILED, or REJECTED), no path lands. Callers that want
    best-effort partial publish must opt out explicitly with
    ``atomic=False``.
    """

    atomic: bool = True


@dataclass(frozen=True)
class PreparedChangeset:
    """Routed changeset consumed by the commit transaction.

    ``changeset_id`` is a stable content-hash derived from
    ``(snapshot.version, atomic flag, path_groups)`` — see
    :func:`compute_changeset_id`. Stable across replays of identical inputs
    so the OCC half of the V3 causal chain (Principle 3) survives. The
    constructor in :mod:`sandbox.occ.changeset_preparation` is the only
    site that populates this; downstream readers consume it as-is.
    """

    snapshot: Manifest | None
    path_groups: tuple[PreparedPathGroup, ...]
    atomic: bool
    timings: dict[str, float] = field(default_factory=dict)
    changeset_id: str = ""


# ---- builders ------


def build_api_write_change(
    *,
    path: str,
    final_content: bytes | str,
    base_hash: str | None = None,
) -> WriteChange:
    """Build a source-tagged write change from the host write API."""
    return WriteChange(
        path=path,
        source=ChangeSource.API_WRITE,
        payload=WritePayload(
            content=final_content if isinstance(final_content, bytes) else final_content.encode("utf-8")
        ),
        base_hash=base_hash,
    )


def build_overlay_write_change(
    *,
    path: str,
    final_content: bytes | None = None,
    content_path: str | None = None,
    precomputed_hash: str | None = None,
    source: ChangeSource = ChangeSource.OVERLAY_CAPTURE,
) -> WriteChange:
    """Build an overlay-captured full-file write without a caller base hash.

    When ``content_path`` is provided and ``final_content`` is None, the
    bytes stay on disk and the OCC stager streams them kernel-to-kernel.
    ``final_content`` is the bytes-based fallback for callers that don't
    have a content path on disk.
    """
    if content_path is not None and final_content is None:
        payload = WritePayload(
            content_path=str(content_path),
            precomputed_hash=precomputed_hash,
        )
    elif final_content is not None:
        payload = WritePayload(
            content=final_content if isinstance(final_content, bytes) else final_content.encode("utf-8")
        )
    else:
        raise ValueError("build_overlay_write_change needs final_content or content_path")
    return WriteChange(
        path=path,
        source=source,
        payload=payload,
        base_hash=None,
    )


def build_overlay_delete_change(
    *,
    path: str,
    base_hash: str | None = None,
    source: ChangeSource = ChangeSource.OVERLAY_CAPTURE,
) -> DeleteChange:
    """Build an overlay-captured delete whose base hash can be inferred later."""
    return DeleteChange(path=path, source=source, base_hash=base_hash)


def _change_signature(change: Change) -> dict[str, str | int | None]:
    """Return a stable dict signature for one change (no Python object reprs).

    Avoids :func:`repr` on dataclass tuples — dict ordering and identity
    reprs sneak in across processes; an explicit per-field encoding keeps
    the signature deterministic.
    """
    sig: dict[str, str | int | None] = {
        "kind": type(change).__name__,
        "path": change.path,
        "source": change.source.value,
    }
    if isinstance(change, WriteChange):
        sig["base_hash"] = change.base_hash
        sig["precomputed_hash"] = change.precomputed_hash
        # When content lives only on disk and no hash is precomputed, hash the
        # bytes lazily so the id reflects content (the replay-stability claim).
        if change.precomputed_hash is None:
            try:
                content_hash = ContentHasher().hash_bytes(change.final_content)
            except (OSError, ValueError):
                content_hash = ""
            sig["content_hash"] = content_hash
    elif isinstance(change, DeleteChange):
        sig["base_hash"] = change.base_hash
    elif isinstance(change, EditChange):
        sig["old_text"] = change.old_text
        sig["new_text"] = change.new_text
        sig["expected_occurrences"] = change.expected_occurrences
    elif isinstance(change, SymlinkChange):
        sig["target"] = change.target
    return sig


def compute_changeset_id(
    *,
    snapshot: Manifest | None,
    path_groups: tuple[PreparedPathGroup, ...],
    atomic: bool,
) -> str:
    """Derive a stable 16-hex-char changeset id for replay matching.

    Determinism contract:

    * Same ``(snapshot.version, atomic, path_groups)`` inputs MUST produce
      the same id across processes — enforced by
      ``test_prepared_changeset_id_is_stable_across_replay``.
    * Distinct inputs (different paths, different content, different
      route decisions) MUST produce distinct ids with high probability —
      sha256 collision domain.
    """
    canonical: dict[str, object] = {
        "snapshot_version": snapshot.version if snapshot is not None else None,
        "atomic": bool(atomic),
        "path_groups": [
            {
                "path": pg.path,
                "route": pg.route.value,
                "changes": [_change_signature(ch) for ch in pg.changes],
            }
            for pg in path_groups
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def drop_or_reject_file_result(group: PreparedPathGroup) -> FileResult | None:
    """Render the standard DROP/REJECT FileResult for a prepared path group.

    Returns None when the route is neither DROP nor REJECT. Centralizes the
    message-fallback ("change dropped" / "change rejected") used in both
    commit-time validation and CAS-exhaustion fallback.
    """
    if group.route is RouteDecision.DROP:
        return FileResult(
            path=group.path,
            status=FileStatus.DROPPED,
            message=group.message or "change dropped",
        )
    if group.route is RouteDecision.REJECT:
        return FileResult(
            path=group.path,
            status=FileStatus.REJECTED,
            message=group.message or "change rejected",
        )
    return None


__all__ = [
    "Change",
    "ChangeSource",
    "ChangesetResult",
    "CommitOptions",
    "DeleteChange",
    "EditChange",
    "FileResult",
    "FileStatus",
    "OpaqueDirChange",
    "PreparedChangeset",
    "PreparedPathGroup",
    "RouteDecision",
    "SymlinkChange",
    "WriteChange",
    "WritePayload",
    "build_api_write_change",
    "build_overlay_delete_change",
    "build_overlay_write_change",
    "compute_changeset_id",
    "drop_or_reject_file_result",
    "is_published_status",
    "is_success_status",
]
