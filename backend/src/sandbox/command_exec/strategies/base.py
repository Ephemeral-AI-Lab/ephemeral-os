"""Strategy protocol for workspace-replaced command execution."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sandbox.command_exec.contract import (
    CommandExecRequest,
    ShellProcessResult,
    WorkspaceReplacementMountSpec,
)


class ExecutionStrategy(Protocol):
    """Runnable command execution strategy."""

    name: str

    def is_available(self) -> bool: ...

    def run(
        self,
        *,
        spec: WorkspaceReplacementMountSpec,
        request: CommandExecRequest,
        run_dir: Path,
        timings: dict[str, float],
    ) -> ShellProcessResult: ...

    def is_recoverable_failure(
        self,
        result: ShellProcessResult,
        *,
        run_dir: Path,
    ) -> bool: ...


__all__ = ["ExecutionStrategy"]
