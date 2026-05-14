"""Workspace replacement command runner."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sandbox.execution.contract import (
    CommandExecRequest,
    MountMode,
    ShellProcessResult,
    WorkspaceReplacementMountSpec,
)
from sandbox.execution.policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox.execution.strategies import (
    ExecutionStrategy,
    StrategyRegistry,
    detect_private_mount_namespace,
)


def run_workspace_replaced_command(
    *,
    spec: WorkspaceReplacementMountSpec,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
    strategies: Sequence[ExecutionStrategy] | None = None,
    policy: CommandExecPolicy = DEFAULT_COMMAND_EXEC_POLICY,
) -> ShellProcessResult:
    """Run a command with the assigned workspace replaced by the leased view."""
    run_root = Path(run_dir)
    run_root.mkdir(parents=True, exist_ok=True)
    registry = (
        StrategyRegistry(tuple(strategies))
        if strategies is not None
        else StrategyRegistry.bootstrap(
            private_namespace_available=detect_private_mount_namespace(),
            policy=policy,
        )
    )
    for strategy in registry.strategies:
        if not strategy.is_available():
            continue
        process = strategy.run(
            spec=spec,
            request=request,
            run_dir=run_root,
            timings=timings,
        )
        if not strategy.is_recoverable_failure(process, run_dir=run_root):
            return process
        fallback_key = (
            "command_exec.private_mount_fallback"
            if strategy.name == MountMode.PRIVATE_NAMESPACE.value
            else f"command_exec.{strategy.name}_fallback"
        )
        timings[fallback_key] = 1.0
    raise RuntimeError("no command execution strategy succeeded")


__all__ = [
    "MountMode",
    "WorkspaceReplacementMountSpec",
    "run_workspace_replaced_command",
]
