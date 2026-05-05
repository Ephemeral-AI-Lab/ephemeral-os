"""Apply snapshot overlay captures through OCC."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from sandbox.occ.changeset.builders import (
    build_shell_delete_change,
    build_shell_write_change,
)
from sandbox.occ.changeset.intent import CommitIntent, PreparedChangeset
from sandbox.occ.changeset.types import (
    Change,
    ChangesetResult,
    OpaqueDirChange,
    SymlinkChange,
)
from sandbox.occ.client import OCCClient
from sandbox.occ.service import OccService
from sandbox.overlay.capture.changes import OverlayPathChange
from sandbox.overlay.capture.types import OverlayCapture


def overlay_capture_to_occ_changes(capture: OverlayCapture) -> tuple[Change, ...]:
    """Convert policy-blind overlay path changes into source-tagged OCC changes."""
    return overlay_path_changes_to_occ_changes(capture.changes)


def overlay_path_changes_to_occ_changes(
    path_changes: Sequence[OverlayPathChange],
) -> tuple[Change, ...]:
    changes: list[Change] = []
    for path_change in path_changes:
        if path_change.kind == "write":
            if path_change.content_path is None:
                raise ValueError(
                    f"write overlay path change lacks content path: {path_change.path}"
                )
            changes.append(
                build_shell_write_change(
                    path=path_change.path,
                    final_content=Path(path_change.content_path).read_bytes(),
                )
            )
            continue
        if path_change.kind == "delete":
            changes.append(build_shell_delete_change(path=path_change.path))
            continue
        if path_change.kind == "symlink":
            if path_change.content_path is None:
                raise ValueError(
                    f"symlink overlay path change lacks content path: {path_change.path}"
                )
            changes.append(
                SymlinkChange(
                    path=path_change.path,
                    target=os.readlink(path_change.content_path),
                    source="shell_capture",
                )
            )
            continue
        if path_change.kind == "opaque_dir":
            changes.append(
                OpaqueDirChange(
                    path=path_change.path,
                    kept_children=frozenset(
                        _kept_children_for(path_change.path, path_changes)
                    ),
                    source="shell_capture",
                )
            )
            continue
    return tuple(changes)


async def apply_overlay_capture(
    capture: OverlayCapture,
    *,
    occ_client: OCCClient,
    agent_id: str = "",
    description: str = "",
) -> ChangesetResult:
    """Commit an overlay capture through OCC."""
    changes = overlay_capture_to_occ_changes(capture)
    if not changes:
        return ChangesetResult(
            files=(),
            timings=dict(capture.timings),
            published_manifest_version=None,
        )
    if capture.snapshot_manifest is None:
        raise ValueError("overlay capture is missing its leased manifest")

    result = await occ_client.apply_changeset(
        changes,
        agent_id=agent_id,
        description=description,
        snapshot=capture.snapshot_manifest,
    )
    if isinstance(result, PreparedChangeset):
        raise TypeError("shell capture OCC service returned an uncommitted changeset")
    return ChangesetResult(
        files=result.files,
        timings={**capture.timings, **result.timings},
        published_manifest_version=result.published_manifest_version,
    )


def apply_overlay_capture_sync(
    capture: OverlayCapture,
    *,
    occ_service: OccService,
    agent_id: str,
    description: str,
) -> ChangesetResult:
    """Synchronously commit an overlay capture through OCC."""
    changes = overlay_capture_to_occ_changes(capture)
    if not changes:
        return ChangesetResult(
            files=(),
            timings=dict(capture.timings),
            published_manifest_version=None,
        )
    if capture.snapshot_manifest is None:
        raise ValueError("overlay capture is missing its leased manifest")
    result = occ_service.apply_changeset_sync(
        changes,
        snapshot=capture.snapshot_manifest,
        options=CommitIntent(caller_id=agent_id, description=description),
    )
    if isinstance(result, PreparedChangeset):
        raise TypeError("shell capture OCC service returned an uncommitted changeset")
    return result


def _kept_children_for(
    rel: str,
    path_changes: Sequence[OverlayPathChange],
) -> set[str]:
    prefix = f"{rel}/" if rel else ""
    kept: set[str] = set()
    for item in path_changes:
        if item.path == rel or not item.path.startswith(prefix):
            continue
        rest = item.path[len(prefix) :]
        if rest:
            kept.add(rest.split("/", 1)[0])
    return kept


__all__ = [
    "apply_overlay_capture",
    "apply_overlay_capture_sync",
    "overlay_capture_to_occ_changes",
    "overlay_path_changes_to_occ_changes",
]
