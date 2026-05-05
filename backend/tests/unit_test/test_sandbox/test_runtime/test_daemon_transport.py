"""Transport-selection tests for ``_call_runtime_server`` (Phase 3)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sandbox.control.daemon import command


@pytest.fixture
def fork_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPHEMERALOS_RUNTIME_TRANSPORT", raising=False)


@pytest.fixture
def daemon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EPHEMERALOS_RUNTIME_TRANSPORT", "daemon")


def _ok_response() -> str:
    return json.dumps({"success": True, "timings": {}})


async def test_default_transport_uses_fork_launcher(fork_env) -> None:
    seen: list[str] = []

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0)

    response = await command._call_runtime_server(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.read_file",
        args={"path": "a"},
    )
    assert response == {"success": True, "timings": {}}
    assert "sandbox.runtime.server" in seen[0]
    assert "runtime.sock" not in seen[0]


async def test_daemon_transport_uses_thin_client(daemon_env) -> None:
    seen: list[str] = []

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0)

    await command._call_runtime_server(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.read_file",
        args={"path": "a"},
    )
    assert len(seen) == 1
    assert "runtime.sock" in seen[0]
    assert "AF_UNIX" in seen[0]
    assert "sandbox.runtime.server" not in seen[0]


async def test_daemon_transport_spawns_on_socket_missing(daemon_env) -> None:
    seen: list[str] = []
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="Traceback ... ConnectionRefusedError: [Errno 111] Connection refused",
            exit_code=1,
        ),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
        SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0),
    ]

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return responses.pop(0)

    response = await command._call_runtime_server(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.read_file",
        args={"path": "a"},
    )
    assert response == {"success": True, "timings": {}}
    assert len(seen) == 3
    assert "AF_UNIX" in seen[0]
    assert "sandbox.runtime.daemon" in seen[1]
    assert "AF_UNIX" in seen[2]
