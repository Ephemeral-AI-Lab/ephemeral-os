"""Runtime helpers and typed ``occ.apply_changeset`` dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.content.hashing import ContentHasher
from sandbox.occ.ports import SnapshotReader


class ApplyChangesetService(Protocol):
    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> ChangesetResult | PreparedChangeset: ...


def content_hash_bytes(content: bytes) -> str:
    """Return the layer-stack OCC hash for file bytes."""
    return ContentHasher().hash_bytes(content)


async def apply_changeset_op(
    service: ApplyChangesetService,
    changes: Sequence[Change],
    *,
    snapshot: Manifest | None = None,
    options: CommitOptions | None = None,
) -> ChangesetResult | PreparedChangeset:
    """Dispatch a typed OCC apply operation to the configured service."""
    return await service.apply_changeset(
        changes,
        snapshot=snapshot,
        options=options,
    )


def infer_manifest_base_hash(
    *,
    snapshot_reader: SnapshotReader,
    manifest: Manifest,
    path: str,
) -> str | None:
    """Hash *path* content as it existed in a leased manifest."""
    content, exists = snapshot_reader.read_bytes(path, manifest)
    if not exists or content is None:
        return None
    return content_hash_bytes(content)


__all__ = [
    "ApplyChangesetService",
    "apply_changeset_op",
    "content_hash_bytes",
    "infer_manifest_base_hash",
]
