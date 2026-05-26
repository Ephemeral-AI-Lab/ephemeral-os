"""Phase 2.5 slice 2 + Phase 2.6 Closer C — ``isolated_workspace.*`` emitter coverage."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from sandbox.daemon.audit_buffer import get_audit_buffer
from sandbox.isolated_workspace._control_plane.pipeline_state import (
    IsolatedWorkspaceHandle,
    _PipelineConfig,
)
from sandbox.isolated_workspace.pipeline import IsolatedPipeline


class _Snapshot:
    lease_id = "lease-iws-1"
    manifest_version = 1
    root_hash = "root"
    layer_paths = ("/layers/L1",)


class _LayerStack:
    def __init__(self) -> None:
        self.released: list[str] = []

    def prepare_workspace_snapshot(self, *, request_id: str) -> _Snapshot:
        del request_id
        return _Snapshot()

    def release_lease(self, *, lease_id: str) -> bool:
        self.released.append(lease_id)
        return True


class _Network:
    initialized = False

    def initialize(self) -> None:
        return None

    def daemon_private_routes(self):  # noqa: ANN201
        return []

    def install_veth(self, *, handle_id: str, root_pid: int) -> None:
        del handle_id, root_pid
        return None

    def teardown_veth(self, _veth) -> None:
        return None


class _FakeRuntime:
    def spawn_ns_holder(self, handle, *, setup_timeout_s):
        del handle, setup_timeout_s
        return 1234

    def open_ns_fds(self, root_pid):
        del root_pid
        return {}

    async def mount_overlay(self, handle, *, layer_paths):
        del handle, layer_paths

    async def configure_dns(self, handle, *, fallback_dns):
        del handle, fallback_dns
        return True

    def signal_net_ready(self, handle, *, setup_timeout_s):
        del handle, setup_timeout_s

    def create_cgroup(self, handle: IsolatedWorkspaceHandle) -> Path:
        path = handle.scratch_dir / "cgroup"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def kill_holder(self, root_pid, *, grace_s):
        del root_pid, grace_s

    def run_in_handle(self, handle, *, argv, stdin=None, timeout_s=None):
        del handle, argv, stdin, timeout_s
        return 0, b"", b""


def _config(**overrides: Any) -> _PipelineConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "ttl_s": 0.0,
        "total_cap": 5,
        "upperdir_bytes": 1024,
        "memavail_fraction": 0.5,
        "setup_timeout_s": 1.0,
        "exit_grace_s": 0.05,
        "rfc1918_egress": "allow",
        "fallback_dns": "1.1.1.1",
        "sample_interval_s": 0.5,
    }
    values.update(overrides)
    return _PipelineConfig(**values)


def _pipeline(tmp_path: Path) -> IsolatedPipeline:
    return IsolatedPipeline(
        scratch_root=tmp_path,
        layer_stack=_LayerStack(),
        config=_config(),
        network=_Network(),
        runtime=_FakeRuntime(),
    )


_AUDIT_CURSOR = {"seq": -1}


def _drain_iws_events() -> list[dict[str, Any]]:
    buf = get_audit_buffer()
    snap = buf.pull(after_seq=_AUDIT_CURSOR["seq"], limit=10_000)
    events = snap.get("events", [])
    if events:
        _AUDIT_CURSOR["seq"] = int(events[-1]["seq"])
    return [
        evt
        for evt in events
        if str(evt.get("type", "")).startswith("isolated_workspace.")
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
async def test_isolated_workspace_lifecycle_emits_entered_exited(
    tmp_path: Path,
) -> None:
    pipeline = _pipeline(tmp_path)
    await pipeline.enter("agent-a")
    await pipeline.exit("agent-a")
    events = _drain_iws_events()
    types = [e["type"] for e in events]
    assert "isolated_workspace.entered" in types
    assert "isolated_workspace.exited" in types
    assert "isolated_workspace.orphan_check_completed" in types

    entered = next(e for e in events if e["type"] == "isolated_workspace.entered")
    exited = next(e for e in events if e["type"] == "isolated_workspace.exited")
    assert entered["payload"]["isolated_workspace"]["agent_id"] == "agent-a"
    assert entered["payload"]["isolated_workspace"]["workspace_mode"] == "isolated"
    assert (
        entered["payload"]["isolated_workspace"]["workspace_handle_id"]
        == exited["payload"]["isolated_workspace"]["workspace_handle_id"]
    )


@pytest.mark.asyncio
async def test_isolated_workspace_orphan_check_reports_zero_when_clean(
    tmp_path: Path,
) -> None:
    pipeline = _pipeline(tmp_path)
    await pipeline.enter("agent-b")
    await pipeline.exit("agent-b")
    events = _drain_iws_events()
    check = next(
        e for e in events if e["type"] == "isolated_workspace.orphan_check_completed"
    )
    section = check["payload"]["isolated_workspace"]
    assert section["orphan_holder_count"] == 0
    assert section["orphan_scratch_count"] == 0


@pytest.mark.asyncio
async def test_isolated_workspace_emitters_add_no_new_threads(
    tmp_path: Path,
) -> None:
    before = threading.active_count()
    pipeline = _pipeline(tmp_path)
    await pipeline.enter("agent-c")
    await pipeline.exit("agent-c")
    # Give the loop one tick to settle.
    time.sleep(0.01)
    after = threading.active_count()
    assert after <= before + 1, (
        f"unexpected thread growth before={before} after={after}"
    )
    events = _drain_iws_events()
    sample_events = [e for e in events if e["type"] == "isolated_workspace.sampled"]
    # No sampler task is started because the pipeline never had `initialize()`
    # called — confirms the dedicated sampler task (Phase 2.6 Closer C) is the
    # sole source of sample-lane events.
    assert sample_events == []


# ----------------------------------------------------------------------
# Phase 2.6 Closer C — dedicated sampler task
# ----------------------------------------------------------------------


def _enabled_pipeline(tmp_path: Path, **overrides: Any) -> IsolatedPipeline:
    pipeline = IsolatedPipeline(
        scratch_root=tmp_path,
        layer_stack=_LayerStack(),
        config=_config(**overrides),
        network=_Network(),
        runtime=_FakeRuntime(),
    )
    # The real ``reap_startup_orphans`` shells out to ``ip`` which isn't
    # available on darwin CI runners. Bypass for these unit tests; the
    # orphan reaper has its own test coverage.
    async def _noop() -> None:
        return None

    pipeline.reap_startup_orphans = _noop  # type: ignore[method-assign]
    return pipeline


@pytest.mark.asyncio
async def test_isolated_workspace_sampler_emits_at_500ms_cadence(
    tmp_path: Path,
) -> None:
    """Closer C: dedicated sampler task ticks at ``sample_interval_s`` cadence."""
    pipeline = _enabled_pipeline(tmp_path, sample_interval_s=0.05)
    await pipeline.initialize()
    try:
        await pipeline.enter("agent-sampler")
        # 5 × 50 ms = 250 ms; expect at least 3 sampler ticks.
        await asyncio.sleep(0.25)
        events = _drain_iws_events()
        samples = [e for e in events if e["type"] == "isolated_workspace.sampled"]
        assert len(samples) >= 3, f"expected ≥3 samples, got {len(samples)}"
        for evt in samples:
            assert evt["lane"] == "sample"
            section = evt["payload"]["isolated_workspace"]
            assert section["agent_id"] == "agent-sampler"
    finally:
        await pipeline.shutdown()


@pytest.mark.asyncio
async def test_isolated_workspace_sampler_stops_on_shutdown(
    tmp_path: Path,
) -> None:
    """Closer C: ``shutdown()`` cancels the sampler task — no further samples."""
    pipeline = _enabled_pipeline(tmp_path, sample_interval_s=0.05)
    await pipeline.initialize()
    await pipeline.enter("agent-shutdown")
    await asyncio.sleep(0.1)
    await pipeline.shutdown()
    _drain_iws_events()  # discard everything emitted up to shutdown
    await asyncio.sleep(0.1)
    after_shutdown = _drain_iws_events()
    leftover = [
        e for e in after_shutdown if e["type"] == "isolated_workspace.sampled"
    ]
    assert leftover == []
    # The sampler task's reference is preserved (parity with how
    # :attr:`_ttl_task` is left after shutdown). The coroutine catches
    # ``CancelledError`` and returns cleanly, so the task reports
    # ``done()`` but not ``cancelled()``.
    assert pipeline._sampler_task is not None  # noqa: SLF001
    assert pipeline._sampler_task.done()  # noqa: SLF001


@pytest.mark.asyncio
async def test_isolated_workspace_sampler_skips_when_feature_disabled(
    tmp_path: Path,
) -> None:
    """Closer C: ``enabled=False`` → no sampler task created (per ``asyncio.all_tasks()``)."""
    pipeline = _enabled_pipeline(
        tmp_path, enabled=False, sample_interval_s=0.05
    )
    await pipeline.initialize()
    try:
        assert pipeline._sampler_task is None  # noqa: SLF001
    finally:
        await pipeline.shutdown()


@pytest.mark.asyncio
async def test_isolated_workspace_orphan_check_after_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closer C backfill: orphan check reports nonzero residue counts.

    Closes the phase-2 §Tests gap left by 2.5 (which only pinned the
    zero-orphan happy path). Simulates a holder PID that survives exit
    (``os.kill(pid, 0)`` succeeds) AND a scratch dir that survives the
    teardown's ``shutil.rmtree``.
    """
    pipeline = _pipeline(tmp_path)
    await pipeline.enter("agent-residue")

    from sandbox.isolated_workspace._control_plane import workspace_handle_lifecycle as wsl

    # Holder pid appears live — os.kill returns without raising.
    monkeypatch.setattr(wsl.os, "kill", lambda pid, sig: None)
    # Scratch dir survives — block the teardown's rmtree of THIS test's scratch
    # dir only. Other rmtree call sites are unrelated test scope.
    original_rmtree = shutil.rmtree

    def _no_rmtree(path: Any, ignore_errors: bool = True, **kwargs: Any) -> None:
        del path, ignore_errors, kwargs
        return None

    monkeypatch.setattr(shutil, "rmtree", _no_rmtree)

    try:
        await pipeline.exit("agent-residue")
        events = _drain_iws_events()
        exited = next(e for e in events if e["type"] == "isolated_workspace.exited")
        check = next(
            e
            for e in events
            if e["type"] == "isolated_workspace.orphan_check_completed"
        )
        for emit in (exited, check):
            section = emit["payload"]["isolated_workspace"]
            assert section["orphan_holder_count"] > 0
            assert section["orphan_scratch_count"] > 0
    finally:
        # Restore for any pytest scratch cleanup downstream.
        monkeypatch.setattr(shutil, "rmtree", original_rmtree)
