"""Phase 2.5 slice 6 — ``background_tool.*`` daemon-ring emitter coverage."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from engine.background.task_supervisor import (
    BackgroundTaskStatus,
    BackgroundTaskSupervisor,
)
from sandbox.daemon.audit_buffer import get_audit_buffer
from tools import ToolResult


_AUDIT_CURSOR = {"seq": -1}


def _drain_background_events() -> list[dict[str, Any]]:
    buf = get_audit_buffer()
    snap = buf.pull(after_seq=_AUDIT_CURSOR["seq"], limit=10_000)
    events = snap.get("events", [])
    if events:
        _AUDIT_CURSOR["seq"] = int(events[-1]["seq"])
    return [
        evt
        for evt in events
        if str(evt.get("type", "")).startswith("background_tool.")
    ]


@pytest.fixture(autouse=True)
def _reset_audit_cursor() -> None:
    buf = get_audit_buffer()
    cursor = -1
    while True:
        snap = buf.pull(after_seq=cursor, limit=10_000)
        events = snap.get("events", [])
        if not events:
            break
        cursor = int(events[-1]["seq"])
    _AUDIT_CURSOR["seq"] = cursor
    yield


@pytest.mark.asyncio
async def test_background_tool_lifecycle_emits_started_completed_delivered() -> None:
    sup = BackgroundTaskSupervisor()

    async def _ok() -> ToolResult:
        return ToolResult(output="hello")

    sup.launch(
        task_id="bg_1",
        tool_name="shell",
        tool_input={"cmd": "ls"},
        coro=_ok(),
        agent_id="agent-x",
    )
    # Wait for the asyncio task done-callback to flip status.
    await asyncio.sleep(0.05)
    completed = sup.collect_completed()
    assert [t.task_id for t in completed] == ["bg_1"]

    events = _drain_background_events()
    types = [e["type"] for e in events]
    assert types == [
        "background_tool.started",
        "background_tool.completed",
        "background_tool.delivered",
    ]
    completed_event = next(
        e for e in events if e["type"] == "background_tool.completed"
    )
    section = completed_event["payload"]["background_tool"]
    assert section["background_task_id"] == "bg_1"
    assert section["status"] == BackgroundTaskStatus.COMPLETED.value
    assert section["tool_name"] == "shell"


@pytest.mark.asyncio
async def test_background_tool_failed_lifecycle() -> None:
    sup = BackgroundTaskSupervisor()

    async def _boom() -> ToolResult:
        raise ValueError("nope")

    sup.launch(
        task_id="bg_fail",
        tool_name="shell",
        tool_input={},
        coro=_boom(),
    )
    await asyncio.sleep(0.05)
    sup.collect_completed()

    events = _drain_background_events()
    failed = next(e for e in events if e["type"] == "background_tool.failed")
    assert failed["payload"]["background_tool"]["background_task_id"] == "bg_fail"
    assert failed["payload"]["background_tool"]["error_kind"] == "error"


@pytest.mark.asyncio
async def test_background_tool_cancelled_lifecycle() -> None:
    sup = BackgroundTaskSupervisor()

    async def _long_running() -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(output="done")

    sup.launch(
        task_id="bg_cancel",
        tool_name="shell",
        tool_input={},
        coro=_long_running(),
    )
    await sup.cancel("bg_cancel", reason="user_request")
    # Give the asyncio task a tick to finish cancellation cleanup.
    await asyncio.sleep(0.05)
    sup.collect_completed()
    events = _drain_background_events()
    cancelled = next(e for e in events if e["type"] == "background_tool.cancelled")
    section = cancelled["payload"]["background_tool"]
    assert section["cancel_reason"] == "user_request"
    assert section["background_task_id"] == "bg_cancel"


def test_background_tool_emitter_adds_no_new_threads_on_launch() -> None:
    sup = BackgroundTaskSupervisor()
    before = threading.active_count()

    async def _drive() -> None:
        async def _ok() -> ToolResult:
            return ToolResult(output="ok")

        sup.launch(
            task_id="bg_thread",
            tool_name="shell",
            tool_input={},
            coro=_ok(),
        )
        await asyncio.sleep(0.02)
        sup.collect_completed()

    asyncio.run(_drive())
    after = threading.active_count()
    assert after == before


def test_audit_recorder_attach_daemon_audit_puller_starts_and_stops(
    tmp_path,
) -> None:
    """Wiring smoke test — attach puller, drain a fake response, stop."""
    from task_center_runner.audit.recorder import AuditRecorder

    recorder = AuditRecorder(
        tmp_path / "run",
        task_center_run_id="run-123",
    )
    recorder.start()

    pulls = 0

    async def _pull(after_seq: int, limit: int) -> dict[str, Any]:
        nonlocal pulls
        pulls += 1
        if pulls > 1:
            return {
                "events": [],
                "buffer": {"pressure": 0.0},
                "snapshot": {"daemon": {"boot_epoch_id": 1}},
                "cursor": {"after_seq": after_seq},
            }
        return {
            "events": [
                {
                    "seq": 1,
                    "lane": "normal",
                    "type": "sandbox.smoke",
                    "payload": {"daemon": {"pid": 12345}},
                }
            ],
            "buffer": {"pressure": 0.0},
            "snapshot": {"daemon": {"boot_epoch_id": 1}},
            "cursor": {"after_seq": 1},
        }

    async def _exercise() -> None:
        recorder.attach_daemon_audit_puller(pull=_pull)
        await asyncio.sleep(0.15)  # let one pull tick fire
        await recorder.stop_daemon_audit_puller()

    asyncio.run(_exercise())
    stats = recorder.daemon_audit_puller_stats()
    # Stats accessor returns None after stop (puller cleared).
    assert stats is None

    recorder.dispose()
    sink_file = tmp_path / "run" / "sandbox_events.jsonl"
    assert sink_file.exists()
    assert sink_file.read_text()
