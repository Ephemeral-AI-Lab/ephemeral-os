"""Phase 3: async perf-report writer + failure isolation.

These tests cover the ``_write_perf_report_safe`` wrapper directly. End-to-end
integration through ``run_scenario`` lives in the live_e2e mock-scenario suite.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from task_center_runner.audit import performance_report as perf_module
from task_center_runner.audit.performance_report import (
    REPORT_SCHEMA,
    _write_perf_report_safe,
)


@pytest.fixture
def minimal_snapshot() -> dict:
    """An empty-but-well-shaped performance snapshot."""
    return {
        "duration_s": 0.0,
        "started_at": "1970-01-01T00:00:00+00:00",
        "ended_at": "1970-01-01T00:00:00+00:00",
        "tools": {"per_tool": {}, "tool_calls_total": 0, "tool_errors_total": 0, "slowest_calls": []},
        "sandbox": {"events": [], "families": {}, "timing_keys": {}, "non_duration_observations": {}},
    }


@pytest.mark.asyncio
async def test_write_perf_report_safe_produces_report_file(
    tmp_path: Path, minimal_snapshot: dict
) -> None:
    result = await _write_perf_report_safe(tmp_path, minimal_snapshot)
    assert result == tmp_path / "performance_report.json"
    assert result.exists()
    import json

    payload = json.loads(result.read_text())
    assert payload["schema"] == REPORT_SCHEMA == "task_center_runner.performance_report.v2"


@pytest.mark.asyncio
async def test_write_perf_report_safe_swallows_writer_failures(
    tmp_path: Path,
    minimal_snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per plan §5: perf-report failures must never propagate."""

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("perf-report intentionally broken")

    monkeypatch.setattr(perf_module, "write_performance_reports", boom)
    caplog.set_level(logging.WARNING, logger=perf_module.__name__)

    result = await _write_perf_report_safe(tmp_path, minimal_snapshot)

    # File does NOT exist because the writer raised; the wrapper still returns
    # the expected path so the caller can detect absence.
    assert result == tmp_path / "performance_report.json"
    assert not result.exists()

    matching = [
        rec for rec in caplog.records
        if "Async perf-report failed" in rec.getMessage()
    ]
    assert matching, "expected a WARNING log entry from the safe wrapper"


def test_report_schema_constant_is_v2() -> None:
    """Phase 3 bumps the perf-report schema string."""
    assert REPORT_SCHEMA == "task_center_runner.performance_report.v2"
