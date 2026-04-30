"""Remote file I/O via ``sandbox.process.exec`` for the Daytona transport."""

from __future__ import annotations

import base64
import inspect
import json
import logging
import shlex
import uuid
from typing import Any

from sandbox.daytona.bash import _wrap_bash_command, _extract_exit_code

logger = logging.getLogger(__name__)

_REMOTE_WRITE_CHUNK_BYTES = 24 * 1024


def _supports_exec_transport(sandbox: Any) -> bool:
    process = getattr(sandbox, "process", None)
    exec_fn = getattr(process, "exec", None) if process is not None else None
    return callable(exec_fn)


async def _exec_command(sandbox: Any, command: str, *, timeout: int | None = None) -> Any:
    process = getattr(sandbox, "process", None)
    exec_fn = getattr(process, "exec", None) if process is not None else None
    if not callable(exec_fn):
        raise RuntimeError("Sandbox process has no exec method")
    if not inspect.iscoroutinefunction(exec_fn):
        raise RuntimeError("Sandbox process.exec must be async")
    return await exec_fn(command, timeout=timeout) if timeout is not None else await exec_fn(command)


def _build_read_text_file_command(file_path: str) -> str:
    script = """
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    content = path.read_text(encoding="utf-8")
except FileNotFoundError:
    print(json.dumps({"exists": False}))
else:
    print(json.dumps({"exists": True, "content": content}))
"""
    return (
        f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"
    )


def _build_write_text_file_command(file_path: str, content: str) -> str:
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = """
import base64
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(base64.b64decode(sys.argv[2]).decode("utf-8"), encoding="utf-8")
"""
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(file_path)} {shlex.quote(payload)}"
    )


def _build_truncate_text_file_command(file_path: str) -> str:
    script = """
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(b"")
"""
    return f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"


def _build_append_text_file_chunk_command(file_path: str, payload: str) -> str:
    script = """
import base64
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
with path.open("ab") as handle:
    handle.write(base64.b64decode(sys.argv[2]))
"""
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(file_path)} {shlex.quote(payload)}"
    )


def _build_replace_file_command(tmp_path: str, file_path: str) -> str:
    script = """
import os
import pathlib
import sys

tmp = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
os.replace(tmp, dst)
"""
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(tmp_path)} {shlex.quote(file_path)}"
    )


def _build_remove_file_command(file_path: str) -> str:
    return f"rm -f {shlex.quote(file_path)}"


def _build_write_text_file_commands(
    file_path: str,
    content: str,
    *,
    chunk_bytes: int = _REMOTE_WRITE_CHUNK_BYTES,
) -> tuple[list[str], str | None]:
    """Build remote write commands for small or large files."""
    data = content.encode("utf-8")
    if len(data) <= chunk_bytes:
        return [_build_write_text_file_command(file_path, content)], None

    tmp_path = f"{file_path}.codex-write-{uuid.uuid4().hex}.tmp"
    commands = [_build_truncate_text_file_command(tmp_path)]
    for index in range(0, len(data), chunk_bytes):
        chunk = data[index : index + chunk_bytes]
        payload = base64.b64encode(chunk).decode("ascii")
        commands.append(_build_append_text_file_chunk_command(tmp_path, payload))
    commands.append(_build_replace_file_command(tmp_path, file_path))
    return commands, tmp_path


async def _read_text_file_via_exec(
    sandbox: Any,
    file_path: str,
    *,
    allow_missing: bool = False,
) -> tuple[str, bool]:
    response = await _exec_command(
        sandbox,
        _wrap_bash_command(_build_read_text_file_command(file_path)),
    )
    stdout = getattr(response, "result", "") or ""
    cleaned, exit_code = _extract_exit_code(
        stdout,
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    if exit_code not in (0, None):
        raise RuntimeError(cleaned or f"read failed for {file_path}")
    payload = json.loads(cleaned or "{}")
    if not payload.get("exists"):
        if allow_missing:
            return "", False
        raise FileNotFoundError(file_path)
    return str(payload.get("content", "") or ""), True


async def _write_text_file_via_exec(
    sandbox: Any,
    file_path: str,
    content: str,
    *,
    timeout: int | None = None,
) -> None:
    if not _supports_exec_transport(sandbox):
        raise RuntimeError("Sandbox process has no exec method")
    commands, tmp_path = _build_write_text_file_commands(file_path, content)
    try:
        for command in commands:
            response = await _exec_command(
                sandbox,
                _wrap_bash_command(command),
                timeout=timeout,
            )
            stdout = getattr(response, "result", "") or ""
            cleaned, exit_code = _extract_exit_code(
                stdout,
                fallback_exit_code=getattr(response, "exit_code", None),
            )
            if exit_code not in (0, None):
                raise RuntimeError(cleaned or f"write failed for {file_path}")
    except Exception:
        if tmp_path:
            try:
                await _exec_command(
                    sandbox,
                    _wrap_bash_command(_build_remove_file_command(tmp_path)),
                    timeout=timeout,
                )
            except Exception:
                logger.debug("remote temp cleanup failed for %s", tmp_path, exc_info=True)
        raise


async def _delete_file_via_exec(sandbox: Any, file_path: str) -> None:
    if not _supports_exec_transport(sandbox):
        raise RuntimeError("Sandbox process has no exec method")
    response = await _exec_command(sandbox, _wrap_bash_command(f"rm -f {shlex.quote(file_path)}"))
    stdout = getattr(response, "result", "") or ""
    cleaned, exit_code = _extract_exit_code(
        stdout,
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    if exit_code not in (0, None):
        raise RuntimeError(cleaned or f"delete failed for {file_path}")


__all__ = [
    "_REMOTE_WRITE_CHUNK_BYTES",
    "_build_append_text_file_chunk_command",
    "_build_read_text_file_command",
    "_build_remove_file_command",
    "_build_replace_file_command",
    "_build_truncate_text_file_command",
    "_build_write_text_file_command",
    "_build_write_text_file_commands",
    "_delete_file_via_exec",
    "_exec_command",
    "_read_text_file_via_exec",
    "_supports_exec_transport",
    "_write_text_file_via_exec",
]
