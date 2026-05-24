"""Runtime handler for layer-stack snapshot overlay requests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from typing import cast

from sandbox.daemon.service.layer_stack_client import LayerStackClient
from sandbox.ephemeral_workspace.pipeline import (
    get_sandbox_overlay,
    stop_sandbox_overlay,
)
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    OverlayCapture,
    OverlayShellRequest,
    ShellProcessResult,
)
from sandbox.ephemeral_workspace._execute_command import execute_command
from sandbox._shared.env_policy import CommandExecPolicy
from sandbox.layer_stack.manifest import Manifest
from sandbox.overlay.layout import LayerPathsLayout
from sandbox.overlay.namespace import run_in_namespace

_OVERLAY_COMMAND_POLICY = CommandExecPolicy(
    host_env_keys=frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TERM",
            "TZ",
        }
    ),
)


async def run_snapshot_overlay(args: dict[str, Any]) -> dict[str, Any]:
    if "layer_stack_root" not in args:
        raise ValueError("overlay.run requires layer_stack_root")
    capture = await _run_snapshot_overlay(args)
    return capture.to_dict()


async def flush_workspace_overlay(args: dict[str, Any]) -> dict[str, object]:
    if "layer_stack_root" not in args:
        raise ValueError("overlay.flush requires layer_stack_root")
    overlay = await get_sandbox_overlay(
        str(args["layer_stack_root"]),
        workspace_root=args.get("workspace_root"),
        start=False,
    )
    return await overlay.flush_to_workspace()


async def stop_workspace_overlay(args: dict[str, Any]) -> dict[str, object]:
    if "layer_stack_root" not in args:
        raise ValueError("overlay.stop requires layer_stack_root")
    result = await stop_sandbox_overlay(
        str(args["layer_stack_root"]),
        workspace_root=args.get("workspace_root"),
    )
    workspace_roots = result.get("workspace_roots")
    if isinstance(workspace_roots, list) and workspace_roots:
        result["workspace_root"] = workspace_roots[0]
    return result


async def _run_snapshot_overlay(args: dict[str, Any]) -> OverlayCapture:
    layer_stack = LayerStackClient(str(args["layer_stack_root"]))
    overlay_request = OverlayShellRequest.from_dict(_snapshot_request_payload(args))
    result = await execute_command(
        _command_request(
            overlay_request,
            layer_stack_root=layer_stack.storage_root,
            workspace_root=str(args.get("workspace_root") or "/workspace"),
        ),
        layer_stack=layer_stack,
        capture_publisher=None,
        storage_root=layer_stack.storage_root,
        occ_apply=False,
        command_runner=_run_overlay_command,
    )
    return OverlayCapture(
        exit_code=result.exit_code,
        stdout_ref=result.stdout_ref,
        stderr_ref=result.stderr_ref,
        snapshot_version=result.workspace_capture.snapshot_version,
        changes=tuple(result.workspace_capture.changes),
        mount_mode=result.workspace_capture.mount_mode,
        snapshot_manifest=cast(
            Manifest | None,
            result.workspace_capture.snapshot_manifest,
        ),
        timings=result.timings,
    )


def _command_request(
    request: OverlayShellRequest,
    *,
    layer_stack_root: Path,
    workspace_root: str,
) -> CommandExecRequest:
    return CommandExecRequest(
        request_id=request.request_id,
        workspace_ref=layer_stack_root.as_posix(),
        workspace_root=workspace_root,
        command=request.command,
        cwd=request.cwd,
        env=request.env,
        timeout_seconds=request.timeout_seconds,
    )


def _run_overlay_command(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    return run_in_namespace(
        spec=spec,
        request=request,
        run_dir=Path(run_dir),
        timings=timings,
        policy=_OVERLAY_COMMAND_POLICY,
    )


def _snapshot_request_payload(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, list):
        raise ValueError("layer-stack overlay.run requires command as argv list")
    env = args.get("env") or {}
    if not isinstance(env, Mapping):
        raise ValueError("layer-stack overlay.run env must be an object")
    return {
        "request_id": str(args.get("request_id") or "overlay-run"),
        "command": command,
        "cwd": str(args.get("cwd") or "."),
        "env": dict(env),
        "timeout_seconds": args.get("timeout_seconds", args.get("timeout")),
    }


__all__ = [
    "flush_workspace_overlay",
    "run_snapshot_overlay",
    "stop_workspace_overlay",
]
