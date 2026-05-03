"""Unit tests for :class:`TimingHarness` — runs in the default suite.

These exercise the harness API contract: step timing, metadata recording,
report formatting, JSON dump shape + atomicity, and baseline comparison.
Live E2E phase tests share the same harness; if these unit tests pass, those
tests only need to verify live integration.
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
            {"name": "workspace_ready_first", "elapsed_s": 0.200, "count": None, "bytes": None},
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
    h.record("workspace_ready_first")
    h._step_index["workspace_ready_first"].elapsed_s = 0.045
    h.record("svc_cmd")
    h._step_index["svc_cmd"].elapsed_s = 0.635
    h.record("daemon_spawn")
    h._step_index["daemon_spawn"].elapsed_s = 0.789

    out = h.compare_to(baseline)
    assert out.startswith("--- vs Phase 0 baseline (baseline.json) ---")
    # Unchanged step: no signed delta.
    assert "sandbox_create:" in out
    # Faster step: negative delta with "faster" annotation.
    faster_lines = [line for line in out.splitlines() if "workspace_ready_first:" in line]
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
    assert removed_names == {"workspace_ready_first", "svc_cmd"}


# ---------------------------------------------------------------------------
# Phase 3.5 — step_repeat / sample_rss_mb / sample_fds
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, stdout: str = "", result: str = "") -> None:
        self.stdout = stdout
        self.result = result


class _StubTransport:
    """Sync transport stub that returns a canned response per substring match."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def exec(self, sandbox_id: str, command: str, timeout: int = 30) -> _StubResponse:
        self.calls.append((sandbox_id, command))
        for needle, payload in self._responses.items():
            if needle in command:
                return _StubResponse(stdout=payload)
        return _StubResponse()


def test_step_repeat_collects_distribution() -> None:
    h = TimingHarness(phase=3.5, test_name="distribution_smoke")
    for step in h.step_repeat("op", n=20):
        with step:
            pass
    assert "op" in h.distributions
    stats = h.distributions["op"]
    assert stats["n"] == 20
    assert stats["min"] <= stats["p50"] <= stats["p99"] <= stats["max"]


def test_record_distribution_collects_concurrent_samples() -> None:
    h = TimingHarness(phase=3.5, test_name="concurrent_distribution")

    stats = h.record_distribution("svc_cmd_50x_latency", [0.4, 0.9, 0.2, 1.1])

    assert stats == h.distributions["svc_cmd_50x_latency"]
    assert stats["n"] == 4
    assert stats["min"] == 0.2
    assert stats["max"] == 1.1
    payload = h.to_payload()
    assert payload["distributions"]["svc_cmd_50x_latency"]["p50"] == 0.4


def test_step_repeat_percentiles_monotonic_on_known_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a deterministic perf_counter sequence, percentiles match expectations."""
    h = TimingHarness(phase=3.5, test_name="dist_known")
    # Provide pairs (start, end) such that elapsed = 1, 2, …, 100.
    times: list[float] = []
    for i in range(1, 101):
        times.append(0.0)
        times.append(float(i))
    counter = iter(times)
    from . import _timing_harness as harness_mod

    monkeypatch.setattr(harness_mod.time, "perf_counter", lambda: next(counter))
    for step in h.step_repeat("op", n=100):
        with step:
            pass
    stats = h.distributions["op"]
    assert stats["min"] == 1.0
    assert stats["max"] == 100.0
    assert stats["p50"] == 50.0
    assert stats["p95"] == 95.0
    assert stats["p99"] == 99.0


def test_sample_rss_mb_parses_vmrss() -> None:
    h = TimingHarness(phase=3.5, test_name="rss_smoke")
    # /proc/<pid>/status puts VmRSS in kB.
    transport = _StubTransport({"VmRSS": "VmRSS:\t   65432 kB\n"})
    mb = h.sample_rss_mb("rss_at_start", transport, sandbox_id="sb", pid=4242)
    assert mb == round(65432 / 1024.0, 2)
    assert h.values["rss_at_start"] == mb


def test_sample_fds_parses_count() -> None:
    h = TimingHarness(phase=3.5, test_name="fds_smoke")
    transport = _StubTransport({"/proc/": "  17\n"})
    n = h.sample_fds("fds_at_start", transport, sandbox_id="sb", pid=4242)
    assert n == 17
    assert h.values["fds_at_start"] == 17.0


def test_report_renders_distributions_and_resource_samples() -> None:
    h = TimingHarness(phase=3.5, test_name="report_extras")
    for step in h.step_repeat("write_file", n=5):
        with step:
            pass
    h.values["rss_at_start"] = 100.0
    h.values["fds_at_start"] = 30.0
    out = h.report()
    assert "--- DISTRIBUTIONS ---" in out
    assert "write_file:" in out
    assert "p50=" in out and "p95=" in out and "p99=" in out
    assert "(5 samples)" in out
    assert "--- RESOURCE SAMPLES ---" in out
    assert "rss_at_start" in out
    assert "fds_at_start" in out


def test_dump_json_includes_distributions_and_values(tmp_path: Path) -> None:
    h = TimingHarness(phase=3.5, test_name="dump_extras")
    for step in h.step_repeat("op", n=4):
        with step:
            pass
    h.values["rss_at_end"] = 144.0
    out = h.dump_json(dir_=tmp_path)
    payload = json.loads(out.read_text())
    assert "distributions" in payload
    assert "op" in payload["distributions"]
    assert payload["values"]["rss_at_end"] == 144.0
