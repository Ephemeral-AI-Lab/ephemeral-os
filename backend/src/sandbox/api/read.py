"""Public sandbox file-read verb."""

from __future__ import annotations

import json
import shlex
import time

from sandbox.api.utils.models import ReadFileRequest, ReadFileResult
from sandbox.api.raw_exec import raw_exec


async def read_file(sandbox_id: str, request: ReadFileRequest) -> ReadFileResult:
    """Read one UTF-8 text file through raw provider exec."""
    total_start = time.perf_counter()
    script = (
        "import json,pathlib,sys; "
        "p=pathlib.Path(sys.argv[1]); "
        "\ntry:\n"
        " data=p.read_text(encoding='utf-8')\n"
        " print(json.dumps({'exists': True, 'content': data}))\n"
        "except FileNotFoundError:\n"
        " print(json.dumps({'exists': False, 'content': ''}))"
    )
    raw_start = time.perf_counter()
    result = await raw_exec(
        sandbox_id,
        f"python3 -c {shlex.quote(script)} {shlex.quote(request.path)}",
    )
    raw_elapsed = time.perf_counter() - raw_start
    if result.exit_code != 0:
        return ReadFileResult(
            success=False,
            exists=False,
            content="",
            timings={
                "api.read.raw_exec_s": raw_elapsed,
                "api.read.total_s": time.perf_counter() - total_start,
            },
        )
    parse_start = time.perf_counter()
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ReadFileResult(
            success=False,
            exists=False,
            content="",
            timings={
                "api.read.raw_exec_s": raw_elapsed,
                "api.read.parse_s": time.perf_counter() - parse_start,
                "api.read.total_s": time.perf_counter() - total_start,
            },
        )
    return ReadFileResult(
        success=True,
        exists=bool(payload.get("exists", False)),
        content=str(payload.get("content", "")),
        timings={
            "api.read.raw_exec_s": raw_elapsed,
            "api.read.parse_s": time.perf_counter() - parse_start,
            "api.read.total_s": time.perf_counter() - total_start,
        },
    )


__all__ = ["read_file"]
