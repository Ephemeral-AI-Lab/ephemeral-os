"""Rust daemon command-op dispatch contract."""

from __future__ import annotations

from pathlib import Path

from sandbox.api import transport


def test_public_command_ops_are_rust_daemon_ops() -> None:
    assert transport.DAEMON_OP_EXEC_COMMAND == "api.v1.exec_command"
    assert transport.DAEMON_OP_COMMAND_WRITE_STDIN == "api.v1.write_stdin"
    assert not hasattr(transport, "DAEMON_OP_SHELL")


def test_rust_dispatcher_registers_write_stdin_without_shell_alias() -> None:
    dispatcher = _repo_root() / "sandbox/crates/eos-daemon/src/dispatcher.rs"
    text = dispatcher.read_text(encoding="utf-8")

    assert 'register_builtin("api.v1.exec_command"' in text
    assert 'register_builtin("api.v1.write_stdin"' in text
    assert '"api.v1.command.write_stdin"' in text
    assert "crate::command::op_command_write_stdin" in text
    assert "api.v1.shell" not in text


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise AssertionError("repo root not found")
