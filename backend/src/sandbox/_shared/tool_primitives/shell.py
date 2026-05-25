"""Shell primitive for namespace-mounted workspaces."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from sandbox._shared.command_exec_policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox._shared.models import ShellResult
from sandbox.overlay.subprocess_runner import run_command_to_refs


def run(
    command: Sequence[str],
    *,
    workspace_root: str,
    cwd: str = ".",
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    stdout_ref: str | Path,
    stderr_ref: str | Path,
    cancel_event: threading.Event | None = None,
    pid_recorder: Callable[[int], None] | None = None,
    policy: CommandExecPolicy = DEFAULT_COMMAND_EXEC_POLICY,
) -> ShellResult:
    exit_code = run_command_to_refs(
        command=command,
        declared_workspace_root=workspace_root,
        mounted_workspace_root=workspace_root,
        cwd=cwd,
        env=env or {},
        timeout_seconds=timeout_seconds,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        policy=policy,
        cancel_event=cancel_event,
        pid_recorder=pid_recorder,
    )
    return ShellResult(
        exit_code=exit_code,
        stdout=Path(stdout_ref).read_bytes().decode("utf-8", "replace"),
        stderr=Path(stderr_ref).read_bytes().decode("utf-8", "replace"),
        status="ok" if exit_code == 0 else "error",
    )


__all__ = ["run"]
