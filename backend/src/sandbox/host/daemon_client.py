"""Provider-backed client for the bundled in-sandbox daemon dispatcher."""

from __future__ import annotations

import json
import shlex
from typing import Any, Protocol

from sandbox.daemon_paths import (
    BUNDLE_REMOTE_DIR,
    DAEMON_ENV_SIGNATURE_PATH,
    DAEMON_LAUNCH_SCRIPT_PATH,
    DAEMON_LOG_PATH,
    DAEMON_PID_PATH,
    DAEMON_SOCKET_PATH,
    DAEMON_THIN_CLIENT_PATH,
    DEFAULT_LAYER_STACK_ROOT,
)
from sandbox.host.runtime_bundle import bundle_hash
from sandbox.provider.registry import get_adapter

# Daemon launcher: ensures the resident daemon is running, then invokes a
# tiny AF_UNIX client that pipes one envelope and prints the response. The
# daemon is spawned via ``nohup`` once per sandbox; subsequent calls hit the
# already-warm process. Both the spawn and the per-call thin client are emitted
# through ``provider.exec``; Daytona stays inside the adapter.
_DAEMON_SOCKET = DAEMON_SOCKET_PATH
_DAEMON_PID = DAEMON_PID_PATH
_DAEMON_LOG = DAEMON_LOG_PATH
_DAEMON_ENV = DAEMON_ENV_SIGNATURE_PATH
_PYTHON_CANDIDATES = ("python3.13", "python3.12", "python3.11", "python3.10", "python3")
_THIN_CLIENT_CONNECT_FAILED = 97
_THIN_CLIENT_IO_FAILED = 98


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
    if _should_retry_after_connect_failure(result):
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


def _should_retry_after_connect_failure(result: Any) -> bool:
    """Retry only when the thin client failed before sending the envelope."""
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
    """Launch the bundled thin client with one daemon envelope."""
    return (
        f"sh -c {shlex.quote(_thin_client_python_launcher())} daemon "
        f"{shlex.quote(_python_candidates_arg())} "
        f"{shlex.quote(DAEMON_THIN_CLIENT_PATH)} "
        f"{shlex.quote(_DAEMON_SOCKET)} "
        f"{shlex.quote(raw_payload)}"
    )


def _daemon_spawn_command() -> str:
    """Launch the bundled daemon supervisor script.

    Idempotent: returns 0 immediately when an existing daemon's socket is
    bound and its PID is alive.
    """
    return " ".join(
        shlex.quote(part)
        for part in (
            "sh",
            DAEMON_LAUNCH_SCRIPT_PATH,
            _python_candidates_arg(),
            _DAEMON_SOCKET,
            _DAEMON_PID,
            _DAEMON_LOG,
            _DAEMON_ENV,
            _daemon_env_signature(),
            "sandbox.runtime.daemon",
        )
    )


def _thin_client_python_launcher() -> str:
    return """\
candidates=$1
script=$2
socket_path=$3
payload=$4
for py in $candidates; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$py" "$script" "$socket_path" "$payload"
    fi
done
echo 'sandbox daemon requires Python >= 3.10' >&2
exit 127
"""


def _daemon_env_signature() -> str:
    return f"runtime_bundle_sha={bundle_hash()}"


def _python_candidates_arg() -> str:
    return " ".join(_PYTHON_CANDIDATES)


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
