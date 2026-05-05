"""Server chat route sandbox auto-create event tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app_factory import RuntimeConfig
from server.protocol import BackendEvent
from server.routers.core import create_core_router
from task_center.sandbox_bridge import TaskCenterSandboxBinding


class _ReadyStore:
    is_ready = True


class _Launcher:
    async def wait_for_idle(self) -> None:
        return None


class _Runtime:
    def __init__(self) -> None:
        self.config = RuntimeConfig(cwd="/tmp")
        self.busy = False
        self._busy_lock = asyncio.Lock()
        self._queue: asyncio.Queue[BackendEvent | None] | None = None

    def set_event_queue(self, queue: asyncio.Queue[BackendEvent | None] | None) -> None:
        self._queue = queue

    async def emit(self, event: BackendEvent) -> None:
        if self._queue is not None:
            await self._queue.put(event)


def test_chat_without_sandbox_id_streams_created_sandbox_id(monkeypatch) -> None:
    from server import app_factory
    import task_center.entry as entry_module

    ready_store = _ReadyStore()
    for name in (
        "task_center_store",
        "complex_task_request_store",
        "task_segment_store",
        "harness_graph_store",
        "context_packet_store",
    ):
        monkeypatch.setattr(app_factory, name, ready_store)

    calls: list[dict[str, Any]] = []

    def fake_start_task_center_entry_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            task_center_run_id="run-1",
            binding=TaskCenterSandboxBinding(
                sandbox_id="sb-auto",
                task_center_run_id="run-1",
                owned_by_task_center=True,
            ),
            launcher=_Launcher(),
        )

    monkeypatch.setattr(
        entry_module,
        "start_task_center_entry_run",
        fake_start_task_center_entry_run,
    )

    runtime = _Runtime()
    app = FastAPI()
    app.include_router(create_core_router(lambda: runtime))

    response = TestClient(app).post("/api/chat", json={"line": "hello"})

    assert response.status_code == 200
    assert calls[0]["sandbox_id"] is None
    sandbox_events = [
        event
        for event in _parse_sse_events(response.text)
        if event.get("type") == "transcript_item"
        and event.get("item", {}).get("role") == "system"
        and event.get("item", {}).get("text") == "sandbox_id=sb-auto"
    ]
    assert len(sandbox_events) == 1


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw.startswith("data: "):
            continue
        payload = raw[len("data: ") :]
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events
