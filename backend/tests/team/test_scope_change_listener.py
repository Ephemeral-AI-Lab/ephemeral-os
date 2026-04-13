from __future__ import annotations

import asyncio

import pytest

from team.runtime.scope_change_buffer import ScopeChangeBuffer
from team.runtime.scope_change_listener import ScopeChangeListener


class _FakeDriverConnection:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def notifies(self):
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
        if False:  # pragma: no cover
            yield None


class _FakeRawConnection:
    def __init__(self, driver_connection: _FakeDriverConnection) -> None:
        self.driver_connection = driver_connection


class _FakeConnection:
    def __init__(self, *, fail_listen: bool = False) -> None:
        self.driver_connection = _FakeDriverConnection()
        self.raw_connection = _FakeRawConnection(self.driver_connection)
        self.fail_listen = fail_listen
        self.listen_sql: list[str] = []
        self.closed = False

    async def get_raw_connection(self):
        return self.raw_connection

    async def exec_driver_sql(self, sql: str):
        self.listen_sql.append(sql)
        if self.fail_listen:
            raise RuntimeError("listen failed")

    async def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, *, fail_listen: bool = False) -> None:
        self.connection = _FakeConnection(fail_listen=fail_listen)

    async def connect(self):
        return self.connection


@pytest.mark.asyncio
async def test_start_uses_sanitized_channel_with_async_listen() -> None:
    engine = _FakeEngine()
    listener = ScopeChangeListener(engine, "run-123:abc")

    await listener.start()

    assert listener.is_running is True
    assert listener._channel == "scope_change_run_123_abc"
    assert engine.connection.listen_sql == ["LISTEN scope_change_run_123_abc"]
    await asyncio.wait_for(engine.connection.driver_connection.started.wait(), timeout=1.0)

    await listener.stop()
    assert engine.connection.closed is True


@pytest.mark.asyncio
async def test_start_falls_back_to_local_publish_when_listen_fails() -> None:
    engine = _FakeEngine(fail_listen=True)
    listener = ScopeChangeListener(engine, "run-123")
    own_buffer = ScopeChangeBuffer()
    other_buffer = ScopeChangeBuffer()

    await listener.start()
    listener.subscribe("agent-run-1", ["src/app"], own_buffer)
    listener.subscribe("agent-run-2", ["src/app"], other_buffer)

    listener.publish_change(
        file_path="src/app/main.py",
        agent_id="developer",
        agent_run_id="agent-run-1",
        edit_type="write",
    )

    assert listener.is_running is True
    assert own_buffer.has_pending is False
    assert other_buffer.has_pending is True
    assert engine.connection.closed is True

    display_messages: list[object] = []
    text = other_buffer.flush_into(display_messages)
    assert text is not None
    assert "src/app/main.py" in text
    assert "developer" in text

    await listener.stop()
