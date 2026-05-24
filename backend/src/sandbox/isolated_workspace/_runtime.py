"""Linux runtime helpers for isolated workspaces."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sandbox.isolated_workspace._types import (
    CGROUP_ROOT,
    HANDLE_PREFIX,
    IsolatedWorkspaceError,
    IsolatedWorkspaceHandle,
    logger,
)

_PR_SET_CHILD_SUBREAPER = 36  # linux/prctl.h
def _read_unshare_grandchild_pid(unshare_pid: int) -> int | None:
    try:
        text = Path(
            f"/proc/{unshare_pid}/task/{unshare_pid}/children"
        ).read_text(encoding="utf-8")
    except OSError:
        return None
    tokens = text.split()
    if not tokens:
        return None
    try:
        return int(tokens[0])
    except ValueError:
        return None
def _wait_pid_with_timeout(pid: int, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
def _enable_child_subreaper() -> bool:
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return False
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return False
    return True
class _LinuxRuntime:
    """Default runtime — calls real Linux syscalls / utilities."""
    def __init__(self) -> None:
        self._holders: dict[int, subprocess.Popen[bytes]] = {}
        self._grandchildren: dict[int, int] = {}
        _enable_child_subreaper()
    def spawn_ns_holder(self, handle: IsolatedWorkspaceHandle, *, setup_timeout_s: float) -> int:
        r_parent, r_holder = os.pipe()
        c_holder, c_parent = os.pipe()
        proc = subprocess.Popen(
            [
                "unshare", "--user", "--map-root-user",
                "--net", "--pid", "--mount",
                "--fork",
                "--kill-child",
                "--propagation", "private",
                sys.executable, "-m", "sandbox.isolated_workspace.scripts.ns_holder",
                str(r_holder), str(c_holder),
            ],
            pass_fds=(r_holder, c_holder),
        )
        self._holders[proc.pid] = proc
        os.close(r_holder)
        os.close(c_holder)
        try:
            _expect_line(r_parent, b"ns-up", timeout_s=setup_timeout_s)
        except BaseException:
            os.close(r_parent)
            os.close(c_parent)
            raise
        grandchild_pid = _read_unshare_grandchild_pid(proc.pid)
        if grandchild_pid is not None:
            self._grandchildren[proc.pid] = grandchild_pid
        handle.readiness_fd = r_parent
        handle.control_fd = c_parent
        return proc.pid
    def open_ns_fds(self, root_pid: int) -> dict[str, int]:
        ns_paths = {
            "user": f"/proc/{root_pid}/ns/user",
            "mnt": f"/proc/{root_pid}/ns/mnt",
            "pid": f"/proc/{root_pid}/ns/pid_for_children",
            "net": f"/proc/{root_pid}/ns/net",
        }
        return {
            name: os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            for name, path in ns_paths.items()
        }
    async def mount_overlay(
        self, handle: IsolatedWorkspaceHandle, *, layer_paths: tuple[str, ...]
    ) -> None:
        user_fd = handle.ns_fds.get("user")
        mnt_fd = handle.ns_fds.get("mnt")
        if user_fd is None or mnt_fd is None:
            raise IsolatedWorkspaceError(
                "setup_failed",
                "mount_overlay requires user+mnt ns FDs",
                failed_step="overlay_mount",
            )
        lowerdirs = list(layer_paths) if layer_paths else [handle.workspace_root]
        payload = json.dumps(
            {
                "ns_fds": {"user": user_fd, "mnt": mnt_fd},
                "target": handle.workspace_root,
                "lowerdirs": lowerdirs,
                "upperdir": handle.upperdir.as_posix(),
                "workdir": handle.workdir.as_posix(),
            }
        ).encode("utf-8")
        returncode, _stdout, stderr_bytes = await _run_helper_subprocess(
            argv=[
                sys.executable,
                "-m",
                "sandbox.isolated_workspace.scripts.setns_overlay_mount",
            ],
            stdin_bytes=payload,
            timeout_s=30.0,
            pass_fds=(user_fd, mnt_fd),
        )
        if returncode != 0:
            raise IsolatedWorkspaceError(
                "setup_failed",
                "mount_overlay helper failed",
                failed_step="overlay_mount",
                helper_stderr=stderr_bytes.decode("utf-8", errors="replace"),
                return_code=returncode,
            )
    async def configure_dns(
        self, handle: IsolatedWorkspaceHandle, *, fallback_dns: str
    ) -> bool:
        user_fd = handle.ns_fds.get("user")
        mnt_fd = handle.ns_fds.get("mnt")
        if user_fd is None or mnt_fd is None:
            return False
        payload = json.dumps(
            {
                "ns_fds": {"user": user_fd, "mnt": mnt_fd},
                "fallback_dns": fallback_dns,
            }
        ).encode("utf-8")
        returncode, stdout_bytes, stderr_bytes = await _run_helper_subprocess(
            argv=[
                sys.executable,
                "-m",
                "sandbox.isolated_workspace.scripts.configure_dns_in_ns",
            ],
            stdin_bytes=payload,
            timeout_s=10.0,
            pass_fds=(user_fd, mnt_fd),
        )
        if returncode != 0:
            logger.warning(
                "configure_dns helper failed rc=%d stderr=%s",
                returncode,
                stderr_bytes.decode("utf-8", errors="replace"),
            )
            return False
        try:
            result = json.loads(stdout_bytes.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return False
        return bool(result.get("applied_fallback", False))
    def signal_net_ready(
        self, handle: IsolatedWorkspaceHandle, *, setup_timeout_s: float
    ) -> None:
        if handle.control_fd < 0:
            return
        try:
            os.write(handle.control_fd, b"net-ready\n")
        except BrokenPipeError as exc:
            raise IsolatedWorkspaceError(
                "setup_failed",
                "ns_holder closed control pipe before net-ready",
                failed_step="signal_net_ready",
            ) from exc
        if handle.readiness_fd < 0:
            return
        _expect_line(handle.readiness_fd, b"ready", timeout_s=setup_timeout_s)
    def create_cgroup(self, handle: IsolatedWorkspaceHandle) -> Path:
        path = CGROUP_ROOT / f"{HANDLE_PREFIX}{handle.handle_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path
    def freeze(self, handle: IsolatedWorkspaceHandle, *, freeze: bool) -> None:
        if handle.cgroup_path is None:
            return
        procs_file = handle.cgroup_path / "cgroup.procs"
        if freeze and procs_file.exists():
            try:
                pids = [
                    int(line)
                    for line in procs_file.read_text().splitlines()
                    if line.strip().isdigit()
                ]
            except OSError:
                pids = []
            root_procs = CGROUP_ROOT / "cgroup.procs"
            for pid in pids:
                with contextlib.suppress(OSError):
                    root_procs.write_text(f"{pid}\n")
        freeze_file = handle.cgroup_path / "cgroup.freeze"
        expected = "1" if freeze else "0"
        if freeze_file.exists():
            try:
                freeze_file.write_text(f"{expected}\n")
                actual = freeze_file.read_text().strip()
                if actual == expected:
                    return
            except OSError:
                pass
            handle.freezer_degraded = True
        else:
            handle.freezer_degraded = True
        if not procs_file.exists():
            return
        sig = signal.SIGSTOP if freeze else signal.SIGCONT
        try:
            pids = [
                int(line)
                for line in procs_file.read_text().splitlines()
                if line.strip().isdigit()
            ]
        except OSError:
            pids = []
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
    def kill_holder(self, root_pid: int, *, grace_s: float) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(root_pid, signal.SIGTERM)
        died = False
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            try:
                os.kill(root_pid, 0)
            except ProcessLookupError:
                died = True
                break
            time.sleep(0.05)
        if not died:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(root_pid, signal.SIGKILL)
        proc = self._holders.pop(root_pid, None)
        if proc is not None:
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=2.0)
        else:
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(root_pid, os.WNOHANG)
        grandchild = self._grandchildren.pop(root_pid, None)
        if grandchild is not None:
            with contextlib.suppress(ChildProcessError, OSError):
                _wait_pid_with_timeout(grandchild, timeout_s=2.0)
        with contextlib.suppress(ChildProcessError, OSError):
            while True:
                reaped_pid, _status = os.waitpid(-1, os.WNOHANG)
                if reaped_pid == 0:
                    break
    def run_in_handle(
        self,
        handle: IsolatedWorkspaceHandle,
        *,
        argv: list[str],
        stdin: bytes | None = None,
        timeout_s: float | None = None,
    ) -> tuple[int, bytes, bytes]:
        ns_fds = {k: handle.ns_fds[k] for k in ("user", "mnt", "pid", "net") if k in handle.ns_fds}
        payload_dict: dict[str, Any] = {"ns_fds": ns_fds, "argv": argv}
        if stdin:
            payload_dict["stdin_b64"] = base64.b64encode(stdin).decode("ascii")
        if handle.cgroup_path is not None:
            payload_dict["cgroup_path"] = str(handle.cgroup_path)
        payload = json.dumps(payload_dict).encode("utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "sandbox.isolated_workspace.scripts.setns_exec"],
            input=payload,
            capture_output=True,
            timeout=timeout_s,
            pass_fds=tuple(ns_fds.values()),
        )
        return proc.returncode, proc.stdout, proc.stderr
async def _run_helper_subprocess(
    *,
    argv: list[str],
    stdin_bytes: bytes,
    timeout_s: float,
    pass_fds: tuple[int, ...],
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=pass_fds,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes), timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise IsolatedWorkspaceError(
            "setup_timeout",
            f"helper {argv[-1]} exceeded {timeout_s}s",
            failed_step=argv[-1].rsplit(".", 1)[-1],
        )
    return proc.returncode or 0, stdout or b"", stderr or b""
def _read_memavailable_kb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 16 * 1024 * 1024
def _du_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.stat(os.path.join(root, f)).st_size
    return total
def _expect_line(fd: int, prefix: bytes, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    buf = b""
    while b"\n" not in buf:
        if time.monotonic() > deadline:
            raise IsolatedWorkspaceError(
                "setup_timeout", f"ns_holder did not signal {prefix!r}",
                failed_step="ns_holder_ready",
            )
        chunk = os.read(fd, 64)
        if not chunk:
            raise IsolatedWorkspaceError(
                "setup_failed", "ns_holder closed pipe before signaling",
            )
        buf += chunk
    if not buf.startswith(prefix):
        raise IsolatedWorkspaceError(
            "setup_failed", f"unexpected ns_holder signal: {buf!r}",
        )


__all__ = ["_LinuxRuntime", "_du_bytes", "_read_memavailable_kb"]
