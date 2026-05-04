"""Tests for sandbox-side upperdir capture helpers."""

from __future__ import annotations

from pathlib import Path

from sandbox.runtime.overlay_capture_runtime.capture import build_upper_change, walk_upperdir


def test_upperdir_walk_emits_raw_changes_without_git_classification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lower = tmp_path / "lower"
    upper = tmp_path / "upper"
    lower.mkdir()
    upper.mkdir()
    (lower / "app.py").write_text("old\n", encoding="utf-8")
    (upper / "app.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("sandbox.runtime.overlay_capture_runtime.capture._NS_LOWER", str(lower))

    changes = tuple(build_upper_change(entry) for entry in walk_upperdir(str(upper)))

    assert len(changes) == 1
    assert changes[0].rel == "app.py"
    assert changes[0].base_bytes == b"old\n"
    assert changes[0].upper_bytes == b"new\n"
    assert not hasattr(changes[0], "gitignore")
    assert not hasattr(changes[0], "direct_merge")
