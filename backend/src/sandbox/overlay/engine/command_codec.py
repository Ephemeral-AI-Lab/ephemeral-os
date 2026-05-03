"""Command payload and runtime-command construction for overlay execution."""

from __future__ import annotations

import base64
import shlex

from sandbox.overlay.engine.constants import COMMAND_SAMPLE_LIMIT, RUN_DIR_PREFIX


def format_command_sample(command: str) -> str:
    compact = " ".join(command.split())
    if len(compact) <= COMMAND_SAMPLE_LIMIT:
        return compact
    return compact[:COMMAND_SAMPLE_LIMIT] + "..."


def encode_command_payload(command: str, stdin: str | None) -> tuple[str, str]:
    user_cmd_b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    stdin_b64 = (
        base64.b64encode(stdin.encode("utf-8")).decode("ascii")
        if stdin is not None
        else ""
    )
    return user_cmd_b64, stdin_b64


def build_runtime_command(args: list[str]) -> str:
    return (
        f"PYTHONPATH={shlex.quote(RUN_DIR_PREFIX)}${{PYTHONPATH:+:$PYTHONPATH}} "
        "python3 -m overlay_runtime.cli "
        + " ".join(shlex.quote(a) for a in args)
    )


__all__ = [
    "build_runtime_command",
    "encode_command_payload",
    "format_command_sample",
]
