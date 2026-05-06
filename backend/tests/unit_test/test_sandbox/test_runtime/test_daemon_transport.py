"""Daemon transport tests for ``_call_runtime_server``."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sandbox.control.daemon import command


def _ok_response() -> str:
    return json.dumps({"success": True, "timings": {}})


async def test_runtime_uses_daemon_thin_client_by_default() -> None:
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
    assert len(seen) == 1
    assert "runtime.sock" in seen[0]
    assert "AF_UNIX" in seen[0]


def test_runtime_commands_forward_only_supported_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EPHEMERALOS_GITIGNORE_BACKEND", "layer_stack")

    env_prefix = "EPHEMERALOS_GITIGNORE_BACKEND=layer_stack "
    thin_client = command._runtime_thin_client_command("{}")
    daemon_spawn = command._runtime_daemon_spawn_command()

    assert thin_client.startswith(env_prefix)
    assert daemon_spawn.startswith(env_prefix)


async def test_daemon_transport_spawns_on_socket_missing() -> None:
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


async def test_daemon_spawn_failure_fails_closed() -> None:
    seen: list[str] = []
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="Traceback ... ConnectionRefusedError: [Errno 111] Connection refused",
            exit_code=1,
        ),
        SimpleNamespace(
            stdout="",
            stderr="sandbox runtime daemon failed to bind socket within 2.5s",
            exit_code=1,
        ),
    ]

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return responses.pop(0)

    with pytest.raises(command._RuntimeDispatchError) as exc:
        await command._call_runtime_server(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.read_file",
            args={"path": "a"},
        )

    assert exc.value.kind == "RuntimeExecFailed"
    assert len(seen) == 2
    assert "AF_UNIX" in seen[0]
    assert "sandbox.runtime.daemon" in seen[1]
