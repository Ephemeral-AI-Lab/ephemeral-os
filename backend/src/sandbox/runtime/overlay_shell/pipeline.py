"""Project overlay shell captures through the typed OCC commit path."""

from __future__ import annotations

from pathlib import Path

from sandbox.occ.changeset.intent import PreparedChangeset
from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.client import OCCClient
from sandbox.runtime.overlay_shell.capture_to_changeset import capture_to_changeset
from sandbox.runtime.overlay_shell.result_envelope import RuntimeResultEnvelope


async def apply_captured_changes(
    envelope: RuntimeResultEnvelope,
    *,
    occ_client: OCCClient,
    agent_id: str = "",
    description: str = "",
) -> ChangesetResult:
    """Convert upperdir capture to typed OCC changes and commit them."""
    changes = capture_to_changeset(envelope.upper_changes)
    if not changes:
        return ChangesetResult(files=(), published_manifest_version=None)
    if envelope.snapshot_manifest is None:
        raise ValueError("overlay shell envelope is missing its leased manifest")

    result = await occ_client.apply_changeset(
        changes,
        agent_id=agent_id,
        description=description,
        snapshot=envelope.snapshot_manifest,
    )
    if isinstance(result, PreparedChangeset):
        raise TypeError("shell capture OCC service returned an uncommitted changeset")
    return result


def read_output_ref(path: str) -> str:
    """Read a runtime stdout/stderr reference as display text."""
    return Path(path).read_bytes().decode("utf-8", "replace")


__all__ = ["apply_captured_changes", "read_output_ref"]
