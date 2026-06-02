"""Internal shell-format command primitive for namespace execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sandbox.overlay.subprocess_runner import subprocess_to_refs
from sandbox.shared.command_exec_policy import CommandExecPolicy


@dataclass(frozen=True, kw_only=True)
class CommandPrimitiveResult:
    success: bool
    workspace: str = "ephemeral"
    timings: dict[str, float] = field(default_factory=dict)
    conflict: object | None = None
    conflict_reason: str | None = None
    changed_paths: tuple[str, ...] = ()
    error: object | None = None
    changed_path_kinds: dict[str, str] = field(default_factory=dict)
    mutation_source: str = "overlay_capture"
    status: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    warnings: tuple[str, ...] = ()


def run(
    command: Sequence[str],
    *,
    workspace_root: str,
    cwd: str,
    env: Mapping[str, str],
    timeout_seconds: float | None,
    stdout_ref: str | Path,
    stderr_ref: str | Path,
    policy: CommandExecPolicy,
) -> CommandPrimitiveResult:
    workspace_path = Path(workspace_root)
    cwd_path = Path(cwd)
    resolved_cwd = cwd_path if cwd_path.is_absolute() else workspace_path / cwd_path
    result_env = policy.command_environment(env)
    exit_code = subprocess_to_refs(
        command=command,
        cwd=resolved_cwd,
        env=result_env,
        timeout_seconds=timeout_seconds,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
    )
    stdout = Path(stdout_ref).read_bytes().decode("utf-8", "replace")
    stderr = Path(stderr_ref).read_bytes().decode("utf-8", "replace")
    timed_out = exit_code == 124
    success = exit_code == 0
    return CommandPrimitiveResult(
        success=success,
        status="timed_out" if timed_out else "ok" if success else "error",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


__all__ = ["CommandPrimitiveResult", "run"]
