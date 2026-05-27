"""Phase 2.5 slice 1 — overlay_workspace emitter coverage.

Asserts the overlay_workspace.{mounted,cleaned,cleanup_failed} events are
emitted with shared causal-chain identifiers from
``sandbox.overlay.lifecycle.{acquire,destroy}``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sandbox.daemon.audit_buffer import get_audit_buffer
from sandbox.overlay import lifecycle as overlay_lifecycle
from sandbox.overlay import writable_dirs as overlay_writable_dirs


@dataclass
class _FakeSnapshot:
    lease_id: str
    layer_paths: tuple[Path, ...]
    manifest: object | None = None
    manifest_version: int = 7
    root_hash: str = "deadbeef"
    timings: dict[str, float] | None = None


class _FakeLayerStack:
    def __init__(self) -> None:
        self.released: list[str] = []

    def prepare_workspace_snapshot(self, request_id: str) -> _FakeSnapshot:
        return _FakeSnapshot(
            lease_id=f"lease-{request_id}",
            layer_paths=(Path("/tmp/layer-a"),),
        )

    def release_lease(self, *, lease_id: str) -> None:
        self.released.append(lease_id)


_AUDIT_CURSOR = {"seq": -1}


def _drain_overlay_events() -> list[dict[str, Any]]:
    buf = get_audit_buffer()
    snap = buf.pull(after_seq=_AUDIT_CURSOR["seq"], limit=10_000)
    events = snap.get("events", [])
    if events:
        _AUDIT_CURSOR["seq"] = int(events[-1]["seq"])
    return [
        evt
        for evt in events
        if str(evt.get("type", "")).startswith("overlay_workspace.")
    ]


@pytest.fixture(autouse=True)
def _reset_audit_cursor() -> None:
    buf = get_audit_buffer()
    # Advance cursor past whatever is currently retained so each test starts
    # at a known empty slice without an infinite drain loop.
    cursor = -1
    while True:
        snap = buf.pull(after_seq=cursor, limit=10_000)
        events = snap.get("events", [])
        if not events:
            break
        cursor = int(events[-1]["seq"])
    _AUDIT_CURSOR["seq"] = cursor
    yield


@pytest.fixture
def _writable_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(overlay_writable_dirs, "OVERLAY_WRITABLE_ROOT", root)
    return root


def test_overlay_workspace_emits_mounted_and_cleaned(_writable_root: Path) -> None:
    layer_stack = _FakeLayerStack()
    handle = overlay_lifecycle.acquire(
        layer_stack,
        invocation_id="op-123",
        workspace_root="/testbed",
    )
    assert handle.operation_id == "op-123"

    asyncio.run(overlay_lifecycle.release_overlay(handle))

    events = _drain_overlay_events()
    types = [e["type"] for e in events]
    assert "overlay_workspace.mounted" in types
    assert "overlay_workspace.cleaned" in types

    mounted = next(e for e in events if e["type"] == "overlay_workspace.mounted")
    cleaned = next(e for e in events if e["type"] == "overlay_workspace.cleaned")
    assert mounted["payload"]["overlay_workspace"]["operation_id"] == "op-123"
    assert cleaned["payload"]["overlay_workspace"]["operation_id"] == "op-123"
    assert (
        mounted["payload"]["overlay_workspace"]["workspace_handle_id"]
        == cleaned["payload"]["overlay_workspace"]["workspace_handle_id"]
    )
    assert mounted["payload"]["overlay_workspace"]["workspace_mode"] == "ephemeral"
    assert cleaned["payload"]["overlay_workspace"]["scratch_removed"] is True


def test_overlay_workspace_cleanup_failed_emits_failure_kind(
    _writable_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer_stack = _FakeLayerStack()
    handle = overlay_lifecycle.acquire(
        layer_stack,
        invocation_id="op-fail",
        workspace_root="/testbed",
    )

    real_rmtree = overlay_lifecycle.shutil.rmtree

    def _exploding_rmtree(path, ignore_errors=False, onerror=None):
        if str(path) == str(handle.run_dir):
            if onerror is not None:
                import sys

                try:
                    raise PermissionError("boom")
                except PermissionError:
                    onerror(real_rmtree, str(path), sys.exc_info())
            # No-op so run_dir persists, forcing scratch_removed=False.
            return
        return real_rmtree(path, ignore_errors=ignore_errors, onerror=onerror)

    monkeypatch.setattr(overlay_lifecycle.shutil, "rmtree", _exploding_rmtree)

    asyncio.run(overlay_lifecycle.release_overlay(handle))

    events = _drain_overlay_events()
    failed = [e for e in events if e["type"] == "overlay_workspace.cleanup_failed"]
    assert failed, f"expected cleanup_failed, got types={[e['type'] for e in events]}"
    section = failed[0]["payload"]["overlay_workspace"]
    assert section["cleanup_failure_kind"]
    assert section["scratch_removed"] is False
