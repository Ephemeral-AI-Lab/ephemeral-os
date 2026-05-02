"""Provider-neutral Python command builders for remote text-file I/O."""

from __future__ import annotations

import base64
import shlex
import uuid


REMOTE_WRITE_CHUNK_BYTES = 24 * 1024


def build_read_text_file_command(file_path: str) -> str:
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
    return f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"


def build_write_text_file_command(file_path: str, content: str) -> str:
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


def build_truncate_text_file_command(file_path: str) -> str:
    script = """
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(b"")
"""
    return f"python3 -c {shlex.quote(script)} {shlex.quote(file_path)}"


def build_append_text_file_chunk_command(file_path: str, payload: str) -> str:
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


def build_replace_file_command(tmp_path: str, file_path: str) -> str:
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


def build_remove_file_command(file_path: str) -> str:
    return f"rm -f {shlex.quote(file_path)}"


def build_write_text_file_commands(
    file_path: str,
    content: str,
    *,
    chunk_bytes: int = REMOTE_WRITE_CHUNK_BYTES,
) -> tuple[list[str], str | None]:
    data = content.encode("utf-8")
    if len(data) <= chunk_bytes:
        return [build_write_text_file_command(file_path, content)], None

    tmp_path = f"{file_path}.codex-write-{uuid.uuid4().hex}.tmp"
    commands = [build_truncate_text_file_command(tmp_path)]
    for index in range(0, len(data), chunk_bytes):
        chunk = data[index : index + chunk_bytes]
        payload = base64.b64encode(chunk).decode("ascii")
        commands.append(build_append_text_file_chunk_command(tmp_path, payload))
    commands.append(build_replace_file_command(tmp_path, file_path))
    return commands, tmp_path


__all__ = [
    "REMOTE_WRITE_CHUNK_BYTES",
    "build_append_text_file_chunk_command",
    "build_read_text_file_command",
    "build_remove_file_command",
    "build_replace_file_command",
    "build_truncate_text_file_command",
    "build_write_text_file_command",
    "build_write_text_file_commands",
]
