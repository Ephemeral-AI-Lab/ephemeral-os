"""Host-side client for OCC runtime server operations."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from typing import Any

from sandbox.occ.changeset.types import ChangesetResult, UpperChangeLike
from sandbox.occ.types import (
    EditSpec,
    OperationResult,
    WriteSpec,
)
from sandbox.occ.wire import (
    changeset_result_from_dict,
    editspec_to_dict,
    normalize_edit_specs,
    normalize_write_specs,
    operation_result_from_dict,
    upper_change_to_dict,
    writespec_to_dict,
)
from sandbox.providers.registry import get_adapter
from sandbox.runtime.bundle import BUNDLE_REMOTE_DIR


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
    """Typed host route for OCC requests dispatched through runtime/server.py."""

    def __init__(
        self,
        sandbox_id: str,
        *,
        workspace_root: str = "/workspace",
        timeout: int = 300,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self.timeout = timeout

    async def write(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        result = await self._call(
            "occ.write",
            {
                "specs": [writespec_to_dict(s) for s in normalize_write_specs(specs)],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return operation_result_from_dict(result)

    async def edit(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        result = await self._call(
            "occ.edit",
            {
                "specs": [editspec_to_dict(s) for s in normalize_edit_specs(specs)],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return operation_result_from_dict(result)

    async def apply_changeset(
        self,
        upper_changes: Sequence[UpperChangeLike],
        *,
        agent_id: str = "",
        edit_type: str = "apply_changeset",
        description: str = "",
    ) -> ChangesetResult:
        result = await self._call(
            "occ.apply_changeset",
            {
                "upper_changes": [upper_change_to_dict(c) for c in upper_changes],
                "agent_id": agent_id,
                "edit_type": edit_type,
                "description": description,
            },
        )
        return changeset_result_from_dict(result)

    async def _call(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "op": op,
            "args": {
                "workspace_root": self.workspace_root,
                **args,
            },
        }
        raw_payload = json.dumps(payload, separators=(",", ":"))
        command = f"python3 -m sandbox.runtime.server {shlex.quote(raw_payload)}"
        result = await get_adapter(self.sandbox_id).exec(
            self.sandbox_id,
            command,
            cwd=BUNDLE_REMOTE_DIR,
            timeout=self.timeout,
        )
        try:
            response = _decode_response(result.stdout)
        except OCCClientError:
            if result.exit_code != 0:
                raise OCCClientError(
                    kind="RuntimeExecFailed",
                    message=result.stderr or result.stdout,
                    details={"exit_code": result.exit_code},
                ) from None
            raise
        if "error" in response:
            error = response.get("error") or {}
            raise OCCClientError(
                kind=str(error.get("kind") or "RuntimeError"),
                message=str(error.get("message") or ""),
                details=error.get("details")
                if isinstance(error.get("details"), dict)
                else {},
            )
        if result.exit_code != 0:
            raise OCCClientError(
                kind="RuntimeExecFailed",
                message=result.stderr or result.stdout,
                details={"exit_code": result.exit_code},
            )
        return response


def _decode_response(stdout: str) -> dict[str, Any]:
    try:
        decoded = json.loads((stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise OCCClientError(
            "BadRuntimeResponse",
            "OCC runtime returned invalid JSON",
            {"stdout": stdout},
        ) from exc
    if not isinstance(decoded, dict):
        raise OCCClientError(
            "BadRuntimeResponse",
            "OCC runtime returned a non-object JSON response",
            {"response": decoded},
        )
    return decoded


__all__ = ["OCCClient", "OCCClientError"]
