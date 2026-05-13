"""Daemon transport tests for ``_call_daemon``."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sandbox.host import daemon_client as command


def _ok_response() -> str:
    return json.dumps({"success": True, "timings": {}})


def _ready_response(*, ready: bool = True) -> str:
    return json.dumps(
        {
            "success": True,
            "ready": ready,
            "probes": [],
            "timings": {},
        }
    )


def _bootstrap_ready_response() -> str:
    return json.dumps(
        {
            "success": True,
            "ready": False,
            "probes": [
                {
                    "name": "control_plane",
                    "status": "down",
                    "details": {
                        "error_type": "WorkspaceBindingError",
                        "error": "workspace binding is missing",
                    },
                },
                {"name": "data_plane", "status": "ok", "details": {}},
                {"name": "mutation_gate", "status": "ok", "details": {}},
            ],
            "timings": {},
        }
    )


async def test_daemon_uses_daemon_thin_client_by_default() -> None:
    seen: list[str] = []

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0)

    response = await command._call_daemon(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.read_file",
        args={"path": "a"},
    )

    assert response == {"success": True, "timings": {}}
    assert len(seen) == 1
    assert "runtime.sock" in seen[0]
    assert "AF_UNIX" in seen[0]


def test_daemon_commands_do_not_forward_host_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNSUPPORTED_RUNTIME_ENV", "ignored")
    monkeypatch.setenv("EOS_OCC_AUTO_SQUASH_MAX_DEPTH", "64")
    monkeypatch.setenv("EOS_OCC_SQUASH_MODE", "async")

    thin_client = command._daemon_thin_client_command("{}")
    daemon_spawn = command._daemon_spawn_command()

    assert thin_client.startswith("sh -c ")
    assert daemon_spawn.startswith("sh -c ")
    assert "UNSUPPORTED_RUNTIME_ENV" not in thin_client
    assert "UNSUPPORTED_RUNTIME_ENV" not in daemon_spawn
    assert "EOS_OCC_SQUASH_MODE" not in daemon_spawn
    assert "EOS_OCC_AUTO_SQUASH_MAX_DEPTH" not in daemon_spawn


def test_daemon_spawn_tracks_empty_runtime_env_signature() -> None:
    daemon_spawn = command._daemon_spawn_command()

    assert "runtime.env" in daemon_spawn
    assert "ENV_SIG=" in daemon_spawn
    assert "kill" in daemon_spawn


async def test_daemon_transport_spawns_on_socket_missing() -> None:
    seen: list[str] = []
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_CONNECT_FAILED:ConnectionRefusedError",
            exit_code=command._THIN_CLIENT_CONNECT_FAILED,
        ),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
        SimpleNamespace(stdout=_ready_response(), stderr="", exit_code=0),
        SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0),
    ]

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return responses.pop(0)

    response = await command._call_daemon(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.read_file",
        args={"layer_stack_root": "/tmp/layers", "path": "a"},
    )

    assert response == {"success": True, "timings": {}}
    assert len(seen) == 4
    assert "AF_UNIX" in seen[0]
    assert "sandbox.runtime.daemon" in seen[1]
    assert "api.runtime.ready" in seen[2]
    assert "AF_UNIX" in seen[3]


async def test_daemon_transport_allows_unbound_readiness_for_workspace_bootstrap() -> None:
    seen: list[str] = []
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_CONNECT_FAILED:ConnectionRefusedError",
            exit_code=command._THIN_CLIENT_CONNECT_FAILED,
        ),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
        SimpleNamespace(stdout=_bootstrap_ready_response(), stderr="", exit_code=0),
        SimpleNamespace(stdout=_ok_response(), stderr="", exit_code=0),
    ]

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return responses.pop(0)

    response = await command._call_daemon(
        exec_fn=fake_exec,
        sandbox_id="sb-1",
        op="api.ensure_workspace_base",
        args={"layer_stack_root": "/tmp/layers", "workspace_root": "/testbed"},
    )

    assert response == {"success": True, "timings": {}}
    assert len(seen) == 4
    assert "api.runtime.ready" in seen[2]
    assert "api.ensure_workspace_base" in seen[3]


async def test_daemon_transport_readiness_failure_fails_closed() -> None:
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_CONNECT_FAILED:FileNotFoundError",
            exit_code=command._THIN_CLIENT_CONNECT_FAILED,
        ),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
        SimpleNamespace(stdout=_ready_response(ready=False), stderr="", exit_code=0),
    ]

    async def fake_exec(_sandbox_id: str, _command_str: str, **_: Any) -> Any:
        return responses.pop(0)

    with pytest.raises(command._DaemonReadinessError) as exc:
        await command._call_daemon(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.read_file",
            args={"layer_stack_root": "/tmp/layers", "path": "a"},
        )

    assert exc.value.kind == "RuntimeNotReady"
    assert exc.value.details["original_op"] == "api.read_file"


async def test_daemon_transport_bad_readiness_response_uses_readiness_error() -> None:
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_CONNECT_FAILED:FileNotFoundError",
            exit_code=command._THIN_CLIENT_CONNECT_FAILED,
        ),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
        SimpleNamespace(stdout="", stderr="", exit_code=0),
    ]

    async def fake_exec(_sandbox_id: str, _command_str: str, **_: Any) -> Any:
        return responses.pop(0)

    with pytest.raises(command._DaemonReadinessError) as exc:
        await command._call_daemon(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.read_file",
            args={"layer_stack_root": "/tmp/layers", "path": "a"},
        )

    assert exc.value.kind == "BadRuntimeReadinessResponse"
    assert exc.value.details["original_op"] == "api.read_file"


async def test_daemon_spawn_failure_fails_closed() -> None:
    seen: list[str] = []
    responses: list[Any] = [
        SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_CONNECT_FAILED:ConnectionRefusedError",
            exit_code=command._THIN_CLIENT_CONNECT_FAILED,
        ),
        SimpleNamespace(
            stdout="",
            stderr="sandbox daemon failed to bind socket within 2.5s",
            exit_code=1,
        ),
    ]

    async def fake_exec(_sandbox_id: str, command_str: str, **_: Any) -> Any:
        seen.append(command_str)
        return responses.pop(0)

    with pytest.raises(command._DaemonDispatchError) as exc:
        await command._call_daemon(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.read_file",
            args={"path": "a"},
        )

    assert exc.value.kind == "RuntimeExecFailed"
    assert len(seen) == 2
    assert "AF_UNIX" in seen[0]
    assert "sandbox.runtime.daemon" in seen[1]


async def test_daemon_transport_does_not_retry_after_io_failure() -> None:
    async def fake_exec(_sandbox_id: str, _command_str: str, **_: Any) -> Any:
        return SimpleNamespace(
            stdout="",
            stderr="EOS_DAEMON_IO_FAILED:socket.timeout",
            exit_code=command._THIN_CLIENT_IO_FAILED,
        )

    with pytest.raises(command._DaemonDispatchError) as exc:
        await command._call_daemon(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.write_file",
            args={"layer_stack_root": "/tmp/layers", "path": "a"},
        )

    assert exc.value.kind == "RuntimeExecFailed"


async def test_daemon_transport_rejects_exec_result_without_exit_code() -> None:
    async def fake_exec(_sandbox_id: str, _command_str: str, **_: Any) -> Any:
        return SimpleNamespace(stdout="", stderr="")

    with pytest.raises(command._DaemonDispatchError) as exc:
        await command._call_daemon(
            exec_fn=fake_exec,
            sandbox_id="sb-1",
            op="api.read_file",
            args={"layer_stack_root": "/tmp/layers", "path": "a"},
        )

    assert exc.value.kind == "BadExecResult"
