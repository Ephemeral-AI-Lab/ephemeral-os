"""Stale layer-stack staging fence tests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sandbox.runtime import layer_stack_server


@pytest.fixture(autouse=True)
def _clear_layer_stack_server_state() -> None:
    layer_stack_server._clear_layer_stack_server_caches_for_tests()
    try:
        yield
    finally:
        layer_stack_server._clear_layer_stack_server_caches_for_tests()


def test_stale_staging_fence_removes_old_dirs_and_keeps_fresh_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = time.time()
    monkeypatch.setattr(layer_stack_server, "_DAEMON_STARTED_AT", started_at)
    staging = tmp_path / "stack" / "staging"
    old_layer = staging / "L000123-old.staging"
    old_occ = staging / "occ-commit-old"
    fresh = staging / "occ-commit-fresh"
    for path in (old_layer, old_occ, fresh):
        path.mkdir(parents=True)
        (path / "payload.txt").write_text("x", encoding="utf-8")
    os.utime(old_layer, (started_at - 10, started_at - 10))
    os.utime(old_occ, (started_at - 10, started_at - 10))
    os.utime(fresh, (started_at + 10, started_at + 10))

    result = layer_stack_server.fence_stale_staging(tmp_path / "stack")

    assert result["success"] is True
    assert result["inspected_dirs"] == 3
    assert result["fenced_dirs"] == 2
    assert not old_layer.exists()
    assert not old_occ.exists()
    assert fresh.exists()
    assert sorted(Path(path).name for path in result["fenced_paths"]) == [
        "L000123-old.staging",
        "occ-commit-old",
    ]


def test_stale_staging_fence_second_call_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = time.time()
    monkeypatch.setattr(layer_stack_server, "_DAEMON_STARTED_AT", started_at)
    old_dir = tmp_path / "stack" / "staging" / "occ-commit-old"
    old_dir.mkdir(parents=True)
    os.utime(old_dir, (started_at - 10, started_at - 10))

    first = layer_stack_server.fence_stale_staging(tmp_path / "stack")
    second = layer_stack_server.fence_stale_staging(tmp_path / "stack")

    assert first["fenced_dirs"] == 1
    assert second["fenced_dirs"] == 0
    assert second["inspected_dirs"] == 0


def test_get_layer_stack_manager_fences_once_per_resolved_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_fence(layer_stack_root: str) -> dict[str, object]:
        calls.append(layer_stack_root)
        return {"success": True}

    monkeypatch.setattr(layer_stack_server, "fence_stale_staging", fake_fence)

    manager_a = layer_stack_server.get_layer_stack_manager(tmp_path / "stack")
    manager_b = layer_stack_server.get_layer_stack_manager(tmp_path / "stack")

    assert manager_a is manager_b
    assert calls == [str((tmp_path / "stack").resolve(strict=False))]
