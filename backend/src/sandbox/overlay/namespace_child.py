"""Helper executed inside a private mount namespace.

This is command-exec's workspace replacement helper. The older
``sandbox.overlay.namespace`` path is for snapshot-overlay requests and stays
separate because command-exec must capture an upperdir for OCC submission.

Mount mechanics live in ``sandbox.overlay.kernel_mount``; this
module owns payload parsing, error reporting, and the sequencing around
``run_command_to_refs``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox._shared.models import Intent, ToolCallRequest
from sandbox._shared.env_policy import (
    CommandExecPolicy,
)
from sandbox._shared.tool_primitives import VERB_TABLE, shell
from sandbox.overlay.kernel_mount import (
    MountInputs,
    mount_overlay,
    umount,
    validate_mount_inputs,
)
from sandbox.overlay.namespace import (
    NAMESPACE_INFRA_EXIT_CODE,
)
from sandbox.overlay.subprocess_runner import (
    child_cpu_times,
    record_child_cpu_delta,
    run_command_to_refs,
)
from sandbox._shared.clock import monotonic_now


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("namespace helper requires one JSON payload path\n")
        return 2
    payload = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        sys.stderr.write("namespace helper payload must be an object\n")
        return 2
    return execute(payload)


def execute(payload: dict[str, Any]) -> int:
    if "tool_call" in payload:
        return _execute_tool_call_payload(payload)

    timings: dict[str, float] = {}
    try:
        request = _payload_request(payload)
        request.stdout_ref.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_ref.parent.mkdir(parents=True, exist_ok=True)
    except KeyError as exc:
        return _fail_bad_payload(
            payload,
            timings,
            f"missing payload key: {exc.args[0]}",
        )
    except Exception as exc:
        return _fail_bad_payload(payload, timings, str(exc))

    mount_inputs: MountInputs | None = None
    try:
        mount_inputs = validate_mount_inputs(
            workspace_root=request.workspace_root,
            layer_paths=request.layer_paths,
            upperdir=request.upperdir,
            workdir=request.workdir,
            policy=request.policy,
        )
        mount_start = monotonic_now()
        mount_overlay(
            workspace_root=mount_inputs.workspace_root,
            layer_paths=mount_inputs.layer_paths,
            upperdir=mount_inputs.upperdir,
            workdir=mount_inputs.workdir,
            pass_fds=mount_inputs.fds,
        )
        timings["command_exec.mount_workspace_s"] = monotonic_now() - mount_start
    except subprocess.CalledProcessError as exc:
        stderr = str(exc.stderr or "").strip()
        stdout = str(exc.stdout or "").strip()
        detail = stderr or stdout
        message = f"{exc}; {detail}" if detail else str(exc)
        return _fail(request, timings, "mount_failed", message, recoverable=True)
    except ValueError as exc:
        return _fail(request, timings, "validation_failed", str(exc))
    except OSError as exc:
        return _fail(request, timings, "setup_failed", str(exc))
    except Exception as exc:
        return _fail(request, timings, "unexpected_setup_failed", str(exc))
    finally:
        if mount_inputs is not None:
            mount_inputs.close()

    try:
        run_start = monotonic_now()
        cpu_start = child_cpu_times()
        env_raw = payload.get("env") or {}
        env = (
            {str(key): str(value) for key, value in env_raw.items()}
            if isinstance(env_raw, dict)
            else {}
        )
        timeout_raw = payload.get("timeout_seconds")
        timeout = float(timeout_raw) if timeout_raw is not None else None
        exit_code = run_command_to_refs(
            command=[str(part) for part in payload["command"]],
            declared_workspace_root=request.workspace_root,
            mounted_workspace_root=request.workspace_root,
            cwd=str(payload.get("cwd") or "."),
            env=env,
            timeout_seconds=timeout,
            stdout_ref=request.stdout_ref,
            stderr_ref=request.stderr_ref,
            policy=request.policy,
        )
        timings["command_exec.run_command_s"] = monotonic_now() - run_start
        record_child_cpu_delta(timings, cpu_start)
        return exit_code
    except Exception as exc:
        with request.stderr_ref.open("ab") as stderr_file:
            stderr_file.write(
                _json_error_line("command_failed", str(exc)).encode()
            )
        return 126
    finally:
        umount(request.workspace_root)
        _write_timings(request.timings_ref, timings)


@dataclass(frozen=True)
class _NamespaceRequest:
    workspace_root: Path
    layer_paths: tuple[Path, ...]
    upperdir: Path
    workdir: Path
    stdout_ref: Path
    stderr_ref: Path
    timings_ref: Path
    control_ref: Path | None
    policy: CommandExecPolicy


def _payload_request(payload: dict[str, Any]) -> _NamespaceRequest:
    raw_layers = payload["layer_paths"]
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ValueError(
            f"layer_paths must be a non-empty list; got {raw_layers!r}"
        )
    layer_paths = tuple(Path(str(p)) for p in raw_layers)
    return _NamespaceRequest(
        workspace_root=Path(str(payload["workspace_root"])),
        layer_paths=layer_paths,
        upperdir=Path(str(payload["upperdir"])),
        workdir=Path(str(payload["workdir"])),
        stdout_ref=Path(str(payload["stdout_ref"])),
        stderr_ref=Path(str(payload["stderr_ref"])),
        timings_ref=Path(str(payload["timings_ref"])),
        control_ref=(
            Path(str(payload["control_ref"]))
            if payload.get("control_ref")
            else None
        ),
        policy=CommandExecPolicy.from_payload(
            payload["policy"] if isinstance(payload.get("policy"), dict) else {}
        ),
    )


def _write_error(path: Path, error_kind: str, detail: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_error_line(error_kind, detail), encoding="utf-8")


def _json_error_line(error_kind: str, detail: str) -> str:
    return json.dumps(
        {"error_kind": error_kind, "detail": detail},
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _write_timings(path: Path, timings: dict[str, float]) -> None:
    path.write_text(
        json.dumps(timings, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _fail_bad_payload(
    payload: dict[str, Any],
    timings: dict[str, float],
    detail: str,
) -> int:
    try:
        stderr_ref = _resolve_fallback_ref(payload, "stderr_ref")
        timings_ref = _resolve_fallback_ref(payload, "timings_ref")
    except ValueError as exc:
        sys.stderr.write(f"bad namespace helper payload: {detail}; {exc}\n")
        return 2
    _write_error(stderr_ref, "bad_payload", detail)
    _write_timings(timings_ref, timings)
    return 126


def _resolve_fallback_ref(payload: dict[str, Any], key: str) -> Path:
    raw = payload.get(key)
    if not raw:
        raise ValueError(f"payload is missing {key}; no fallback ref available")
    path = Path(str(raw))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fail(
    request: _NamespaceRequest,
    timings: dict[str, float],
    error_kind: str,
    detail: str,
    *,
    recoverable: bool = False,
) -> int:
    _write_error(request.stderr_ref, error_kind, detail)
    if recoverable and request.control_ref is not None:
        request.control_ref.parent.mkdir(parents=True, exist_ok=True)
        request.control_ref.write_text(
            json.dumps(
                {
                    "detail": detail,
                    "error_kind": error_kind,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    _write_timings(request.timings_ref, timings)
    return NAMESPACE_INFRA_EXIT_CODE if recoverable else 126


_HOST_DENYLIST_PREFIXES = ("/etc/", "/var/", "/proc/", "/sys/", "/boot/")


def _execute_tool_call_payload(payload: dict[str, Any]) -> int:
    timings: dict[str, float] = {}
    result_ref = Path(str(payload["result_ref"]))
    mount_inputs: MountInputs | None = None
    request: _NamespaceRequest | None = None
    try:
        request = _payload_request(payload)
        if payload.get("mount_overlay", True):
            mount_inputs = validate_mount_inputs(
                workspace_root=request.workspace_root,
                layer_paths=request.layer_paths,
                upperdir=request.upperdir,
                workdir=request.workdir,
                policy=request.policy,
            )
            mount_start = monotonic_now()
            mount_overlay(
                workspace_root=mount_inputs.workspace_root,
                layer_paths=mount_inputs.layer_paths,
                upperdir=mount_inputs.upperdir,
                workdir=mount_inputs.workdir,
                pass_fds=mount_inputs.fds,
            )
            timings["workspace.mount_s"] = monotonic_now() - mount_start
        result = execute_tool_payload(payload, timings=timings)
        result_ref.parent.mkdir(parents=True, exist_ok=True)
        result_ref.write_text(
            json.dumps(result, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        result_ref.parent.mkdir(parents=True, exist_ok=True)
        result_ref.write_text(
            json.dumps(
                {
                    "success": False,
                    "status": "error",
                    "error": {
                        "kind": type(exc).__name__,
                        "message": str(exc),
                    },
                    "timings": timings,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0
    finally:
        if mount_inputs is not None:
            mount_inputs.close()
        if request is not None and payload.get("mount_overlay", True):
            umount(request.workspace_root)
            _write_timings(request.timings_ref, timings)


def execute_tool_payload(
    payload: dict[str, Any],
    *,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    req = ToolCallRequest.from_payload(payload["tool_call"])
    old_cwd = os.getcwd()
    try:
        os.chdir(str(payload.get("workspace_root") or "/testbed"))
        denied = _check_host_denylist(req)
        if denied is not None:
            return denied
        run_start = monotonic_now()
        if req.verb == "shell":
            result = shell.run(
                _shell_argv(req.args),
                workspace_root=str(payload.get("workspace_root") or "/testbed"),
                cwd=str(req.args.get("cwd") or "."),
                env=_string_mapping(req.args.get("env")),
                timeout_seconds=_optional_float(
                    req.args.get("timeout_seconds", req.args.get("timeout"))
                ),
                stdout_ref=Path(str(payload["stdout_ref"])),
                stderr_ref=Path(str(payload["stderr_ref"])),
                policy=CommandExecPolicy.from_payload(
                    payload["policy"] if isinstance(payload.get("policy"), dict) else {}
                ),
            )
            result_payload = _jsonable_result(result)
        else:
            compute = VERB_TABLE[req.verb]
            result_payload = _jsonable_result(compute(req.args))
    finally:
        os.chdir(old_cwd)
    elapsed = monotonic_now() - run_start
    result_payload.setdefault("success", True)
    result_payload.setdefault("status", "ok" if result_payload.get("success") else "error")
    result_payload.setdefault("workspace", "ephemeral")
    result_payload.setdefault("timings", {})
    if isinstance(result_payload["timings"], dict):
        result_payload["timings"]["workspace.tool_s"] = elapsed
        if timings:
            result_payload["timings"].update(timings)
    return result_payload


def _check_host_denylist(req: ToolCallRequest) -> dict[str, Any] | None:
    if req.intent != Intent.WRITE_ALLOWED and req.verb not in {
        "write_file",
        "edit_file",
        "shell",
    }:
        return None
    target = str(req.args.get("path") or req.args.get("cwd") or "")
    if not target:
        return None
    if any(target == prefix.rstrip("/") or target.startswith(prefix) for prefix in _HOST_DENYLIST_PREFIXES):
        return {
            "success": False,
            "status": "error",
            "error": {
                "kind": "forbidden_host_path",
                "path": target,
                "message": "writes to system paths are denied inside the namespace child",
            },
            "changed_paths": [],
            "timings": {},
        }
    return None


def _shell_argv(args: Any) -> list[str]:
    if not isinstance(args, dict):
        raise ValueError("shell args must be an object")
    command = args.get("command")
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        return ["bash", "-lc", command]
    raise ValueError("command must be a string or argv list")


def _string_mapping(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    return float(raw)


def _jsonable_result(value: Any) -> dict[str, Any]:
    from sandbox.overlay.namespace import jsonable_result

    return jsonable_result(value)


__all__ = [
    "execute",
    "execute_tool_payload",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
