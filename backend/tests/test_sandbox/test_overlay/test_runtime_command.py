"""Tests for sandbox-side user command execution."""

from __future__ import annotations

from pathlib import Path

from sandbox.runtime.overlay_capture_runtime.command import run_user_command


def test_run_user_command_preserves_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.bin"

    stdout, exit_code = run_user_command(
        user_cmd="printf out; printf err >&2; exit 3",
        stdin_bytes=None,
        cwd=str(tmp_path),
        stdout_path=str(stdout_path),
    )

    assert exit_code == 3
    assert stdout == b"outerr"
    assert stdout_path.read_bytes() == b"outerr"
