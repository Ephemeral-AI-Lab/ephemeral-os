"""Workspace replacement command runner."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sandbox.command_exec.contract import (
    CommandExecRequest,
    MountMode,
    ShellProcessResult,
    WorkspaceReplacementMountSpec,
)
from sandbox.command_exec.policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox.command_exec.strategies import (
    CopyBackedStrategy,
    ExecutionStrategy,
    PrivateNamespaceStrategy,
    StrategyRegistry,
    detect_private_mount_namespace,
)
from sandbox.command_exec.workspace.path_rewrite import (
    path_starts_at,
    rewrite_declared_workspace_env,
    rewrite_declared_workspace_refs,
    rewrite_path_token,
    rewrite_workspace_paths,
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
            private_namespace_available=_private_mount_namespace_available(),
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


def _private_mount_namespace_available() -> bool:
    return detect_private_mount_namespace()


def _run_copy_backed_mount(
    *,
    spec: WorkspaceReplacementMountSpec,
    request: CommandExecRequest,
    run_dir: Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    return CopyBackedStrategy().run(
        spec=spec,
        request=request,
        run_dir=run_dir,
        timings=timings,
    )


def _run_private_mount_namespace(
    *,
    spec: WorkspaceReplacementMountSpec,
    request: CommandExecRequest,
    run_dir: Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    return PrivateNamespaceStrategy(available=True).run(
        spec=spec,
        request=request,
        run_dir=run_dir,
        timings=timings,
    )


def _is_namespace_mount_failure(
    process: ShellProcessResult,
    *,
    run_dir: str | Path | None = None,
) -> bool:
    if run_dir is None:
        return False
    return PrivateNamespaceStrategy(available=True).is_recoverable_failure(
        process,
        run_dir=Path(run_dir),
    )


def _rewrite_declared_workspace_refs(
    command: tuple[str, ...],
    workspace_root: str,
    mounted_workspace_root: str,
) -> tuple[str, ...]:
    return rewrite_declared_workspace_refs(
        command,
        workspace_root=workspace_root,
        mounted_workspace_root=mounted_workspace_root,
    )


def _rewrite_declared_workspace_env(
    env: dict[str, str],
    *,
    workspace_root: str,
    mounted_workspace_root: str,
) -> dict[str, str]:
    return rewrite_declared_workspace_env(
        env,
        workspace_root=workspace_root,
        mounted_workspace_root=mounted_workspace_root,
    )


def _rewrite_workspace_paths(
    value: str,
    *,
    workspace_root: str,
    mounted_workspace_root: str,
) -> str:
    return rewrite_workspace_paths(
        value,
        workspace_root=workspace_root,
        mounted_workspace_root=mounted_workspace_root,
    )


def _rewrite_path_token(
    value: str,
    *,
    workspace_root: str,
    mounted_workspace_root: str,
) -> str:
    return rewrite_path_token(
        value,
        workspace_root=workspace_root,
        mounted_workspace_root=mounted_workspace_root,
    )


def _path_starts_at(value: str, index: int, workspace_root: str) -> bool:
    return path_starts_at(value, index, workspace_root)


__all__ = [
    "MountMode",
    "WorkspaceReplacementMountSpec",
    "run_workspace_replaced_command",
]
