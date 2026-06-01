"""Tests for the rotating + gzipping sandbox_events.jsonl sink."""

from __future__ import annotations

import json

from task_center_runner.audit.sandbox_events_sink import (
    RotatingJsonlSink,
    iter_rotated_jsonl,
)


def test_sink_rotates_and_caps_history(tmp_path) -> None:
    path = tmp_path / "sandbox_events.jsonl"
    sink = RotatingJsonlSink(path, rotation_bytes=512, retention_files=3)
    # Each event ~80B; ~12 events fits in 512B before rotation.
    for i in range(80):
        sink.append_event({"i": i, "filler": "x" * 60})

    rotated_gz = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".gz"))
    assert 1 <= len(rotated_gz) <= 3
    assert path.exists()
    # Live file is bounded.
    assert path.stat().st_size <= 512 + 200


def test_iter_jsonl_concatenates_rotated_gzipped_history(tmp_path) -> None:
    path = tmp_path / "sandbox_events.jsonl"
    sink = RotatingJsonlSink(path, rotation_bytes=512, retention_files=200)
    for i in range(60):
        sink.append_event({"i": i, "payload": "y" * 50})

    seen = [row.get("i") for row in iter_rotated_jsonl(path)]
    # All 60 events must come back, in original order, across rotated + live.
    assert seen == list(range(60))
    # At least one rotation occurred.
    rotated = [p for p in tmp_path.iterdir() if p.name.endswith(".gz")]
    assert rotated


def test_sink_rotation_path_stable_under_eos_tier_run_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EOS_TIER_RUN_ID", "test-xyz")
    artifact_root = tmp_path / "test-xyz"
    artifact_root.mkdir()
    path = artifact_root / "sandbox_events.jsonl"

    sink = RotatingJsonlSink(path, rotation_bytes=200, retention_files=8)
    for i in range(30):
        sink.append_event({"i": i, "filler": "z" * 40})

    # Simulate restart: a brand new sink at the same path keeps reading the
    # same rotated history.
    sink2 = RotatingJsonlSink(path, rotation_bytes=200, retention_files=8)
    sink2.append_event({"i": 999})

    seen = [row.get("i") for row in iter_rotated_jsonl(path)]
    assert 999 in seen
    assert all(parent == artifact_root for parent in [p.parent for p in artifact_root.iterdir()])


def test_jsonl_round_trip(tmp_path) -> None:
    path = tmp_path / "sandbox_events.jsonl"
    sink = RotatingJsonlSink(path, rotation_bytes=10_000)
    sink.append_event({"event_type": "tool_call.started", "payload": {"tool_name": "x"}})
    contents = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 1
    row = json.loads(contents[0])
    assert row["event_type"] == "tool_call.started"
