"""Unit coverage for SWE-EVO live-run locking."""

from __future__ import annotations

from pathlib import Path

from task_center_runner.benchmarks.sweevo import fixtures as sweevo_adapter


def test_lock_slug_keeps_instance_ids_filesystem_safe() -> None:
    assert (
        sweevo_adapter._lock_slug("dask__dask_2023.3.2_2023.4.0")
        == "dask__dask_2023.3.2_2023.4.0"
    )
    assert sweevo_adapter._lock_slug("../bad id") == "..-bad-id"
    assert sweevo_adapter._lock_slug("   ") == "default"


def test_sweevo_session_lock_writes_instance_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sweevo_adapter, "_LOCK_DIR", tmp_path)

    handle = sweevo_adapter._acquire_sweevo_session_lock("instance/one")
    try:
        lock_path = tmp_path / "sweevo-instance-one.lock"
        assert lock_path.exists()
        assert "instance=instance/one" in lock_path.read_text(encoding="utf-8")
    finally:
        sweevo_adapter._release_sweevo_session_lock(handle)


def test_sweevo_session_lock_is_reentrant_in_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sweevo_adapter, "_LOCK_DIR", tmp_path)

    first = sweevo_adapter._acquire_sweevo_session_lock("instance/one")
    second = sweevo_adapter._acquire_sweevo_session_lock("instance/one")
    try:
        assert first.path == second.path
        assert sweevo_adapter._HELD_SWEEVO_LOCKS[first.path][1] == 2
    finally:
        sweevo_adapter._release_sweevo_session_lock(second)
        sweevo_adapter._release_sweevo_session_lock(first)
    assert first.path not in sweevo_adapter._HELD_SWEEVO_LOCKS
