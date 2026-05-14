"""Typed public sandbox API contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from sandbox.models import (
    EditFileRequest,
    EditFileResult,
    RawExecResult,
    ReadFileRequest,
    ReadFileResult,
    ShellRequest,
    ShellResult,
    WriteFileRequest,
    WriteFileResult,
)

if TYPE_CHECKING:
    from audit.base import AuditSink


class SandboxTransport(Protocol):
    """Transport used by public tool verbs to call the sandbox runtime."""

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: Mapping[str, object],
        *,
        timeout: int,
    ) -> dict[str, Any]: ...


class SandboxLifecycleAPI(Protocol):
    """Synchronous lifecycle/discovery/control subset of the public API."""

    def create_sandbox(
        self,
        *,
        name: str,
        snapshot: str | None = None,
        image: str | None = None,
        language: str = "python",
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...
    def start_sandbox(self, sandbox_id: str) -> dict[str, Any]: ...
    def stop_sandbox(self, sandbox_id: str) -> dict[str, Any]: ...
    def delete_sandbox(self, sandbox_id: str) -> None: ...
    def ensure_sandbox_running(self, sandbox_id: str) -> dict[str, Any]: ...
    def set_sandbox_labels(
        self,
        sandbox_id: str,
        labels: dict[str, str],
    ) -> dict[str, Any]: ...
    def get_sandbox(self, sandbox_id: str) -> dict[str, Any]: ...
    def list_sandboxes(self) -> list[dict[str, Any]]: ...
    def list_snapshots(self) -> list[dict[str, Any]]: ...
    def get_health(self) -> dict[str, Any]: ...
    def get_signed_preview_url(
        self,
        sandbox_id: str,
        port: int,
    ) -> dict[str, Any]: ...
    def get_build_logs_url(self, sandbox_id: str) -> str | None: ...
    def context_preparer_for(self, sandbox_id: str) -> Any: ...


class SandboxToolAPI(Protocol):
    """Async agent-facing tool subset of the public API."""

    async def shell(
        self,
        sandbox_id: str,
        request: ShellRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ShellResult: ...
    async def raw_exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        audit_sink: AuditSink | None = None,
    ) -> RawExecResult: ...
    async def read_file(
        self,
        sandbox_id: str,
        request: ReadFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ReadFileResult: ...
    async def write_file(
        self,
        sandbox_id: str,
        request: WriteFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> WriteFileResult: ...
    async def edit_file(
        self,
        sandbox_id: str,
        request: EditFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> EditFileResult: ...


class SandboxAPI(SandboxLifecycleAPI, SandboxToolAPI, Protocol):
    """Complete public sandbox API contract."""


__all__ = [
    "SandboxAPI",
    "SandboxLifecycleAPI",
    "SandboxToolAPI",
    "SandboxTransport",
]
