"""Process.exec based file I/O helpers for sync Daytona live tests."""

from __future__ import annotations

import json
from typing import Any

from sandbox.api.bash import (
    extract_exit_code,
    wrap_bash_command,
)
from sandbox.api.file_commands import (
    build_read_text_file_command,
    build_write_text_file_command,
)


def write_text_via_exec(
    sandbox: Any,
    file_path: str,
    content: bytes | str,
    *,
    timeout: int = 120,
) -> None:
    """Write a UTF-8 text file through sync sandbox.process.exec."""
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    response = sandbox.process.exec(
        wrap_bash_command(build_write_text_file_command(file_path, text)),
        timeout=timeout,
    )
    cleaned, exit_code = extract_exit_code(
        getattr(response, "result", "") or "",
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    if exit_code not in (0, None):
        raise RuntimeError(cleaned or f"write failed for {file_path}")


def read_text_via_exec(
    sandbox: Any,
    file_path: str,
    *,
    timeout: int = 120,
) -> str:
    """Read a UTF-8 text file through sync sandbox.process.exec."""
    response = sandbox.process.exec(
        wrap_bash_command(build_read_text_file_command(file_path)),
        timeout=timeout,
    )
    cleaned, exit_code = extract_exit_code(
        getattr(response, "result", "") or "",
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    if exit_code not in (0, None):
        raise RuntimeError(cleaned or f"read failed for {file_path}")
    payload = json.loads(cleaned or "{}")
    if not payload.get("exists"):
        raise FileNotFoundError(file_path)
    return str(payload.get("content", "") or "")
