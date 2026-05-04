"""Host-side client for OCC changeset operations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sandbox.occ.changeset.prepared import ChangesetOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.service import OccService
from sandbox.occ.wire import change_to_dict, changeset_result_from_dict
from sandbox.providers.registry import get_adapter
from sandbox.runtime._server_dispatch import RuntimeDispatchError, call_runtime_server


class OCCClientError(RuntimeError):
    """Raised when the OCC runtime server returns a transport/error envelope."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class OCCClient:
    """Public OCC changeset client.

    Phase 03 callers can bind an :class:`OccService` and receive a
    :class:`PreparedChangeset`. Legacy callers still dispatch to the runtime
    ``occ.apply_changeset`` handler until the Phase 06 cutover removes the
    live-root apply path.
    """

    def __init__(
        self,
        sandbox_id: str | None = None,
        *,
        workspace_root: str = "/workspace",
        timeout: int = 300,
        service: OccService | None = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self.timeout = timeout
        self._service = service

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        agent_id: str = "",
        description: str = "",
        snapshot=None,
        options: ChangesetOptions | None = None,
    ) -> ChangesetResult | PreparedChangeset:
        """Apply or prepare a typed :class:`Change` batch through OCC."""
        if self._service is not None:
            opts = options or ChangesetOptions(
                caller_id=agent_id,
                description=description,
            )
            return await self._service.apply_changeset(
                changes,
                snapshot=snapshot,
                options=opts,
            )

        if self.sandbox_id is None:
            raise OCCClientError(
                "MissingClientTarget",
                "OCCClient requires either sandbox_id or service",
            )
        result = await self._call(
            "occ.apply_changeset",
            {
                "changes": [change_to_dict(c) for c in changes],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return changeset_result_from_dict(result)

    async def _call(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.sandbox_id is None:
            raise OCCClientError(
                "MissingSandboxId",
                "runtime OCC calls require sandbox_id",
            )
        try:
            return await call_runtime_server(
                exec_fn=get_adapter(self.sandbox_id).exec,
                sandbox_id=self.sandbox_id,
                op=op,
                args={"workspace_root": self.workspace_root, **args},
                timeout=self.timeout,
            )
        except RuntimeDispatchError as exc:
            raise OCCClientError(exc.kind, exc.message, exc.details) from exc


__all__ = ["OCCClient", "OCCClientError"]
