"""Tool-facing, audit-aware sandbox surface.

Tools depend on this Protocol and never on ``sandbox.daytona.*`` or on a
``CodeIntelligenceService`` handle. Audit metadata reaches the tool layer
as fields on the result models, not as a separate service dependency.
"""

from __future__ import annotations

from typing import Protocol

from sandbox.api.models import (
    EditFileRequest,
    EditFileResult,
    ReadFileRequest,
    ReadFileResult,
    ShellRequest,
    ShellResult,
    WriteFileRequest,
    WriteFileResult,
)


class SandboxApi(Protocol):
    """Audit-aware sandbox I/O exposed to tools."""

    name: str

    async def read_file(
        self, sandbox_id: str, request: ReadFileRequest,
    ) -> ReadFileResult: ...

    async def write_file(
        self, sandbox_id: str, request: WriteFileRequest,
    ) -> WriteFileResult: ...

    async def edit_file(
        self, sandbox_id: str, request: EditFileRequest,
    ) -> EditFileResult: ...

    async def shell(
        self, sandbox_id: str, request: ShellRequest,
    ) -> ShellResult: ...


__all__ = ["SandboxApi"]
