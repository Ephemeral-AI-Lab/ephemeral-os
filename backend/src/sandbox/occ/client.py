"""Narrow OCC mutation client boundary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset import CommitOptions
from sandbox.occ.changeset import Change, ChangesetResult
from sandbox.occ.ports import WorkspaceBindingReader

if TYPE_CHECKING:
    from sandbox.occ.service import OccService


class OccClient:
    """Command-exec-facing client for submitting typed mutation changesets."""

    def __init__(
        self,
        service: OccService,
        *,
        binding_reader: WorkspaceBindingReader,
        workspace_ref: str,
    ) -> None:
        self._service = service
        self._binding_reader = binding_reader
        self._workspace_ref = workspace_ref

    def _require_binding(self, workspace_ref: str | None) -> None:
        ref = self._workspace_ref if workspace_ref is None else workspace_ref
        self._binding_reader.require_workspace_binding(ref)

    async def apply_changeset(
        self,
        typed_changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
        workspace_ref: str | None = None,
        run_maintenance: bool = True,
    ) -> ChangesetResult:
        self._require_binding(workspace_ref)
        return await self._service.apply_changeset(
            typed_changes,
            snapshot=snapshot,
            options=options,
            run_maintenance=run_maintenance,
        )

    async def run_maintenance_after_publish(
        self,
        result: ChangesetResult,
        *,
        workspace_ref: str | None = None,
    ) -> dict[str, float]:
        self._require_binding(workspace_ref)
        return await self._service.run_maintenance_after_publish(result)


__all__ = ["OccClient"]
