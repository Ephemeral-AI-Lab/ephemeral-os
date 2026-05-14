"""Package-level default sandbox API client wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sandbox.api.facade import SandboxClient
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
    from sandbox.api.protocol import SandboxLifecycleAPI, SandboxTransport

_default_client = SandboxClient()


def default_client() -> SandboxClient:
    return _default_client


def set_default_client(client: SandboxClient) -> None:
    global _default_client
    _default_client = client


def configure_default_client(
    *,
    audit_sink: AuditSink | None = None,
    transport: SandboxTransport | None = None,
    lifecycle: SandboxLifecycleAPI | None = None,
) -> SandboxClient:
    client = SandboxClient(
        audit_sink=audit_sink,
        transport=transport,
        lifecycle=lifecycle,
    )
    set_default_client(client)
    return client


def create_sandbox(
    *,
    name: str,
    snapshot: str | None = None,
    image: str | None = None,
    language: str = "python",
    env_vars: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    return default_client().create_sandbox(
        name=name,
        snapshot=snapshot,
        image=image,
        language=language,
        env_vars=env_vars,
        labels=labels,
    )


def start_sandbox(sandbox_id: str) -> dict[str, Any]:
    return default_client().start_sandbox(sandbox_id)


def stop_sandbox(sandbox_id: str) -> dict[str, Any]:
    return default_client().stop_sandbox(sandbox_id)


def delete_sandbox(sandbox_id: str) -> None:
    default_client().delete_sandbox(sandbox_id)


def ensure_sandbox_running(sandbox_id: str) -> dict[str, Any]:
    return default_client().ensure_sandbox_running(sandbox_id)


def set_sandbox_labels(sandbox_id: str, labels: dict[str, str]) -> dict[str, Any]:
    return default_client().set_sandbox_labels(sandbox_id, labels)


def get_sandbox(sandbox_id: str) -> dict[str, Any]:
    return default_client().get_sandbox(sandbox_id)


def list_sandboxes() -> list[dict[str, Any]]:
    return default_client().list_sandboxes()


def list_snapshots() -> list[dict[str, Any]]:
    return default_client().list_snapshots()


def get_health() -> dict[str, Any]:
    return default_client().get_health()


def get_signed_preview_url(sandbox_id: str, port: int) -> dict[str, Any]:
    return default_client().get_signed_preview_url(sandbox_id, port)


def get_build_logs_url(sandbox_id: str) -> str | None:
    return default_client().get_build_logs_url(sandbox_id)


def context_preparer_for(sandbox_id: str) -> Any:
    return default_client().context_preparer_for(sandbox_id)


async def shell(
    sandbox_id: str,
    request: ShellRequest,
    *,
    audit_sink: AuditSink | None = None,
) -> ShellResult:
    return await default_client().shell(sandbox_id, request, audit_sink=audit_sink)


async def raw_exec(
    sandbox_id: str,
    command: str,
    *,
    cwd: str | None = None,
    timeout: int | None = None,
    audit_sink: AuditSink | None = None,
) -> RawExecResult:
    return await default_client().raw_exec(
        sandbox_id,
        command,
        cwd=cwd,
        timeout=timeout,
        audit_sink=audit_sink,
    )


async def read_file(
    sandbox_id: str,
    request: ReadFileRequest,
    *,
    audit_sink: AuditSink | None = None,
) -> ReadFileResult:
    return await default_client().read_file(sandbox_id, request, audit_sink=audit_sink)


async def write_file(
    sandbox_id: str,
    request: WriteFileRequest,
    *,
    audit_sink: AuditSink | None = None,
) -> WriteFileResult:
    return await default_client().write_file(sandbox_id, request, audit_sink=audit_sink)


async def edit_file(
    sandbox_id: str,
    request: EditFileRequest,
    *,
    audit_sink: AuditSink | None = None,
) -> EditFileResult:
    return await default_client().edit_file(sandbox_id, request, audit_sink=audit_sink)


__all__ = [
    "configure_default_client",
    "context_preparer_for",
    "create_sandbox",
    "default_client",
    "delete_sandbox",
    "edit_file",
    "ensure_sandbox_running",
    "get_build_logs_url",
    "get_health",
    "get_sandbox",
    "get_signed_preview_url",
    "list_sandboxes",
    "list_snapshots",
    "raw_exec",
    "read_file",
    "set_default_client",
    "set_sandbox_labels",
    "shell",
    "start_sandbox",
    "stop_sandbox",
    "write_file",
]
