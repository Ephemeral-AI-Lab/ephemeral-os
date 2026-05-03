"""Host-side client for overlay runtime server operations."""

from __future__ import annotations

import json
import shlex
from typing import Any

from sandbox.overlay.types import OverlayRunOutcome, ShellResult
from sandbox.overlay.wire import overlay_outcome_from_dict, shell_result_from_dict


class OverlayClientError(RuntimeError):
    """Raised when the overlay runtime server returns an error envelope."""

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


class OverlayClient:
    """Typed host route for overlay requests through ``runtime/server.py``."""

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

    async def run(
        self,
        command: str,
        *,
        timeout: int | None = None,
        stdin: str | None = None,
        description: str = "",
        agent_id: str = "",
    ) -> OverlayRunOutcome:
        result = await self._call(
            "overlay.run",
            {
                "command": command,
                "timeout": timeout,
                "stdin": stdin,
                "description": description,
                "agent_id": agent_id,
            },
        )
        return overlay_outcome_from_dict(result)

    async def shell(
        self,
        command: str,
        *,
        timeout: int | None = None,
        stdin: str | None = None,
        description: str = "",
        agent_id: str = "",
    ) -> ShellResult:
        result = await self._call(
            "shell",
            {
                "command": command,
                "timeout": timeout,
                "stdin": stdin,
                "description": description,
                "agent_id": agent_id,
            },
        )
        return shell_result_from_dict(result)

    async def _call(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "op": op,
            "args": {
                "workspace_root": self.workspace_root,
                "sandbox_id": self.sandbox_id,
                **{key: value for key, value in args.items() if value is not None},
            },
        }
        raw_payload = json.dumps(payload, separators=(",", ":"))
        command = f"python3 -m sandbox.runtime.server {shlex.quote(raw_payload)}"
        from sandbox.providers.registry import get_adapter
        from sandbox.runtime.bundle import BUNDLE_REMOTE_DIR

        result = await get_adapter(self.sandbox_id).exec(
            self.sandbox_id,
            command,
            cwd=BUNDLE_REMOTE_DIR,
            timeout=self.timeout,
        )
        try:
            response = _decode_response(result.stdout)
        except OverlayClientError:
            if result.exit_code != 0:
                raise OverlayClientError(
                    kind="RuntimeExecFailed",
                    message=result.stderr or result.stdout,
                    details={"exit_code": result.exit_code},
                ) from None
            raise
        if "error" in response:
            error = response.get("error") or {}
            raise OverlayClientError(
                kind=str(error.get("kind") or "RuntimeError"),
                message=str(error.get("message") or ""),
                details=error.get("details")
                if isinstance(error.get("details"), dict)
                else {},
            )
        if result.exit_code != 0:
            raise OverlayClientError(
                kind="RuntimeExecFailed",
                message=result.stderr or result.stdout,
                details={"exit_code": result.exit_code},
            )
        return response


def _decode_response(stdout: str) -> dict[str, Any]:
    try:
        decoded = json.loads((stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise OverlayClientError(
            "BadRuntimeResponse",
            "overlay runtime returned invalid JSON",
            {"stdout": stdout},
        ) from exc
    if not isinstance(decoded, dict):
        raise OverlayClientError(
            "BadRuntimeResponse",
            "overlay runtime returned a non-object JSON response",
            {"response": decoded},
        )
    return decoded


__all__ = ["OverlayClient", "OverlayClientError"]
