"""runtime · command family helpers.

Runtime ops route to a sandbox via ``runtime --sandbox-id <id>`` (the flag
precedes the operation name).
"""

from core.cli import runtime


def exec_command(
    sandbox_id, cmd, workspace_session_id=None, timeout_ms=None, yield_time_ms=None
):
    """Start a command. Without ``workspace_session_id`` it runs one-shot in an
    ephemeral workspace."""
    args = ["exec_command"]
    if workspace_session_id:
        args += ["--workspace-session-id", workspace_session_id]
    if timeout_ms is not None:
        args += ["--timeout-ms", str(timeout_ms)]
    if yield_time_ms is not None:
        args += ["--yield-time-ms", str(yield_time_ms)]
    args.append(cmd)
    return runtime(sandbox_id, *args)


def read_command_lines(sandbox_id, command_session_id, start_offset=0, limit=200):
    return runtime(
        sandbox_id,
        "read_command_lines",
        "--command-session-id",
        command_session_id,
        "--start-offset",
        str(start_offset),
        "--limit",
        str(limit),
    )


def write_command_stdin(sandbox_id, command_session_id, text, yield_time_ms=None):
    args = ["write_command_stdin", "--command-session-id", command_session_id]
    if yield_time_ms is not None:
        args += ["--yield-time-ms", str(yield_time_ms)]
    args.append(text)
    return runtime(sandbox_id, *args)
