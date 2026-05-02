"""Unit tests for :class:`TimingHarness` — runs in the default suite.

These exercise the harness API contract: step timing, metadata recording,
report formatting, JSON dump shape + atomicity, and baseline comparison.
The live E2E baseline test (``test_live_ci_phase0_baseline.py``) shares
the same harness; if these unit tests pass, that test only needs to verify
the live integration.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from ._timing_harness import TimingHarness


def _harness() -> TimingHarness:
    return TimingHarness(phase=0, test_name="harness_unit")


def test_step_records_elapsed_within_bounds() -> None:
    h = _harness()
    with h.step("sleep_50ms"):
        time.sleep(0.05)
    payload = h.to_payload()
    elapsed = payload["steps"][0]["elapsed_s"]
    # Generous tolerance (~50ms either side) to avoid CI flake.
    assert elapsed >= 0.04
    assert elapsed < 0.5


def test_record_attaches_metadata_to_existing_step() -> None:
    h = _harness()
    with h.step("read_files"):
        pass
    h.record("read_files", count=10, bytes_=2048)
    step = h.to_payload()["steps"][0]
    assert step["count"] == 10
    assert step["bytes"] == 2048


def test_record_creates_bare_entry_when_step_missing() -> None:
    h = _harness()
    h.record("metadata_only", count=5, bytes_=128)
    payload = h.to_payload()
    assert len(payload["steps"]) == 1
    assert payload["steps"][0]["name"] == "metadata_only"
    assert payload["steps"][0]["count"] == 5
    assert payload["steps"][0]["bytes"] == 128
    assert payload["steps"][0]["elapsed_s"] == 0.0


def _harness_with_fixed_steps(monkeypatch: pytest.MonkeyPatch) -> TimingHarness:
    """Inject a deterministic ``perf_counter`` to make report output stable."""
    h = TimingHarness(phase=0, test_name="baseline_timings")

    fake_clock = iter(
        [
            0.0,
            1.234,  # sandbox_create
            2.0,
            2.456,  # ci_runtime_upload
            3.0,
            3.789,  # daemon_spawn
        ]
    )

    def fake_perf() -> float:
        return next(fake_clock)

    from . import _timing_harness as harness_mod

    monkeypatch.setattr(harness_mod.time, "perf_counter", fake_perf)
    with h.step("sandbox_create"):
        pass
    with h.step("ci_runtime_upload"):
        pass
    h.record("ci_runtime_upload", count=5, bytes_=12_340)
    with h.step("daemon_spawn"):
        pass
    return h


def test_report_renders_canonical_format(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _harness_with_fixed_steps(monkeypatch)
    text = h.report()
    lines = text.splitlines()
    assert lines[0] == "=== Phase 0 E2E timing breakdown for baseline_timings ==="
    # Each step renders with a `<name>:` prefix and a `<elapsed>s` suffix.
    assert any("sandbox_create:" in line and "1.234s" in line for line in lines)
    assert any(
        "ci_runtime_upload:" in line
        and "0.456s" in line
        and "12.1 KB" in line
        and "5 files" in line
        for line in lines
    )
    assert any("daemon_spawn:" in line and "0.789s" in line for line in lines)
    # Total = 1.234 + 0.456 + 0.789 = 2.479
    assert lines[-1] == "--- TOTAL: 2.479s ---"


def test_dump_json_writes_documented_shape(tmp_path: Path) -> None:
    h = _harness()
    with h.step("first"):
        pass
    with h.step("second"):
        pass
    h.record("first", count=3, bytes_=1024)

    out = h.dump_json(dir_=tmp_path)

    assert out.exists()
    assert out.parent == tmp_path
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["phase"] == 0
    assert payload["test_name"] == "harness_unit"
    assert isinstance(payload["timestamp"], str) and "T" in payload["timestamp"]
    assert payload["total_s"] >= 0.0
    assert isinstance(payload["steps"], list)
    assert len(payload["steps"]) == 2
    assert payload["steps"][0]["name"] == "first"
    assert payload["steps"][0]["count"] == 3
    assert payload["steps"][0]["bytes"] == 1024
    assert payload["steps"][1]["name"] == "second"
    assert payload["steps"][1]["count"] is None
    assert payload["steps"][1]["bytes"] is None


def test_dump_json_is_atomic(tmp_path: Path) -> None:
    """Tmp file is removed after rename; only the final target exists."""
    h = _harness()
    with h.step("only"):
        pass
    out = h.dump_json(dir_=tmp_path)
    assert out.exists()
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def _baseline(tmp_path: Path) -> Path:
    """Build a small baseline JSON file by hand (independent of dump_json)."""
    payload: dict[str, Any] = {
        "phase": 0,
        "test_name": "baseline_timings",
        "timestamp": "2026-05-02T00:00:00+00:00",
        "steps": [
            {"name": "sandbox_create", "elapsed_s": 1.234, "count": None, "bytes": None},
            {"name": "query_symbols_first", "elapsed_s": 0.200, "count": None, "bytes": None},
            {"name": "svc_cmd", "elapsed_s": 1.420, "count": None, "bytes": None},
        ],
        "total_s": 2.854,
    }
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_compare_to_signed_deltas_and_new_keys(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    h = TimingHarness(phase=1, test_name="phase1_timings")
    # Force exact elapsed_s values via record() so the test is deterministic.
    h.record("sandbox_create")
    h._step_index["sandbox_create"].elapsed_s = 1.234  # type: ignore[attr-defined]
    h.record("query_symbols_first")
    h._step_index["query_symbols_first"].elapsed_s = 0.045
    h.record("svc_cmd")
    h._step_index["svc_cmd"].elapsed_s = 0.635
    h.record("daemon_spawn")
    h._step_index["daemon_spawn"].elapsed_s = 0.789

    out = h.compare_to(baseline)
    assert out.startswith("--- vs Phase 0 baseline (baseline.json) ---")
    # Unchanged step: no signed delta.
    assert "sandbox_create:" in out
    # Faster step: negative delta with "faster" annotation.
    faster_lines = [line for line in out.splitlines() if "query_symbols_first:" in line]
    assert faster_lines and "-0.155s" in faster_lines[0]
    assert "faster" in faster_lines[0]
    # svc_cmd faster too.
    svc_lines = [line for line in out.splitlines() if "svc_cmd:" in line]
    assert svc_lines and "-0.785s" in svc_lines[0]
    # NEW key annotation.
    new_lines = [line for line in out.splitlines() if "daemon_spawn:" in line]
    assert new_lines and "NEW cost" in new_lines[0]


def test_compare_to_marks_removed_keys(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    h = TimingHarness(phase=1, test_name="phase1_timings")
    # Only contribute one of the baseline's three keys.
    h.record("sandbox_create")
    h._step_index["sandbox_create"].elapsed_s = 1.000

    out = h.compare_to(baseline)
    removed_lines = [line for line in out.splitlines() if "(REMOVED)" in line]
    removed_names = {line.split(":", 1)[0].strip() for line in removed_lines}
    assert removed_names == {"query_symbols_first", "svc_cmd"}
