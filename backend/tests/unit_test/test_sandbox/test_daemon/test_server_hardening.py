"""Hardening tests for ``sandbox.runtime.daemon.rpc.server``.

Covers BL-01..BL-04 from the runtime code review:

* BL-01: oversized requests get a structured ``request_too_large`` envelope
  instead of a silent connection drop.
* BL-02: ``readline`` is bounded by a read timeout; stalled peers close
  silently.
* BL-03: socket TOCTOU — parent dir is chmod'd 0o700, the bind happens under
  umask 0o077 and chmod failures propagate.
* BL-04: dispatcher's internal_error envelope omits the traceback and
  includes a correlation ``error_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sandbox.runtime.daemon.rpc import dispatcher
from sandbox.runtime.daemon.rpc import server as server_module
from sandbox.runtime.daemon import __main__ as daemon_main


@pytest.fixture(autouse=True)
def _restore_op_table() -> None:
    saved = dict(dispatcher.OP_TABLE)
    try:
        yield
    finally:
        dispatcher.OP_TABLE.clear()
        dispatcher.OP_TABLE.update(saved)


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """A short tmp path under /tmp. pytest's tmp_path can exceed the macOS
    AF_UNIX 104-char sun_path limit."""
    path = Path(tempfile.mkdtemp(prefix="eos-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def _wait_for_socket(path: Path, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"socket never appeared at {path}")


async def _spawn_server(
    socket_path: Path, pid_path: Path
) -> asyncio.Task[None]:
    task = asyncio.create_task(server_module.serve(socket_path, pid_path))
    try:
        await _wait_for_socket(socket_path)
    except BaseException:
        task.cancel()
        with pytest.raises(BaseException):  # pragma: no cover - propagation
            await task
        raise
    return task


async def _stop_server(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def _read_envelope(reader: asyncio.StreamReader) -> dict[str, Any]:
    raw = await asyncio.wait_for(reader.readline(), timeout=2.0)
    return json.loads(raw.decode("utf-8"))


async def test_oversize_request_returns_request_too_large_envelope(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-01: requests larger than ``MAX_REQUEST_BYTES`` get a structured
    error rather than a silently-closed connection."""

    monkeypatch.setattr(server_module, "MAX_REQUEST_BYTES", 1024)
    socket_path = short_tmp_path / "runtime.sock"
    pid_path = short_tmp_path / "runtime.pid"

    task = await _spawn_server(socket_path, pid_path)
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        try:
            # Send a single line that exceeds the buffer limit before \n.
            payload = b"x" * 4096 + b"\n"
            writer.write(payload)
            await writer.drain()
            response = await _read_envelope(reader)
        finally:
            writer.close()
            with contextlib.suppress(BaseException):
                await writer.wait_closed()
    finally:
        await _stop_server(task)

    assert response["success"] is False
    assert response["error"]["kind"] == "request_too_large"
    assert response["error"]["details"] == {"limit": 1024}


async def test_idle_connection_times_out_without_envelope(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-02: a peer that opens but never sends ``\\n`` is closed silently
    by the read timeout rather than pinning a connection task forever."""

    monkeypatch.setattr(server_module, "REQUEST_READ_TIMEOUT_S", 0.05)
    socket_path = short_tmp_path / "runtime.sock"
    pid_path = short_tmp_path / "runtime.pid"

    task = await _spawn_server(socket_path, pid_path)
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        try:
            # Do not write anything; expect EOF from the daemon side after
            # the timeout fires.
            raw = await asyncio.wait_for(reader.read(), timeout=1.0)
        finally:
            writer.close()
            with contextlib.suppress(BaseException):
                await writer.wait_closed()
    finally:
        await _stop_server(task)

    # Timeout closes silently — no envelope written.
    assert raw == b""


async def test_socket_and_parent_dir_locked_down(short_tmp_path: Path) -> None:
    """BL-03: socket inode and parent dir are restricted before any peer can
    connect; the explicit chmod is not allowed to fail silently."""

    sock_dir = short_tmp_path / "runtime"
    socket_path = sock_dir / "runtime.sock"
    pid_path = sock_dir / "runtime.pid"

    task = await _spawn_server(socket_path, pid_path)
    try:
        parent_mode = stat.S_IMODE(os.stat(sock_dir).st_mode)
        sock_mode = stat.S_IMODE(os.stat(socket_path).st_mode)
    finally:
        await _stop_server(task)

    assert parent_mode == 0o700
    assert sock_mode == 0o600


def test_daemon_pid_lock_rejects_second_owner(short_tmp_path: Path) -> None:
    pid_path = short_tmp_path / "runtime.pid"
    fd = daemon_main._acquire_pid_lock(pid_path)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            daemon_main._acquire_pid_lock(pid_path)
    finally:
        os.close(fd)


async def test_dispatch_internal_error_envelope_strips_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BL-04: handler exceptions produce ``internal_error`` envelopes with a
    correlation ``error_id`` and *no* ``traceback`` field on the wire."""

    def boom(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(dispatcher.OP_TABLE, "test.boom", boom)

    response = await dispatcher.dispatch_envelope_async(
        {"op": "test.boom", "args": {}}
    )

    assert response["success"] is False
    err = response["error"]
    assert err["kind"] == "internal_error"
    assert err["message"] == "kaboom"
    details = err["details"]
    assert details["op"] == "test.boom"
    assert "error_id" in details
    assert isinstance(details["error_id"], str) and details["error_id"]
    assert "traceback" not in details
