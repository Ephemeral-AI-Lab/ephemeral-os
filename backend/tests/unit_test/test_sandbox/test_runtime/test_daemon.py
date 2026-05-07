"""Tests for the resident sandbox runtime daemon (Phase 3)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from sandbox.runtime import api_handlers, daemon, server, write_edit_handlers


def _short_socket_path() -> tuple[Path, Path]:
    """Return ``(socket, pid)`` paths short enough for AF_UNIX (≤104 bytes)."""
    base = Path(tempfile.gettempdir()) / f"eos-daemon-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    return base / "runtime.sock", base / "runtime.pid"


@pytest.fixture(autouse=True)
def _isolate_op_table() -> None:
    saved = dict(server.OP_TABLE)
    server.OP_TABLE.clear()
    try:
        yield
    finally:
        server.OP_TABLE.clear()
        server.OP_TABLE.update(saved)


@pytest.fixture(autouse=True)
def _isolate_daemon_env() -> None:
    """Daemon ``serve`` mutates ``EPHEMERALOS_RUNTIME_DAEMON`` directly; reset
    after every test so it can't leak across cases."""
    saved = os.environ.get("EPHEMERALOS_RUNTIME_DAEMON")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("EPHEMERALOS_RUNTIME_DAEMON", None)
        else:
            os.environ["EPHEMERALOS_RUNTIME_DAEMON"] = saved


async def test_dispatch_envelope_async_runs_async_handler() -> None:
    async def handler(args: dict[str, object]) -> dict[str, object]:
        await asyncio.sleep(0)
        return {"success": True, "value": args["value"]}

    server.register_op("test.async_echo", handler)

    response = await server.dispatch_envelope_async(
        {"op": "test.async_echo", "args": {"value": 7}}
    )

    assert response["success"] is True
    assert response["value"] == 7
    assert "runtime.boot_to_dispatch_s" in response["timings"]


async def test_dispatch_envelope_async_runs_sync_handler() -> None:
    def handler(args: dict[str, object]) -> dict[str, object]:
        return {"success": True, "value": args["value"] * 2}

    server.register_op("test.sync_echo", handler)

    response = await server.dispatch_envelope_async(
        {"op": "test.sync_echo", "args": {"value": 5}}
    )
    assert response["success"] is True
    assert response["value"] == 10


async def test_dispatch_envelope_async_unknown_op_returns_structured_error() -> None:
    response = await server.dispatch_envelope_async({"op": "nope", "args": {}})
    assert response["success"] is False
    assert response["error"]["kind"] == "unknown_op"


async def test_dispatch_envelope_async_honors_boot_t0_override() -> None:
    """``boot_t0`` overrides module-level ``_BOOT_T0`` so daemon-mode dispatch
    measures per-call boot, not daemon uptime."""
    import time

    def handler(_: dict[str, object]) -> dict[str, object]:
        return {"success": True}

    server.register_op("test.boot", handler)

    # Pretend the daemon has been running for hours: real `_BOOT_T0` is far
    # in the past. With the per-call override, we should still see a small
    # boot_to_dispatch.
    response = await server.dispatch_envelope_async(
        {"op": "test.boot", "args": {}},
        boot_t0=time.perf_counter(),
    )
    assert response["success"] is True
    assert response["timings"]["runtime.boot_to_dispatch_s"] < 0.05


async def test_daemon_serves_one_envelope_per_connection() -> None:
    socket_path, pid_path = _short_socket_path()

    async def echo(args: dict[str, object]) -> dict[str, object]:
        return {"success": True, "value": args["value"]}

    server.register_op("test.echo", echo)

    serve_task = asyncio.create_task(daemon.serve(socket_path, pid_path))
    try:
        for _ in range(50):
            if socket_path.exists():
                break
            await asyncio.sleep(0.02)
        assert socket_path.exists(), "daemon never bound socket"
        assert pid_path.read_text().strip() == str(os.getpid())

        async def call(value: int) -> dict[str, object]:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            envelope = json.dumps({"op": "test.echo", "args": {"value": value}})
            writer.write(envelope.encode("utf-8") + b"\n")
            writer.write_eof()
            await writer.drain()
            raw = await reader.read()
            writer.close()
            await writer.wait_closed()
            return json.loads(raw.decode("utf-8").strip())

        first = await call(1)
        second = await call(2)
        assert first["value"] == 1
        assert second["value"] == 2
        # Per-connection ``boot_t0`` must keep ``boot_to_dispatch_s`` small
        # regardless of daemon uptime (regression guard for module-level
        # ``_BOOT_T0`` leaking into daemon mode).
        assert second["timings"]["runtime.boot_to_dispatch_s"] < 0.05
    finally:
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_daemon_handles_invalid_json() -> None:
    socket_path, pid_path = _short_socket_path()
    serve_task = asyncio.create_task(daemon.serve(socket_path, pid_path))
    try:
        for _ in range(50):
            if socket_path.exists():
                break
            await asyncio.sleep(0.02)
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(b"{not json\n")
        writer.write_eof()
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        response = json.loads(raw.decode("utf-8").strip())
        assert response["success"] is False
        assert response["error"]["kind"] == "bad_json"
    finally:
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass


def test_peer_bootstraps_register_snapshot_ops_without_compact() -> None:
    server._load_peer_bootstraps()

    assert "api.prepare_workspace_snapshot" in server.OP_TABLE
    assert "api.release_workspace_snapshot" in server.OP_TABLE
    assert "api.compact" not in server.OP_TABLE


def test_services_cached_per_layer_stack_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write_edit_handlers caches the per-root service tuple across calls."""
    api_handlers._services_cache_clear()

    class _FakeManager:
        def __init__(self, root: str) -> None:
            self.root = root

    monkeypatch.setattr(
        write_edit_handlers,
        "get_layer_stack_manager",
        lambda root: _FakeManager(str(root)),
    )
    monkeypatch.setattr(
        write_edit_handlers,
        "LayerStackClient",
        lambda manager: ("layer-stack", manager),
    )
    monkeypatch.setattr(
        write_edit_handlers,
        "SnapshotGitignoreOracle",
        lambda layer_stack: ("oracle", layer_stack),
    )
    monkeypatch.setattr(
        write_edit_handlers,
        "OccService",
        lambda *, gitignore, layer_stack: ("service", gitignore, layer_stack),
    )
    monkeypatch.setattr(
        write_edit_handlers,
        "OCCClient",
        lambda service, *, binding_reader, workspace_ref: (
            "occ-client",
            service,
            workspace_ref,
        ),
    )

    a1 = write_edit_handlers._services("/tmp/a")
    a2 = write_edit_handlers._services("/tmp/a")
    b1 = write_edit_handlers._services("/tmp/b")

    assert a1 is a2  # same root → cached tuple
    assert a1.manager is not b1.manager  # different roots → distinct managers


def test_drop_services_cache_cascades_to_all_runtime_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``api_handlers.drop_services_cache`` must cascade to write_edit + command_exec."""
    api_handlers._services_cache_clear()
    cleared: list[str] = []

    monkeypatch.setattr(
        write_edit_handlers,
        "drop_services_cache",
        lambda root: cleared.append(f"write_edit:{root}"),
    )
    from sandbox.runtime import command_exec_server

    monkeypatch.setattr(
        command_exec_server,
        "drop_services_cache",
        lambda root: cleared.append(f"command_exec:{root}"),
    )

    api_handlers.drop_services_cache("/tmp/a")
    assert "write_edit:/tmp/a" in cleared
    assert "command_exec:/tmp/a" in cleared
