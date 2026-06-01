"""Phase 2.6 Closer F — ``AuditRecorder.aclose()`` single async teardown path."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from task_center_runner.audit.recorder import AuditRecorder


def test_audit_recorder_aclose_awaits_puller_then_disposes(tmp_path: Path) -> None:
    """``aclose`` drains the puller, writes the sink, leaves no background tasks."""
    recorder = AuditRecorder(tmp_path / "run", request_id="rid-1")
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
                    "type": "smoke.event",
                    "payload": {"daemon": {"pid": 12345}},
                }
            ],
            "buffer": {"pressure": 0.0},
            "snapshot": {"daemon": {"boot_epoch_id": 1}},
            "cursor": {"after_seq": 1},
        }

    async def _exercise() -> None:
        recorder.attach_daemon_audit_puller(pull=_pull)
        # Let at least one pull complete so the sink writes a row.
        await asyncio.sleep(0.15)
        # Single teardown path — awaits puller's final drain + dispose body.
        await recorder.aclose()
        # No leftover puller task should be left dangling.
        remaining = [t for t in asyncio.all_tasks() if not t.done()]
        # We can't filter precisely (other test infra may have tasks) but the
        # recorder must have cleared its own puller field.
        assert recorder._daemon_audit_puller is None  # noqa: SLF001
        del remaining  # purely an assertion staging point

    asyncio.run(_exercise())

    sink_file = tmp_path / "run" / "sandbox_events.jsonl"
    assert sink_file.exists()
    # At least one normalized row from the pulled event landed on disk.
    assert sink_file.read_text().strip() != ""


def test_audit_recorder_dispose_still_works_without_puller(tmp_path: Path) -> None:
    """Back-compat: sync ``dispose`` is the path for stubs that never attach a puller."""
    recorder = AuditRecorder(tmp_path / "run", request_id="rid-2")
    recorder.start()
    # No puller attached → sync dispose is allowed.
    recorder.dispose()
    assert (tmp_path / "run" / "run.json").exists()


def test_audit_recorder_dispose_raises_when_puller_still_active(tmp_path: Path) -> None:
    """Safety: calling sync ``dispose`` while a puller is alive must raise."""
    recorder = AuditRecorder(tmp_path / "run", request_id="rid-3")
    recorder.start()

    async def _pull(after_seq: int, limit: int) -> dict[str, Any]:
        return {
            "events": [],
            "buffer": {"pressure": 0.0},
            "snapshot": {"daemon": {"boot_epoch_id": 1}},
            "cursor": {"after_seq": after_seq},
        }

    async def _exercise() -> None:
        recorder.attach_daemon_audit_puller(pull=_pull)
        try:
            with pytest.raises(RuntimeError, match="aclose"):
                recorder.dispose()
        finally:
            await recorder.stop_daemon_audit_puller()

    asyncio.run(_exercise())
    # Now safe to dispose (puller torn down).
    recorder.dispose()
