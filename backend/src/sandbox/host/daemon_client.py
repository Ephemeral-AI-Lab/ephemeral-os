"""Provider-backed client for the bundled in-sandbox daemon dispatcher."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from collections.abc import Mapping
from typing import Any, Protocol

from sandbox.daemon.paths import (
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

logger = logging.getLogger(__name__)

# Daemon spawned once per sandbox via provider.exec; subsequent calls hit the
# warm process via an AF_UNIX thin client (one envelope per call).
_DAEMON_SOCKET = DAEMON_SOCKET_PATH
_DAEMON_PID = DAEMON_PID_PATH
_DAEMON_LOG = DAEMON_LOG_PATH
_DAEMON_ENV = DAEMON_ENV_SIGNATURE_PATH
_PYTHON_CANDIDATES = ("python3.13", "python3.12", "python3.11", "python3.10", "python3")
_THIN_CLIENT_CONNECT_FAILED = 97
_THIN_CLIENT_IO_FAILED = 98
_DAEMON_SPAWN_TIMEOUT = 20
# Bounded retry on CONNECT_FAILED: under parallel agent load the daemon's
# accept queue can transiently refuse new connections immediately after spawn.
# These delays give the daemon time to bind/accept before declaring readiness
# a hard failure. Total worst-case added latency: ~3.5s before raising.
_CONNECT_RETRY_DELAYS_S: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0)
DAEMON_PROTOCOL_VERSION = 1
DAEMON_PROTOCOL_FIELD = "_eos_daemon_protocol_version"


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
    result = await _dispatch_once_with_retry(
        exec_fn=exec_fn,
        sandbox_id=sandbox_id,
        op=op,
        args=_without_none(args),
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


def versioned_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Attach the daemon protocol version while preserving caller payloads."""
    return {
        DAEMON_PROTOCOL_FIELD: DAEMON_PROTOCOL_VERSION,
        **dict(payload),
    }


async def ensure_daemon_current(
    sandbox_id: str,
    *,
    timeout: int = _DAEMON_SPAWN_TIMEOUT,
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


async def _dispatch_once_with_retry(
    *,
    exec_fn: _DaemonExec,
    sandbox_id: str,
    op: str,
    args: dict[str, Any],
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
    if _exit_code(result) != _THIN_CLIENT_CONNECT_FAILED:
        return result

    spawn_result = await exec_fn(
        sandbox_id,
        _daemon_spawn_command(),
        cwd=cwd,
        timeout=_DAEMON_SPAWN_TIMEOUT,
    )
    if _exit_code(spawn_result) != 0:
        return spawn_result

    layer_stack_root = args.get("layer_stack_root")
    if not str(layer_stack_root or "").strip():
        raise _DaemonReadinessError(
            kind="MissingLayerStackRoot",
            message="daemon readiness check requires layer_stack_root",
            details={"op": op},
        )

    readiness_payload = json.dumps(
        {
            "op": "api.runtime.ready",
            "args": {"layer_stack_root": str(layer_stack_root)},
        },
        separators=(",", ":"),
    )
    readiness_result = await _call_thin_client_with_connect_retry(
        exec_fn=exec_fn,
        sandbox_id=sandbox_id,
        payload=readiness_payload,
        cwd=cwd,
        timeout=30,
    )
    if _exit_code(readiness_result) != 0:
        raise _DaemonReadinessError(
            kind="RuntimeReadinessFailed",
            message=str(
                getattr(readiness_result, "stderr", "")
                or getattr(readiness_result, "stdout", "")
            ),
            details={"exit_code": _exit_code(readiness_result), "original_op": op},
        )
    try:
        response = _decode_response(getattr(readiness_result, "stdout", ""))
    except _DaemonDispatchError as exc:
        raise _DaemonReadinessError(
            kind="BadRuntimeReadinessResponse",
            message=exc.message,
            details={**exc.details, "original_op": op},
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
                "original_op": op,
            },
        )
    if response.get("ready") is not True:
        if _is_bootstrap_ready_response(op, response):
            logger.warning(
                "daemon-readiness: declaring %s ready despite control_plane "
                "WorkspaceBindingError; original op will retry against an "
                "unbound workspace and its own error path will surface the "
                "binding failure if it persists",
                op,
            )
        else:
            raise _DaemonReadinessError(
                kind="RuntimeNotReady",
                message="daemon readiness check failed",
                details={"response": response, "original_op": op},
            )

    return await _call_thin_client_with_connect_retry(
        exec_fn=exec_fn,
        sandbox_id=sandbox_id,
        payload=raw_payload,
        cwd=cwd,
        timeout=timeout,
    )


async def _call_thin_client_with_connect_retry(
    *,
    exec_fn: _DaemonExec,
    sandbox_id: str,
    payload: str,
    cwd: str,
    timeout: int | None,
) -> Any:
    """Dispatch one envelope, retrying transient CONNECT_FAILED responses.

    The in-sandbox daemon's accept queue can transiently refuse connections
    immediately after spawn, or while many parallel agent runs land on the
    socket at once. A bounded backoff retry absorbs that without surfacing a
    user-visible tool failure.
    """
    last_result: Any = None
    for delay in _CONNECT_RETRY_DELAYS_S:
        last_result = await exec_fn(
            sandbox_id,
            _daemon_thin_client_command(payload),
            cwd=cwd,
            timeout=timeout,
        )
        if _exit_code(last_result) != _THIN_CLIENT_CONNECT_FAILED:
            return last_result
        await asyncio.sleep(delay)
    return await exec_fn(
        sandbox_id,
        _daemon_thin_client_command(payload),
        cwd=cwd,
        timeout=timeout,
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
    """Launch the bundled daemon supervisor. Idempotent: returns 0 when
    an existing daemon's socket is bound and its PID is alive."""
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
            "sandbox.daemon",
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
