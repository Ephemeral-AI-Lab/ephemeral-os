"""Provider-backed client for the bundled in-sandbox runtime dispatcher."""

from __future__ import annotations

import json
import logging
import os
import shlex
import threading
import time
from types import SimpleNamespace
from typing import Any, Protocol

from sandbox.control.daemon.bundle import BUNDLE_REMOTE_DIR, ensure_runtime_uploaded
from sandbox.providers.registry import get_adapter
from sandbox.runtime.async_bridge import run_sync

logger = logging.getLogger(__name__)

# Runtime launcher: ensures the resident daemon is running, then invokes a
# tiny AF_UNIX client that pipes one envelope and prints the response. The
# daemon is spawned via ``nohup`` once per sandbox; subsequent calls hit the
# already-warm process. Both the spawn and the per-call thin client are emitted
# through ``provider.exec``; Daytona stays inside the adapter.
_RUNTIME_DAEMON_SOCKET = f"{BUNDLE_REMOTE_DIR}/runtime.sock"
_RUNTIME_DAEMON_PID = f"{BUNDLE_REMOTE_DIR}/runtime.pid"
_RUNTIME_DAEMON_LOG = f"{BUNDLE_REMOTE_DIR}/runtime.log"

_RUNTIME_THIN_CLIENT_PY = (
    "import socket,sys,os\n"
    f"s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(float(os.environ.get('EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT','600')));s.connect({_RUNTIME_DAEMON_SOCKET!r});"
    "s.sendall(sys.argv[1].encode('utf-8')+b'\\n');s.shutdown(socket.SHUT_WR);"
    "buf=b''\n"
    "while True:\n"
    " chunk=s.recv(65536)\n"
    " if not chunk: break\n"
    " buf+=chunk\n"
    "sys.stdout.buffer.write(buf)\n"
)

_RUNTIME_DAEMON_LAUNCHER = f"""\
set -e
SOCK={shlex.quote(_RUNTIME_DAEMON_SOCKET)}
PID={shlex.quote(_RUNTIME_DAEMON_PID)}
LOG={shlex.quote(_RUNTIME_DAEMON_LOG)}
mkdir -p {shlex.quote(BUNDLE_REMOTE_DIR)}
if [ -S "$SOCK" ] && [ -f "$PID" ] && kill -0 "$(cat "$PID" 2>/dev/null)" 2>/dev/null; then
    exit 0
fi
rm -f "$SOCK"
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        nohup "$py" -m sandbox.runtime.daemon --socket "$SOCK" --pid-file "$PID" </dev/null >"$LOG" 2>&1 &
        # Wait briefly for the socket to appear so the next client connect succeeds.
        for _ in $(seq 1 50); do
            [ -S "$SOCK" ] && exit 0
            sleep 0.05
        done
        echo 'sandbox runtime daemon failed to bind socket within 2.5s' >&2
        exit 1
    fi
done
echo 'sandbox runtime requires Python >= 3.10' >&2
exit 127
"""

_RUNTIME_THIN_CLIENT_LAUNCHER = f"""\
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1; then
        exec "$py" -c {shlex.quote(_RUNTIME_THIN_CLIENT_PY)} "$1"
    fi
done
echo 'sandbox runtime requires python3' >&2
exit 127
"""


class RuntimeCommandError(Exception):
    """Raised when the runtime dispatcher returns a structured error."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class _RuntimeDispatchError(RuntimeError):
    """Raised when runtime dispatch fails before typed decoding."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class _RuntimeExec(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any: ...


async def _call_runtime_server(
    *,
    exec_fn: _RuntimeExec,
    sandbox_id: str,
    op: str,
    args: dict[str, Any],
    cwd: str = BUNDLE_REMOTE_DIR,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Dispatch one JSON envelope to the resident in-sandbox runtime daemon."""
    raw_payload = json.dumps(
        {"op": op, "args": _without_none(args)},
        separators=(",", ":"),
    )
    result = await _exec_daemon_call(
        exec_fn=exec_fn,
        sandbox_id=sandbox_id,
        raw_payload=raw_payload,
        cwd=cwd,
        timeout=timeout,
    )
    try:
        response = _decode_response(getattr(result, "stdout", ""))
    except _RuntimeDispatchError:
        if getattr(result, "exit_code", 1) != 0:
            _raise_exec_failed(result)
        raise
    if "error" in response:
        error = response.get("error") or {}
        raise _RuntimeDispatchError(
            kind=str(error.get("kind") or "RuntimeError"),
            message=str(error.get("message") or ""),
            details=error.get("details") if isinstance(error.get("details"), dict) else {},
        )
    if getattr(result, "exit_code", 1) != 0:
        _raise_exec_failed(result)
    return response


async def _exec_daemon_call(
    *,
    exec_fn: _RuntimeExec,
    sandbox_id: str,
    raw_payload: str,
    cwd: str,
    timeout: int | None,
) -> Any:
    result = await exec_fn(
        sandbox_id,
        _runtime_thin_client_command(raw_payload),
        cwd=cwd,
        timeout=timeout,
    )
    if _looks_like_socket_missing(result):
        spawn_result = await exec_fn(
            sandbox_id,
            _runtime_daemon_spawn_command(),
            cwd=cwd,
            timeout=10,
        )
        if getattr(spawn_result, "exit_code", 1) != 0:
            return spawn_result
        result = await exec_fn(
            sandbox_id,
            _runtime_thin_client_command(raw_payload),
            cwd=cwd,
            timeout=timeout,
        )
    return result


def _looks_like_socket_missing(result: Any) -> bool:
    """Detect a thin-client failure caused by a missing daemon socket.

    The thin client raises ``ConnectionRefusedError`` / ``FileNotFoundError``
    when the daemon hasn't bound the socket yet. Both surface as a non-zero
    exit code with the exception text on stderr.
    """
    if getattr(result, "exit_code", 0) == 0:
        return False
    blob = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").lower()
    needles = (
        "connectionrefusederror",
        "filenotfounderror",
        "no such file or directory",
        "connection refused",
    )
    return any(needle in blob for needle in needles)


# Env vars forwarded from host into the sandbox runtime process so feature
# flags (e.g. ``EPHEMERALOS_GITIGNORE_BACKEND``) reach the runtime handlers
# without having to be threaded through every per-call args dict.
_FORWARDED_RUNTIME_ENV_VARS: tuple[str, ...] = ("EPHEMERALOS_GITIGNORE_BACKEND",)


def _runtime_env_prefix() -> str:
    parts: list[str] = []
    for name in _FORWARDED_RUNTIME_ENV_VARS:
        value = os.environ.get(name)
        if value is None or value == "":
            continue
        parts.append(f"{name}={shlex.quote(value)}")
    if not parts:
        return ""
    return " ".join(parts) + " "


def _runtime_thin_client_command(raw_payload: str) -> str:
    """sh-c launcher that pipes one envelope to the resident daemon.

    Forwarded env vars (``EPHEMERALOS_GITIGNORE_BACKEND``) are still set so a
    daemon spawn issued from the same call sees them; the resident daemon
    inherits its env from its first spawn.
    """
    env_prefix = _runtime_env_prefix()
    return (
        f"{env_prefix}sh -c {shlex.quote(_RUNTIME_THIN_CLIENT_LAUNCHER)} runtime "
        f"{shlex.quote(raw_payload)}"
    )


def _runtime_daemon_spawn_command() -> str:
    """sh-c launcher that ensures the resident daemon is running.

    Idempotent: returns 0 immediately when an existing daemon's socket is
    bound and its PID is alive.
    """
    env_prefix = _runtime_env_prefix()
    return f"{env_prefix}sh -c {shlex.quote(_RUNTIME_DAEMON_LAUNCHER)}"


def _without_none(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None}


def _decode_response(stdout: str) -> dict[str, Any]:
    try:
        decoded = json.loads((stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise _RuntimeDispatchError(
            "BadRuntimeResponse",
            "runtime server returned invalid JSON",
            {"stdout": stdout},
        ) from exc
    if not isinstance(decoded, dict):
        raise _RuntimeDispatchError(
            "BadRuntimeResponse",
            "runtime server returned a non-object JSON response",
            {"response": decoded},
        )
    return decoded


def _raise_exec_failed(result: Any) -> None:
    exit_code = getattr(result, "exit_code", 1)
    raise _RuntimeDispatchError(
        kind="RuntimeExecFailed",
        message=str(getattr(result, "stderr", "") or getattr(result, "stdout", "")),
        details={"exit_code": exit_code},
    )


class RuntimeCommandClient:
    """Client for runtime operations executed through provider exec."""

    is_initialized: bool = False

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self.is_initialized = False
        self._init_lock = threading.Lock()

    def ensure_initialized(self, wait: bool = True) -> bool:
        del wait
        with self._init_lock:
            if self.is_initialized:
                return True
        run_sync(self._ensure_initialized_async())
        with self._init_lock:
            return self.is_initialized

    async def _ensure_initialized_async(self) -> None:
        await ensure_runtime_uploaded(self.sandbox_id)
        with self._init_lock:
            self.is_initialized = True

    def _call_sync(self, op: str, args: dict[str, Any] | None = None) -> Any:
        return run_sync(self._call_runtime_command(op, args or {}))

    async def _call_async(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        return await self._call_runtime_command(op, args or {}, timeout=timeout)

    async def _call_runtime_command(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        started = time.perf_counter()
        try:
            result = await self._call_runtime_once(
                op,
                args or {},
                timeout=timeout,
            )
            logger.debug(
                "sandbox runtime command done: op=%s elapsed=%.3fs retry=false",
                op,
                time.perf_counter() - started,
            )
            return result
        except (ConnectionRefusedError, BrokenPipeError, FileNotFoundError, OSError):
            logger.debug("sandbox runtime command retry after exec failure: op=%s", op)
            try:
                result = await self._call_runtime_once(
                    op,
                    args or {},
                    timeout=timeout,
                )
                logger.debug(
                    "sandbox runtime command done: op=%s elapsed=%.3fs retry=true",
                    op,
                    time.perf_counter() - started,
                )
                return result
            except (
                ConnectionRefusedError,
                BrokenPipeError,
                FileNotFoundError,
                OSError,
            ) as exc:
                raise ConnectionRefusedError(
                    f"runtime dispatcher unreachable after retry: {exc}"
                ) from exc

    async def _call_runtime_once(
        self,
        op: str,
        args: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        await ensure_runtime_uploaded(self.sandbox_id)
        try:
            response = await _call_runtime_server(
                exec_fn=get_adapter(self.sandbox_id).exec,
                sandbox_id=self.sandbox_id,
                op=op,
                args=args,
                timeout=max(1, int(timeout) + 5),
            )
        except _RuntimeDispatchError as exc:
            if exc.kind == "RuntimeExecFailed":
                raise ConnectionRefusedError(exc.message) from exc
            raise RuntimeCommandError(
                kind=exc.kind,
                message=exc.message,
                details=exc.details,
            ) from exc
        logger.debug(
            "sandbox runtime command_once: op=%s success=%s",
            op,
            response.get("success"),
        )
        return response

    def warmup(self) -> None:
        self.ensure_initialized(wait=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        del sandbox
        return None

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        del sandbox
        on_progress_line = kwargs.pop("on_progress_line", None)
        timeout = kwargs.get("timeout")
        command_timeout = float(timeout if timeout is not None else 600) + 30.0
        command_started = time.perf_counter()
        raw = await self._call_async(
            "shell",
            {
                "sandbox_id": self.sandbox_id,
                "workspace_root": self.workspace_root,
                "command": command,
                **kwargs,
            },
            timeout=command_timeout,
        )
        command_elapsed = round(time.perf_counter() - command_started, 6)
        result = _shell_result_namespace(raw or {})
        result.runtime_call_timings = {"total": command_elapsed}
        if on_progress_line is not None and result.result:
            on_progress_line(result.result)
        return result

    def dispose(self) -> None:
        return None


def _shell_result_namespace(raw: dict[str, Any]) -> SimpleNamespace:
    conflict = raw.get("conflict") if isinstance(raw.get("conflict"), dict) else {}
    conflict_reason = None
    conflict_file = None
    if conflict:
        conflict_reason = conflict.get("message") or conflict.get("reason")
        conflict_file = conflict.get("conflict_file")
    changed_paths = [
        str(path) for path in (raw.get("changed_paths") or ()) if str(path or "").strip()
    ]
    return SimpleNamespace(
        result=str(raw.get("result") or ""),
        exit_code=int(raw.get("exit_code") or 0),
        changed_paths=changed_paths,
        conflict_file=conflict_file,
        conflict_reason=conflict_reason,
        warnings=list(raw.get("warnings") or ()),
        overlay_run_timings=dict(raw.get("overlay_run_timings") or {}),
        overlay_stage_timings=dict(raw.get("overlay_stage_timings") or {}),
    )


__all__ = [
    "RuntimeCommandClient",
    "RuntimeCommandError",
]
