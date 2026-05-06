"""Tests for the resident sandbox runtime daemon (Phase 3)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from sandbox.runtime import api_handlers, daemon, server


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


def test_running_in_daemon_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPHEMERALOS_RUNTIME_DAEMON", raising=False)
    assert api_handlers._running_in_daemon() is False
    monkeypatch.setenv("EPHEMERALOS_RUNTIME_DAEMON", "1")
    assert api_handlers._running_in_daemon() is True


def test_peer_bootstraps_register_snapshot_ops_without_compact() -> None:
    server._load_peer_bootstraps()

    assert "api.prepare_workspace_snapshot" in server.OP_TABLE
    assert "api.release_workspace_snapshot" in server.OP_TABLE
    assert "api.compact" not in server.OP_TABLE


async def test_commit_lock_skipped_in_daemon_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EPHEMERALOS_RUNTIME_DAEMON", "1")
    lock = api_handlers._commit_lock(tmp_path)
    async with lock:
        pass
    assert not (tmp_path / ".commit.lock").exists()


def test_services_cached_per_layer_stack_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_handlers._services_cache_clear()

    class _FakeManager:
        def __init__(self, root: str) -> None:
            self.root = root

    class _FakeOracle:
        def __init__(self, layer_stack: object) -> None:
            self.layer_stack = layer_stack

    class _FakeService:
        def __init__(
            self,
            *,
            gitignore: _FakeOracle,
            layer_stack: object,
            workspace_ref: str = "",
        ) -> None:
            self.gitignore = gitignore
            self.layer_stack = layer_stack
            self.workspace_ref = workspace_ref

    monkeypatch.setattr(
        api_handlers,
        "get_layer_stack_manager",
        lambda root: _FakeManager(str(root)),
    )
    monkeypatch.setattr(api_handlers, "SnapshotGitignoreOracle", _FakeOracle)
    monkeypatch.setattr(api_handlers, "OccService", _FakeService)

    a1 = api_handlers._services({"layer_stack_root": "/tmp/a"})
    a2 = api_handlers._services({"layer_stack_root": "/tmp/a"})
    b1 = api_handlers._services({"layer_stack_root": "/tmp/b"})

    assert a1 is a2  # same root → cached triple
    assert a1[0] is not b1[0]  # different roots → distinct managers


def test_drop_services_cache_removes_only_requested_layer_stack_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_handlers._services_cache_clear()

    class _FakeManager:
        def __init__(self, root: str) -> None:
            self.root = root

    class _FakeOracle:
        def __init__(self, layer_stack: object) -> None:
            self.layer_stack = layer_stack

    class _FakeService:
        def __init__(
            self,
            *,
            gitignore: _FakeOracle,
            layer_stack: object,
            workspace_ref: str = "",
        ) -> None:
            self.gitignore = gitignore
            self.layer_stack = layer_stack
            self.workspace_ref = workspace_ref

    monkeypatch.setattr(
        api_handlers,
        "get_layer_stack_manager",
        lambda root: _FakeManager(str(root)),
    )
    monkeypatch.setattr(api_handlers, "SnapshotGitignoreOracle", _FakeOracle)
    monkeypatch.setattr(api_handlers, "OccService", _FakeService)

    first_a = api_handlers._services({"layer_stack_root": "/tmp/a"})
    first_b = api_handlers._services({"layer_stack_root": "/tmp/b"})

    api_handlers.drop_services_cache("/tmp/a")

    second_a = api_handlers._services({"layer_stack_root": "/tmp/a"})
    second_b = api_handlers._services({"layer_stack_root": "/tmp/b"})
    assert second_a is not first_a
    assert second_b is first_b
