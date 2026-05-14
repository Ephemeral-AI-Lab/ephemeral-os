"""Sandbox API facade implementation."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from sandbox.api import discovery as discovery_module
from sandbox.api import lifecycle as lifecycle_module
from sandbox.api import preview_urls as preview_urls_module
from sandbox.api._impl import edit as edit_module
from sandbox.api._impl import raw_exec as raw_exec_module
from sandbox.api._impl import read as read_module
from sandbox.api._impl import shell as shell_module
from sandbox.api._impl import write as write_module
from sandbox.api.protocol import SandboxLifecycleAPI, SandboxTransport
from sandbox.api.transport import DaemonSandboxTransport
from sandbox.host.context_preparer import (
    context_preparer_for as default_context_preparer_for,
)
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

_DEFAULT_LIFECYCLE = SimpleNamespace(
    create_sandbox=lifecycle_module.create_sandbox,
    start_sandbox=lifecycle_module.start_sandbox,
    stop_sandbox=lifecycle_module.stop_sandbox,
    delete_sandbox=lifecycle_module.delete_sandbox,
    ensure_sandbox_running=lifecycle_module.ensure_sandbox_running,
    set_sandbox_labels=lifecycle_module.set_sandbox_labels,
    get_sandbox=discovery_module.get_sandbox,
    list_sandboxes=discovery_module.list_sandboxes,
    list_snapshots=discovery_module.list_snapshots,
    get_health=discovery_module.get_health,
    get_signed_preview_url=preview_urls_module.get_signed_preview_url,
    get_build_logs_url=preview_urls_module.get_build_logs_url,
)


class SandboxClient:
    """Injectable public call surface for sandbox lifecycle and tool verbs."""

    def __init__(
        self,
        *,
        audit_sink: AuditSink | None = None,
        transport: SandboxTransport | None = None,
        lifecycle: SandboxLifecycleAPI | None = None,
        context_preparer: Callable[[str], Any] = default_context_preparer_for,
    ) -> None:
        self._audit_sink = audit_sink
        self._transport = transport or DaemonSandboxTransport()
        self._lifecycle = lifecycle or cast(SandboxLifecycleAPI, _DEFAULT_LIFECYCLE)
        self._context_preparer = context_preparer

    def create_sandbox(
        self,
        *,
        name: str,
        snapshot: str | None = None,
        image: str | None = None,
        language: str = "python",
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._lifecycle.create_sandbox(
            name=name,
            snapshot=snapshot,
            image=image,
            language=language,
            env_vars=env_vars,
            labels=labels,
        )

    def start_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        return self._lifecycle.start_sandbox(sandbox_id)

    def stop_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        return self._lifecycle.stop_sandbox(sandbox_id)

    def delete_sandbox(self, sandbox_id: str) -> None:
        self._lifecycle.delete_sandbox(sandbox_id)

    def ensure_sandbox_running(self, sandbox_id: str) -> dict[str, Any]:
        return self._lifecycle.ensure_sandbox_running(sandbox_id)

    def set_sandbox_labels(
        self,
        sandbox_id: str,
        labels: dict[str, str],
    ) -> dict[str, Any]:
        return self._lifecycle.set_sandbox_labels(sandbox_id, labels)

    def get_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        return self._lifecycle.get_sandbox(sandbox_id)

    def list_sandboxes(self) -> list[dict[str, Any]]:
        return self._lifecycle.list_sandboxes()

    def list_snapshots(self) -> list[dict[str, Any]]:
        return self._lifecycle.list_snapshots()

    def get_health(self) -> dict[str, Any]:
        return self._lifecycle.get_health()

    def get_signed_preview_url(
        self,
        sandbox_id: str,
        port: int,
    ) -> dict[str, Any]:
        return self._lifecycle.get_signed_preview_url(sandbox_id, port)

    def get_build_logs_url(self, sandbox_id: str) -> str | None:
        return self._lifecycle.get_build_logs_url(sandbox_id)

    def context_preparer_for(self, sandbox_id: str) -> Any:
        return self._context_preparer(sandbox_id)

    async def shell(
        self,
        sandbox_id: str,
        request: ShellRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ShellResult:
        return await shell_module.shell(
            sandbox_id,
            request,
            audit_sink=self._select_audit_sink(audit_sink),
            transport=self._transport,
        )

    async def raw_exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        audit_sink: AuditSink | None = None,
    ) -> RawExecResult:
        return await raw_exec_module.raw_exec(
            sandbox_id,
            command,
            cwd=cwd,
            timeout=timeout,
            audit_sink=self._select_audit_sink(audit_sink),
        )

    async def read_file(
        self,
        sandbox_id: str,
        request: ReadFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ReadFileResult:
        return await read_module.read_file(
            sandbox_id,
            request,
            audit_sink=self._select_audit_sink(audit_sink),
            transport=self._transport,
        )

    async def write_file(
        self,
        sandbox_id: str,
        request: WriteFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> WriteFileResult:
        return await write_module.write_file(
            sandbox_id,
            request,
            audit_sink=self._select_audit_sink(audit_sink),
            transport=self._transport,
        )

    async def edit_file(
        self,
        sandbox_id: str,
        request: EditFileRequest,
        *,
        audit_sink: AuditSink | None = None,
    ) -> EditFileResult:
        return await edit_module.edit_file(
            sandbox_id,
            request,
            audit_sink=self._select_audit_sink(audit_sink),
            transport=self._transport,
        )

    def _select_audit_sink(
        self,
        audit_sink: AuditSink | None,
    ) -> AuditSink | None:
        return self._audit_sink if audit_sink is None else audit_sink


__all__ = ["SandboxClient"]
