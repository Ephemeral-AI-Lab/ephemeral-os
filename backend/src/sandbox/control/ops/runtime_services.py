"""Provider-backed sandbox runtime service helpers.

This module owns the host transport for operations whose state and guardrails
live inside the sandbox runtime bundle.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sandbox.api import (
    ConflictInfo,
    EditFileResult,
    ReadFileResult,
    SandboxCaller,
    SearchReplaceEdit,
    ShellResult,
    WriteFileResult,
)
from sandbox.control.daemon.bundle import BUNDLE_REMOTE_DIR, ensure_runtime_uploaded
from sandbox.control.daemon.command import _call_runtime_server
from sandbox.providers.registry import dispose_adapter, get_adapter

DEFAULT_LAYER_STACK_ROOT = f"{BUNDLE_REMOTE_DIR}/layer-stack"


@dataclass
class RemoteRuntimeServiceBinding:
    """Provider-backed runtime services with state stored inside the sandbox."""

    sandbox_id: str
    layer_stack_root: str
    _initialized: bool = False
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def caller(self, label: str) -> SandboxCaller:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label)
        return SandboxCaller(agent_id=f"sandbox-api-{safe or uuid.uuid4().hex}")

    async def shell(
        self,
        *,
        command: str,
        timeout: int | None,
        cwd: str,
        caller: SandboxCaller,
        description: str,
    ) -> ShellResult:
        await self.ensure_initialized()
        args: dict[str, object] = {
            "layer_stack_root": self.layer_stack_root,
            "command": command,
            "cwd": cwd,
            "timeout_seconds": timeout,
            "actor_id": caller.agent_id,
            "description": description,
        }
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.shell",
            args=args,
            timeout=(timeout or 60) + 30,
        )
        return _shell_result_from_payload(raw)

    async def write_file(
        self,
        *,
        path: str,
        content: str,
        caller: SandboxCaller,
        description: str,
        overwrite: bool = True,
    ) -> WriteFileResult:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.write_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
                "content": content,
                "actor_id": caller.agent_id,
                "description": description,
                "overwrite": overwrite,
            },
            timeout=60,
        )
        return _write_result_from_payload(raw)

    async def edit_file(
        self,
        *,
        path: str,
        edits: Sequence[SearchReplaceEdit],
        caller: SandboxCaller,
        description: str,
    ) -> EditFileResult:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.edit_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
                "edits": [
                    {"old_text": edit.old_text, "new_text": edit.new_text}
                    for edit in edits
                ],
                "actor_id": caller.agent_id,
                "description": description,
            },
            timeout=60,
        )
        return _edit_result_from_payload(raw)

    async def read_file(self, *, path: str, caller: SandboxCaller) -> ReadFileResult:
        del caller
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.read_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
            },
            timeout=60,
        )
        return ReadFileResult(
            success=bool(raw.get("success", False)),
            exists=bool(raw.get("exists", False)),
            content=str(raw.get("content", "")),
            encoding=str(raw.get("encoding", "utf-8")),
            timings=_timings(raw.get("timings")),
        )

    async def layer_metrics(self) -> dict[str, object]:
        await self.ensure_initialized()
        return await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.layer_metrics",
            args={
                "layer_stack_root": self.layer_stack_root,
            },
            timeout=60,
        )

    async def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> dict[str, object]:
        await self.ensure_initialized()
        return await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.prepare_workspace_snapshot",
            args={
                "layer_stack_root": self.layer_stack_root,
                "request_id": request_id,
            },
            timeout=60,
        )

    async def release_workspace_snapshot(self, *, lease_id: str) -> bool:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.release_workspace_snapshot",
            args={
                "layer_stack_root": self.layer_stack_root,
                "lease_id": lease_id,
            },
            timeout=60,
        )
        return bool(raw.get("released", False))

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await ensure_runtime_uploaded(self.sandbox_id)
            self._initialized = True

    def dispose(self) -> None:
        dispose_adapter(self.sandbox_id)


def create_remote_runtime_services(
    *,
    sandbox_id: str,
    layer_stack_root: str = DEFAULT_LAYER_STACK_ROOT,
) -> RemoteRuntimeServiceBinding:
    """Create a provider-backed runtime API binding for an existing sandbox."""
    return RemoteRuntimeServiceBinding(
        sandbox_id=sandbox_id,
        layer_stack_root=layer_stack_root,
    )


def _shell_result_from_payload(raw: dict[str, object]) -> ShellResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return ShellResult(
        success=bool(raw.get("success", False)),
        exit_code=_int(raw.get("exit_code"), default=1),
        stdout=str(raw.get("stdout", "")),
        stderr=str(raw.get("stderr", "")),
        changed_paths=_paths(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        warnings=_paths(raw.get("warnings")),
        timings=_timings(raw.get("timings")),
    )


def _write_result_from_payload(raw: dict[str, object]) -> WriteFileResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return WriteFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=_paths(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=_timings(raw.get("timings")),
    )


def _edit_result_from_payload(raw: dict[str, object]) -> EditFileResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return EditFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=_paths(raw.get("changed_paths")),
        applied_edits=_int(raw.get("applied_edits"), default=0),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=_timings(raw.get("timings")),
    )


def _conflict_from_payload(raw: object) -> ConflictInfo | None:
    if not isinstance(raw, dict):
        return None
    return ConflictInfo(
        reason=str(raw.get("reason", "")),
        conflict_file=(
            str(raw.get("conflict_file"))
            if raw.get("conflict_file") is not None
            else None
        ),
        message=str(raw.get("message", "")),
    )


def _paths(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(path) for path in raw if str(path or "").strip())


def _timings(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): float(value) for key, value in raw.items()}


def _int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


__all__ = [
    "DEFAULT_LAYER_STACK_ROOT",
    "RemoteRuntimeServiceBinding",
    "create_remote_runtime_services",
]
