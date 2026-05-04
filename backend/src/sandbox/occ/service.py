"""OCC changeset preparation service."""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.occ.changeset.prepared import ChangesetOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change
from sandbox.occ.routing.gitignore import GitignoreOracle
from sandbox.occ.routing.router import ChangeRouter
from sandbox.occ.runtime_ops import infer_manifest_base_hash


class OccService:
    """Prepare typed OCC changesets before the Phase 04 commit transaction."""

    def __init__(
        self,
        *,
        gitignore: GitignoreOracle,
        layer_stack: LayerStackManager | None = None,
    ) -> None:
        self._layer_stack = layer_stack
        self._router = ChangeRouter(gitignore)

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: ChangesetOptions | None = None,
    ) -> PreparedChangeset:
        """Prepare a changeset for a later commit transaction."""
        opts = options or ChangesetOptions()
        base_hash_reader = None
        if snapshot is not None and self._layer_stack is not None:
            layer_stack = self._layer_stack

            def base_hash_reader(path: str) -> str | None:
                return infer_manifest_base_hash(
                    layer_stack=layer_stack,
                    manifest=snapshot,
                    path=path,
                )

        return await self._router.prepare(
            changes,
            snapshot=snapshot,
            options=opts,
            base_hash_reader=base_hash_reader,
        )


__all__ = ["OccService"]
