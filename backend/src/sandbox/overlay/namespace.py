"""Private mount namespace command execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from sandbox._shared.env_policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    PRIVATE_NAMESPACE_MOUNT,
    ShellProcessResult,
)
from sandbox.overlay.layout import LayerPathsLayout
from sandbox.overlay.subprocess_runner import wait_for_process_with_cancel

NAMESPACE_INFRA_EXIT_CODE = 125
NAMESPACE_CONTROL_REF = "namespace-control.json"


def run_in_namespace(
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
