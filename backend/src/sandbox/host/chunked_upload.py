"""Shared host-side helpers for chunked base64 uploads through raw exec."""

from __future__ import annotations

import base64
import shlex
from collections.abc import Callable
from typing import Any, Protocol

DEFAULT_CHUNK_SIZE = 32 * 1024


class RawExecCallable(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any: ...


async def write_base64_chunks(
    exec_fn: RawExecCallable,
    sandbox_id: str,
    *,
    content: bytes,
    remote_path: str,
    check_result: Callable[[Any, str], None],
    failure_message: Callable[[int], str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout: int = 60,
) -> int:
    """Append *content* to *remote_path* using the existing base64 exec path."""
    encoded = base64.b64encode(content).decode("ascii")
    chunks = 0
    for offset in range(0, len(encoded), chunk_size):
        chunk = encoded[offset : offset + chunk_size]
        result = await exec_fn(
            sandbox_id,
            (
                f"printf %s {shlex.quote(chunk)} | base64 -d "
                f">> {shlex.quote(remote_path)}"
            ),
            timeout=timeout,
        )
        check_result(result, failure_message(offset))
        chunks += 1
    return chunks


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "RawExecCallable",
    "write_base64_chunks",
]
