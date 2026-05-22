"""Strategy protocol for workspace-replaced command execution."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from sandbox.execution.contract import (
    CommandExecRequest,
    OverlayLayout,
    ShellProcessResult,
)


class ExecutionStrategy(Protocol):
    """Runnable command execution strategy."""

    name: str

    def is_available(self) -> bool: ...

    def run(
        self,
        *,
        spec: OverlayLayout,
        request: CommandExecRequest,
        run_dir: Path,
        timings: dict[str, float],
        cancel_event: threading.Event | None = None,
        pid_recorder: Callable[[int], None] | None = None,
    ) -> ShellProcessResult: ...

    def should_fall_back(
        self,
        result: ShellProcessResult,
        *,
        run_dir: Path,
    ) -> bool: ...


__all__ = ["ExecutionStrategy"]
