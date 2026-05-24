"""Private mount namespace command execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from sandbox._shared.env_policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox._shared.tool_primitives.cancellation import (
    NO_OP_CANCELLATION,
    ShellPgrpCancellation,
    VerbCancellation,
)
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    PRIVATE_NAMESPACE_MOUNT,
    ShellProcessResult,
)
from sandbox._shared.models import ToolCallRequest, ToolCallResult
from sandbox.overlay.handle import OverlayHandle
from sandbox.overlay.layout import LayerPathsLayout
from sandbox.overlay.subprocess_runner import wait_for_process_with_cancel

NAMESPACE_INFRA_EXIT_CODE = 125
NAMESPACE_CONTROL_REF = "namespace-control.json"
TOOL_CALL_COMMAND_POLICY = CommandExecPolicy(
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


def run_in_namespace(*args: Any, **kwargs: Any) -> Any:
    """Run either the legacy command path or a unified tool call in namespace."""
    if args and isinstance(args[0], OverlayHandle):
        handle = args[0]
        req = args[1]
        return _run_tool_call_in_namespace(
            handle,
            req,
            *args[2:],
            cancellation=_build_verb_cancellation(req),
            **kwargs,
        )
    return _run_command_in_namespace(**kwargs)


def _run_command_in_namespace(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: Path,
    timings: dict[str, float],
    policy: CommandExecPolicy = DEFAULT_COMMAND_EXEC_POLICY,
    cancel_event: threading.Event | None = None,
    pid_recorder: Callable[[int], None] | None = None,
) -> ShellProcessResult:
    """Run a command by overlay-mounting the leased workspace in a namespace."""
    stdout_ref = run_dir / "stdout.bin"
    stderr_ref = run_dir / "stderr.bin"
    timings_ref = run_dir / "namespace-timings.json"
    control_ref = run_dir / NAMESPACE_CONTROL_REF
    payload_ref = run_dir / "namespace-request.json"
    payload_ref.write_text(
        json.dumps(
            {
                "workspace_root": spec.workspace_root,
                "layer_paths": list(spec.layer_paths),
                "upperdir": spec.writes,
                "workdir": spec.kernel_scratch,
                "command": list(request.command),
                "cwd": request.cwd,
                "env": dict(request.env),
                "timeout_seconds": request.timeout_seconds,
                "stdout_ref": str(stdout_ref),
                "stderr_ref": str(stderr_ref),
                "timings_ref": str(timings_ref),
                "control_ref": str(control_ref),
                "policy": policy.to_payload(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    timeout = None if request.timeout_seconds is None else request.timeout_seconds + 10
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    stderr_ref.parent.mkdir(parents=True, exist_ok=True)
    exit_code = _run_namespace_child(
        payload_ref=payload_ref,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        timeout=timeout,
        cancel_event=cancel_event,
        pid_recorder=pid_recorder,
    )
    _merge_namespace_timings(timings_ref, timings)
    return ShellProcessResult(
        exit_code=exit_code,
        stdout_ref=str(stdout_ref),
        stderr_ref=str(stderr_ref),
        mounted_workspace_root=spec.workspace_root,
        mount_mode=PRIVATE_NAMESPACE_MOUNT,
    )


async def _run_tool_call_in_namespace(
    handle: OverlayHandle,
    req: ToolCallRequest,
    *,
    isolated_runner: Callable[[list[str], bytes | None, float | None], Awaitable[Mapping[str, Any]]] | None = None,
    cancellation: VerbCancellation = NO_OP_CANCELLATION,
) -> ToolCallResult:
    if isolated_runner is not None:
        return await _run_tool_call_in_existing_namespace(
            handle,
            req,
            isolated_runner=isolated_runner,
            cancellation=cancellation,
        )
    run_dir = handle.upperdir.parent
    stdout_ref = run_dir / "stdout.bin"
    stderr_ref = run_dir / "stderr.bin"
    timings_ref = run_dir / "namespace-tool-timings.json"
    result_ref = run_dir / "namespace-tool-result.json"
    payload_ref = run_dir / "namespace-tool-request.json"
    payload_ref.write_text(
        json.dumps(
            {
                "workspace_root": handle.workspace_root,
                "layer_paths": list(handle.layer_paths),
                "upperdir": handle.upperdir.as_posix(),
                "workdir": handle.workdir.as_posix(),
                "tool_call": req.to_payload(),
                "stdout_ref": str(stdout_ref),
                "stderr_ref": str(stderr_ref),
                "timings_ref": str(timings_ref),
                "result_ref": str(result_ref),
                "policy": TOOL_CALL_COMMAND_POLICY.to_payload(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    stderr_ref.parent.mkdir(parents=True, exist_ok=True)
    child_task = asyncio.create_task(
        asyncio.to_thread(
            _run_namespace_child,
            payload_ref=payload_ref,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            timeout=_tool_timeout(req),
            cancel_event=cancellation.cancel_event,
            pid_recorder=cancellation.record_pid,
        )
    )
    try:
        exit_code = await asyncio.shield(child_task)
    except asyncio.CancelledError:
        cancellation.on_cancel()
        with contextlib.suppress(Exception):
            await asyncio.shield(child_task)
        raise
    if result_ref.exists():
        return _read_tool_result(result_ref)
    stderr = stderr_ref.read_text(encoding="utf-8", errors="replace") if stderr_ref.exists() else ""
    return {
        "success": False,
        "workspace": "ephemeral",
        "status": "error",
        "error": {
            "kind": "namespace_child_failed",
            "message": stderr.strip() or f"namespace child exited {exit_code}",
        },
        "timings": {},
    }


async def _run_tool_call_in_existing_namespace(
    handle: OverlayHandle,
    req: ToolCallRequest,
    *,
    isolated_runner: Callable[[list[str], bytes | None, float | None], Awaitable[Mapping[str, Any]]],
    cancellation: VerbCancellation = NO_OP_CANCELLATION,
) -> ToolCallResult:
    payload = json.dumps(
        {
            "workspace_root": handle.workspace_root,
            "tool_call": req.to_payload(),
            "stdout_ref": (handle.upperdir.parent / f"{req.request_id}.stdout").as_posix(),
            "stderr_ref": (handle.upperdir.parent / f"{req.request_id}.stderr").as_posix(),
            "policy": TOOL_CALL_COMMAND_POLICY.to_payload(),
            "mount_overlay": False,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    src_root = Path(__file__).resolve().parents[2].as_posix()
    script = (
        "import json,sys;"
        f"sys.path.insert(0,{src_root!r});"
        "from sandbox.overlay.namespace_child import execute_tool_payload;"
        "payload=json.loads(sys.stdin.buffer.read());"
        "print(json.dumps(execute_tool_payload(payload),separators=(',',':'),sort_keys=True))"
    )
    try:
        response = await isolated_runner(
            [sys.executable, "-c", script],
            payload,
            _tool_timeout(req),
        )
    except asyncio.CancelledError:
        cancellation.on_cancel()
        raise
    if not response.get("success"):
        return dict(response)
    stdout = str(response.get("stdout") or "")
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "workspace": "isolated",
            "status": "error",
            "error": {
                "kind": "namespace_child_bad_json",
                "message": stdout or str(response.get("stderr") or ""),
            },
            "timings": {},
        }
    if isinstance(result, dict):
        result.setdefault("workspace", "isolated")
        return result
    return {
        "success": False,
        "workspace": "isolated",
        "status": "error",
        "error": {"kind": "namespace_child_bad_result", "message": repr(result)},
        "timings": {},
    }


def _read_tool_result(path: Path) -> ToolCallResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("namespace tool result must be a JSON object")
    return raw


def _tool_timeout(req: ToolCallRequest) -> float | None:
    raw = req.args.get("timeout_seconds", req.args.get("timeout"))
    if raw is None:
        return None
    try:
        return float(str(raw)) + 10.0
    except (TypeError, ValueError):
        return None


def _build_verb_cancellation(req: ToolCallRequest) -> VerbCancellation:
    if req.verb == "shell":
        return ShellPgrpCancellation()
    return NO_OP_CANCELLATION


def jsonable_result(value: Any) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return {str(k): _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    raise TypeError(f"tool primitive returned non-object result: {type(value).__name__}")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {str(k): _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _run_namespace_child(
    *,
    payload_ref: Path,
    stdout_ref: Path,
    stderr_ref: Path,
    timeout: float | None,
    cancel_event: threading.Event | None,
    pid_recorder: Callable[[int], None] | None,
) -> int:
    """Spawn ``unshare -Urm python -m namespace_child`` with cancel support."""
    cmd = [
        _unshare_path(),
        "-Urm",
        sys.executable,
        "-m",
        "sandbox.overlay.namespace_child",
        str(payload_ref),
    ]
    with stdout_ref.open("wb") as stdout_file, stderr_ref.open("wb") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        if pid_recorder is not None:
            try:
                pid_recorder(proc.pid)
            except Exception:
                pass
        try:
            try:
                return wait_for_process_with_cancel(
                    proc,
                    timeout_seconds=timeout,
                    cancel_event=cancel_event,
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
                raise
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, 9)
                except (ProcessLookupError, PermissionError):
                    pass


def detect_private_mount_namespace() -> bool:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return False
    if _unshare_path() == "" or shutil.which("mount") is None:
        return False
    try:
        result = subprocess.run(
            [_unshare_path(), "-Urm", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _merge_namespace_timings(path: Path, timings: dict[str, float]) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            timings[str(key)] = float(value)


def _unshare_path() -> str:
    return shutil.which("unshare") or ""


__all__ = [
    "NAMESPACE_CONTROL_REF",
    "NAMESPACE_INFRA_EXIT_CODE",
    "detect_private_mount_namespace",
    "run_in_namespace",
]
