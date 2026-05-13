"""Provider-backed client for the bundled in-sandbox daemon dispatcher."""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Protocol

from sandbox.host.runtime_bundle import BUNDLE_REMOTE_DIR, bundle_hash
from sandbox.provider.registry import get_adapter

# Daemon launcher: ensures the resident daemon is running, then invokes a
# tiny AF_UNIX client that pipes one envelope and prints the response. The
# daemon is spawned via ``nohup`` once per sandbox; subsequent calls hit the
# already-warm process. Both the spawn and the per-call thin client are emitted
# through ``provider.exec``; Daytona stays inside the adapter.
_DAEMON_SOCKET = f"{BUNDLE_REMOTE_DIR}/runtime.sock"
_DAEMON_PID = f"{BUNDLE_REMOTE_DIR}/runtime.pid"
_DAEMON_LOG = f"{BUNDLE_REMOTE_DIR}/runtime.log"
_DAEMON_ENV = f"{BUNDLE_REMOTE_DIR}/runtime.env"
DEFAULT_LAYER_STACK_ROOT = f"{BUNDLE_REMOTE_DIR}/layer-stack"
# Explicit extension point for daemon-scoped feature flags. Keep empty unless
# a runtime setting must restart the daemon when the host value changes.
_FORWARDED_DAEMON_ENV: tuple[str, ...] = ()
_THIN_CLIENT_CONNECT_FAILED = 97
_THIN_CLIENT_IO_FAILED = 98

_DAEMON_THIN_CLIENT_PY = (
    "import socket,sys,os\n"
    f"CONNECT_FAILED={_THIN_CLIENT_CONNECT_FAILED};IO_FAILED={_THIN_CLIENT_IO_FAILED}\n"
    "timeout=float(os.environ.get('EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT','600'))\n"
    "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(timeout)\n"
    "try:\n"
    f" s.connect({_DAEMON_SOCKET!r})\n"
    "except (ConnectionRefusedError,FileNotFoundError) as e:\n"
    " sys.stderr.write('EOS_DAEMON_CONNECT_FAILED:'+e.__class__.__name__+'\\n');sys.exit(CONNECT_FAILED)\n"
    "except OSError as e:\n"
    " sys.stderr.write('EOS_DAEMON_CONNECT_FAILED:'+e.__class__.__name__+'\\n');sys.exit(CONNECT_FAILED)\n"
    "try:\n"
    " s.sendall(sys.argv[1].encode('utf-8')+b'\\n');s.shutdown(socket.SHUT_WR)\n"
    "except OSError as e:\n"
    " sys.stderr.write('EOS_DAEMON_IO_FAILED:'+e.__class__.__name__+'\\n');sys.exit(IO_FAILED)\n"
    "buf=b''\n"
    "try:\n"
    " while True:\n"
    "  chunk=s.recv(65536)\n"
    "  if not chunk: break\n"
    "  buf+=chunk\n"
    "except socket.timeout:\n"
    " sys.stderr.write('EOS_DAEMON_IO_FAILED:socket.timeout\\n');sys.exit(IO_FAILED)\n"
    "except OSError as e:\n"
    " sys.stderr.write('EOS_DAEMON_IO_FAILED:'+e.__class__.__name__+'\\n');sys.exit(IO_FAILED)\n"
    "sys.stdout.buffer.write(buf)\n"
)

_DAEMON_THIN_CLIENT_LAUNCHER = f"""\
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$py" -c {shlex.quote(_DAEMON_THIN_CLIENT_PY)} "$1"
    fi
done
echo 'sandbox daemon requires Python >= 3.10' >&2
exit 127
"""


class _DaemonDispatchError(RuntimeError):
    """Raised when daemon dispatch fails before typed decoding."""

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


class _DaemonReadinessError(_DaemonDispatchError):
    """Raised when a relaunched daemon does not become ready."""


class _DaemonExec(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any: ...


async def _call_daemon(
    *,
    exec_fn: _DaemonExec,
    sandbox_id: str,
    op: str,
    args: dict[str, Any],
    cwd: str = BUNDLE_REMOTE_DIR,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Dispatch one JSON envelope to the resident in-sandbox daemon."""
    raw_payload = json.dumps(
        {"op": op, "args": _without_none(args)},
        separators=(",", ":"),
    )
    result = await _exec_daemon_call(
        exec_fn=exec_fn,
        sandbox_id=sandbox_id,
        op=op,
        raw_payload=raw_payload,
        cwd=cwd,
        timeout=timeout,
    )
    try:
        response = _decode_response(getattr(result, "stdout", ""))
    except _DaemonDispatchError:
        if _exit_code(result) != 0:
            _raise_exec_failed(result)
        raise
    if "error" in response:
        error = response.get("error") or {}
        raise _DaemonDispatchError(
            kind=str(error.get("kind") or "RuntimeError"),
            message=str(error.get("message") or ""),
            details=error.get("details") if isinstance(error.get("details"), dict) else {},
        )
    if _exit_code(result) != 0:
        _raise_exec_failed(result)
    return response


async def call_daemon_api(
    sandbox_id: str,
    op: str,
    args: dict[str, Any],
    *,
    timeout: int = 60,
    layer_stack_root: str = DEFAULT_LAYER_STACK_ROOT,
) -> dict[str, Any]:
    """Call one guarded API operation inside the preinstalled daemon bundle."""
    daemon_args = {
        "layer_stack_root": layer_stack_root,
        **args,
    }
    return await _call_daemon(
        exec_fn=get_adapter(sandbox_id).exec,
        sandbox_id=sandbox_id,
        op=op,
        args=daemon_args,
        timeout=timeout,
    )


async def ensure_daemon_current(
    sandbox_id: str,
    *,
    timeout: int = 10,
) -> None:
    """Ensure the resident daemon is running for the current runtime bundle."""
    result = await get_adapter(sandbox_id).exec(
        sandbox_id,
        _daemon_spawn_command(),
        cwd=BUNDLE_REMOTE_DIR,
        timeout=timeout,
    )
    if _exit_code(result) != 0:
        _raise_exec_failed(result)


async def _exec_daemon_call(
    *,
    exec_fn: _DaemonExec,
    sandbox_id: str,
    op: str,
    raw_payload: str,
    cwd: str,
    timeout: int | None,
) -> Any:
    result = await exec_fn(
        sandbox_id,
        _daemon_thin_client_command(raw_payload),
        cwd=cwd,
        timeout=timeout,
    )
    if _should_retry_after_connect_failure(result, op):
        spawn_result = await exec_fn(
            sandbox_id,
            _daemon_spawn_command(),
            cwd=cwd,
            timeout=10,
        )
        if _exit_code(spawn_result) != 0:
            return spawn_result
        await _check_daemon_readiness_after_spawn(
            exec_fn=exec_fn,
            sandbox_id=sandbox_id,
            original_raw_payload=raw_payload,
            cwd=cwd,
        )
        result = await exec_fn(
            sandbox_id,
            _daemon_thin_client_command(raw_payload),
            cwd=cwd,
            timeout=timeout,
        )
    return result


def _should_retry_after_connect_failure(result: Any, op: str) -> bool:
    """Retry only when the thin client failed before sending the envelope."""
    del op
    return _exit_code(result) == _THIN_CLIENT_CONNECT_FAILED


async def _check_daemon_readiness_after_spawn(
    *,
    exec_fn: _DaemonExec,
    sandbox_id: str,
    original_raw_payload: str,
    cwd: str,
) -> None:
    original_op, readiness_payload = _readiness_request_for_original(
        original_raw_payload
    )
    result = await exec_fn(
        sandbox_id,
        _daemon_thin_client_command(readiness_payload),
        cwd=cwd,
        timeout=30,
    )
    if _exit_code(result) != 0:
        raise _DaemonReadinessError(
            kind="RuntimeReadinessFailed",
            message=str(getattr(result, "stderr", "") or getattr(result, "stdout", "")),
            details={"exit_code": _exit_code(result), "original_op": original_op},
        )
    try:
        response = _decode_response(getattr(result, "stdout", ""))
    except _DaemonDispatchError as exc:
        raise _DaemonReadinessError(
            kind="BadRuntimeReadinessResponse",
            message=exc.message,
            details={**exc.details, "original_op": original_op},
        ) from exc
    if "error" in response:
        error = response.get("error") or {}
        raise _DaemonReadinessError(
            kind=str(error.get("kind") or "RuntimeReadinessFailed"),
            message=str(error.get("message") or ""),
            details={
                **(
                    error.get("details")
                    if isinstance(error.get("details"), dict)
                    else {}
                ),
                "original_op": original_op,
            },
        )
    if response.get("ready") is not True and not _is_bootstrap_ready_response(
        original_op,
        response,
    ):
        raise _DaemonReadinessError(
            kind="RuntimeNotReady",
            message="daemon readiness check failed",
            details={"response": response, "original_op": original_op},
        )


def _readiness_request_for_original(raw_payload: str) -> tuple[str, str]:
    try:
        envelope = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise _DaemonReadinessError(
            kind="BadRuntimeRequest",
            message="cannot derive readiness request from invalid daemon payload",
            details={"error": str(exc)},
        ) from exc
    op = envelope.get("op") if isinstance(envelope, dict) else None
    args = envelope.get("args") if isinstance(envelope, dict) else None
    layer_stack_root = args.get("layer_stack_root") if isinstance(args, dict) else None
    if not str(layer_stack_root or "").strip():
        raise _DaemonReadinessError(
            kind="MissingLayerStackRoot",
            message="daemon readiness check requires layer_stack_root",
            details={"op": op},
        )
    return (
        str(op or ""),
        json.dumps(
            {
                "op": "api.runtime.ready",
                "args": {"layer_stack_root": str(layer_stack_root)},
            },
            separators=(",", ":"),
        ),
    )


def _is_bootstrap_ready_response(
    original_op: str,
    response: dict[str, Any],
) -> bool:
    if original_op not in {"api.ensure_workspace_base", "api.build_workspace_base"}:
        return False
    probes = response.get("probes")
    if not isinstance(probes, list):
        return False
    by_name = {
        str(probe.get("name")): probe
        for probe in probes
        if isinstance(probe, dict)
    }
    control_plane = by_name.get("control_plane")
    if not isinstance(control_plane, dict):
        return False
    details = control_plane.get("details")
    if not isinstance(details, dict):
        return False
    if (
        control_plane.get("status") != "down"
        or details.get("error_type") != "WorkspaceBindingError"
    ):
        return False
    return all(
        isinstance(probe, dict) and probe.get("status") == "ok"
        for name, probe in by_name.items()
        if name != "control_plane"
    )


def _daemon_thin_client_command(raw_payload: str) -> str:
    """sh-c launcher that pipes one envelope to the resident daemon."""
    return (
        f"sh -c {shlex.quote(_DAEMON_THIN_CLIENT_LAUNCHER)} daemon "
        f"{shlex.quote(raw_payload)}"
    )


def _daemon_spawn_command() -> str:
    """sh-c launcher that ensures the resident daemon is running.

    Idempotent: returns 0 immediately when an existing daemon's socket is
    bound and its PID is alive.
    """
    return f"sh -c {shlex.quote(_daemon_launcher())}"


def _daemon_launcher() -> str:
    env_signature = _daemon_env_signature()
    return f"""\
set -e
{_daemon_env_exports()}SOCK={shlex.quote(_DAEMON_SOCKET)}
PID={shlex.quote(_DAEMON_PID)}
LOG={shlex.quote(_DAEMON_LOG)}
ENV_FILE={shlex.quote(_DAEMON_ENV)}
ENV_SIG={shlex.quote(env_signature)}
mkdir -p {shlex.quote(BUNDLE_REMOTE_DIR)}
if [ -S "$SOCK" ] && [ -f "$PID" ] && kill -0 "$(cat "$PID" 2>/dev/null)" 2>/dev/null; then
    if [ -f "$ENV_FILE" ] && [ "$(cat "$ENV_FILE")" = "$ENV_SIG" ]; then
        exit 0
    fi
    kill "$(cat "$PID" 2>/dev/null)" 2>/dev/null || true
fi
rm -f "$SOCK"
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        nohup "$py" -m sandbox.runtime.daemon --socket "$SOCK" --pid-file "$PID" </dev/null >"$LOG" 2>&1 &
        printf '%s' "$ENV_SIG" > "$ENV_FILE"
        # Wait briefly for the socket to appear so the next client connect succeeds.
        for _ in $(seq 1 50); do
            [ -S "$SOCK" ] && exit 0
            sleep 0.05
        done
        echo 'sandbox daemon failed to bind socket within 2.5s' >&2
        exit 1
    fi
done
echo 'sandbox daemon requires Python >= 3.10' >&2
exit 127
"""


def _daemon_env_exports() -> str:
    exports: list[str] = []
    for name in _FORWARDED_DAEMON_ENV:
        value = os.getenv(name)
        if value is not None:
            exports.append(f"export {name}={shlex.quote(value)}")
    if not exports:
        return ""
    return "\n".join(exports) + "\n"


def _daemon_env_signature() -> str:
    parts: list[str] = [f"runtime_bundle_sha={bundle_hash()}"]
    for name in _FORWARDED_DAEMON_ENV:
        value = os.getenv(name)
        if value is not None:
            parts.append(f"{name}={value}")
    return "\n".join(parts)


def _without_none(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None}


def _decode_response(stdout: str) -> dict[str, Any]:
    try:
        decoded = json.loads((stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise _DaemonDispatchError(
            "BadRuntimeResponse",
            "daemon returned invalid JSON",
            {"stdout": stdout},
        ) from exc
    if not isinstance(decoded, dict):
        raise _DaemonDispatchError(
            "BadRuntimeResponse",
            "daemon returned a non-object JSON response",
            {"response": decoded},
        )
    return decoded


def _raise_exec_failed(result: Any) -> None:
    exit_code = _exit_code(result)
    raise _DaemonDispatchError(
        kind="RuntimeExecFailed",
        message=str(getattr(result, "stderr", "") or getattr(result, "stdout", "")),
        details={"exit_code": exit_code},
    )


def _exit_code(result: Any) -> int:
    raw = getattr(result, "exit_code", None)
    if raw is None:
        raise _DaemonDispatchError(
            kind="BadExecResult",
            message="provider exec result is missing exit_code",
            details={"result_type": type(result).__name__},
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise _DaemonDispatchError(
            kind="BadExecResult",
            message=f"provider exec result has invalid exit_code: {raw!r}",
            details={"result_type": type(result).__name__},
        ) from exc


__all__: list[str] = []
