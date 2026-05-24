"""Cancellation-aware namespace execution tests."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable

import pytest

from sandbox._shared.models import Intent, ToolCallRequest
from sandbox.overlay import namespace_runner as namespace_mod
from sandbox.overlay.handle import OverlayHandle


pytestmark = pytest.mark.asyncio


async def test_run_in_namespace_signals_shell_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    saw_cancel = threading.Event()

    def _fake_child(
        *,
        payload_ref: Path,
        stdout_ref: Path,
        stderr_ref: Path,
        timeout: float | None,
        cancel_event: threading.Event | None,
        pid_recorder: Callable[[int], None] | None,
    ) -> int:
        del payload_ref, stdout_ref, stderr_ref, timeout
        assert cancel_event is not None
        if pid_recorder is not None:
            pid_recorder(99999999)
        started.set()
        if cancel_event.wait(timeout=2):
            saw_cancel.set()
        return -15

    monkeypatch.setattr(namespace_mod, "_run_namespace_entrypoint", _fake_child)
    handle = OverlayHandle(
        workspace_root="/testbed",
        layer_paths=((tmp_path / "lower").as_posix(),),
        upperdir=tmp_path / "upper",
        workdir=tmp_path / "work",
        snapshot_version=1,
        lease_id="lease-1",
        namespace_pid=None,
        run_dir=tmp_path,
        snapshot_manifest=None,
        _release=None,
    )
    handle.upperdir.mkdir(parents=True)
    handle.workdir.mkdir(parents=True)
    req = ToolCallRequest(
        invocation_id="req-1",
        agent_id="agent-a",
        verb="shell",
        intent=Intent.WRITE_ALLOWED,
        args={"command": "sleep 60", "cwd": ".", "timeout_seconds": 60},
    )

    task = asyncio.create_task(namespace_mod.run_in_namespace(handle, req))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert saw_cancel.is_set()
