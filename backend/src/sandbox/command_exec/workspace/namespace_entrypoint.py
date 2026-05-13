"""Helper executed inside a private mount namespace."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox.command_exec.workspace.environment import run_command_to_refs
from sandbox.timing import monotonic_now


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
    timings: dict[str, float] = {}
    try:
        request = _payload_request(payload)
        request.stdout_ref.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_ref.parent.mkdir(parents=True, exist_ok=True)
    except KeyError as exc:
        stderr_ref = _fallback_ref(payload, "stderr_ref")
        timings_ref = _fallback_ref(payload, "timings_ref")
        _write_error(stderr_ref, "bad_payload", f"missing payload key: {exc.args[0]}")
        _write_timings(timings_ref, timings)
        return 126
    except Exception as exc:
        stderr_ref = _fallback_ref(payload, "stderr_ref")
        timings_ref = _fallback_ref(payload, "timings_ref")
        _write_error(stderr_ref, "bad_payload", str(exc))
        _write_timings(timings_ref, timings)
        return 126

    mount_inputs: _MountInputs | None = None
    try:
        mount_inputs = _validate_mount_inputs(
            workspace_root=request.workspace_root,
            lowerdir=request.lowerdir,
            upperdir=request.upperdir,
            workdir=request.workdir,
        )
        request.upperdir.mkdir(parents=True, exist_ok=True)
        request.workdir.mkdir(parents=True, exist_ok=True)
        mount_start = monotonic_now()
        _mount_overlay(
            workspace_root=mount_inputs.workspace_root,
            lowerdir=mount_inputs.lowerdir,
            upperdir=mount_inputs.upperdir,
            workdir=mount_inputs.workdir,
        )
        timings["command_exec.mount_workspace_s"] = monotonic_now() - mount_start
    except subprocess.CalledProcessError as exc:
        message = _called_process_message(exc)
        _write_error(request.stderr_ref, "mount_failed", message)
        _write_timings(request.timings_ref, timings)
        return 126
    except ValueError as exc:
        _write_error(request.stderr_ref, "validation_failed", str(exc))
        _write_timings(request.timings_ref, timings)
        return 126
    except OSError as exc:
        _write_error(request.stderr_ref, "setup_failed", str(exc))
        _write_timings(request.timings_ref, timings)
        return 126
    except Exception as exc:
        _write_error(request.stderr_ref, "unexpected_setup_failed", str(exc))
        _write_timings(request.timings_ref, timings)
        return 126
    finally:
        if mount_inputs is not None:
            mount_inputs.close()

    try:
        run_start = monotonic_now()
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
        )
        timings["command_exec.run_command_s"] = monotonic_now() - run_start
        return exit_code
    except Exception as exc:
        with request.stderr_ref.open("ab") as stderr_file:
            stderr_file.write(
                _json_error_line("command_failed", str(exc)).encode()
            )
        return 126
    finally:
        _umount(request.workspace_root)
        _write_timings(request.timings_ref, timings)


@dataclass(frozen=True)
class _NamespaceRequest:
    workspace_root: Path
    lowerdir: Path
    upperdir: Path
    workdir: Path
    stdout_ref: Path
    stderr_ref: Path
    timings_ref: Path


@dataclass(frozen=True)
class _MountInputs:
    workspace_root: Path
    lowerdir: Path
    upperdir: Path
    workdir: Path
    fds: tuple[int, ...]

    def close(self) -> None:
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _payload_request(payload: dict[str, Any]) -> _NamespaceRequest:
    return _NamespaceRequest(
        workspace_root=Path(str(payload["workspace_root"])),
        lowerdir=Path(str(payload["lowerdir"])),
        upperdir=Path(str(payload["upperdir"])),
        workdir=Path(str(payload["workdir"])),
        stdout_ref=Path(str(payload["stdout_ref"])),
        stderr_ref=Path(str(payload["stderr_ref"])),
        timings_ref=Path(str(payload["timings_ref"])),
    )


def _mount_overlay(
    *,
    workspace_root: Path,
    lowerdir: Path,
    upperdir: Path,
    workdir: Path,
) -> None:
    options = f"lowerdir={lowerdir},upperdir={upperdir},workdir={workdir}"
    subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o", options, str(workspace_root)],
        check=True,
        capture_output=True,
        text=True,
    )


def _umount(workspace_root: Path) -> None:
    subprocess.run(
        ["umount", str(workspace_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


# Characters that, if present in an overlay mount-option value, would split
# or terminate the option string and let a caller inject extra options.
# ``,`` separates options; ``:`` separates lowerdir entries; backslash is
# the kernel's escape character; whitespace can mangle the option parser.
# NUL (\0) terminates any C string the option may be passed through.
_FORBIDDEN_OVERLAY_PATH_CHARS = (",", ":", "\\", "\n", "\r", "\t", "\0")


def _validate_mount_inputs(
    *,
    workspace_root: Path,
    lowerdir: Path,
    upperdir: Path,
    workdir: Path,
) -> _MountInputs:
    fds: list[int] = []
    try:
        for path, label in (
            (workspace_root, "workspace root"),
            (lowerdir, "leased lowerdir"),
        ):
            if path.is_symlink():
                raise ValueError(f"{label} must not be a symlink: {path}")
            if not path.is_dir():
                raise ValueError(f"{label} is missing: {path}")
            fds.append(_open_dir_no_follow(path))
        for path in (upperdir, workdir):
            if path.is_symlink():
                raise ValueError(f"mount scratch dir must not be a symlink: {path}")
            if path.exists() and not path.is_dir():
                raise ValueError(f"mount scratch path is not a directory: {path}")
        _assert_same_dir(workspace_root, fds[0])
        _assert_same_dir(lowerdir, fds[1])
    except Exception:
        for fd in fds:
            os.close(fd)
        raise
    for path in (workspace_root, lowerdir, upperdir, workdir):
        text = path.as_posix()
        for bad in _FORBIDDEN_OVERLAY_PATH_CHARS:
            if bad in text:
                # NUL renders as garbage in messages; describe instead.
                label = repr(bad)
                raise ValueError(
                    f"overlay mount path cannot contain {label}: {path!r}"
                )
    return _MountInputs(
        workspace_root=workspace_root,
        lowerdir=lowerdir,
        upperdir=upperdir,
        workdir=workdir,
        fds=tuple(fds),
    )


def _open_dir_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _assert_same_dir(path: Path, fd: int) -> None:
    before = os.fstat(fd)
    after = path.stat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError(f"mount input changed during validation: {path}")


def _fallback_ref(payload: dict[str, Any], key: str) -> Path:
    raw = payload.get(key)
    if raw:
        path = Path(str(raw))
    else:
        path = Path("/tmp") / f"namespace-entrypoint-{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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


def _called_process_message(exc: subprocess.CalledProcessError) -> str:
    stderr = str(exc.stderr or "").strip()
    stdout = str(exc.stdout or "").strip()
    detail = stderr or stdout
    if detail:
        return f"{exc}; {detail}"
    return str(exc)


__all__ = [
    "execute",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
