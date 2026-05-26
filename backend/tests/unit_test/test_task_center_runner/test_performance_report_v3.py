"""Phase 3 — performance report V3 layout + release gates.

Covers the 10 tests listed in
``docs/daemon-audit-pull-consolidation-v3/phase-3-report-and-release-gates.md``
§Tests plus the engine dual-disable startup check.

The synthetic events here intentionally use only ``payload.<section>``
keys; ``payload.daemon_event`` is left out so the "consumer reads
promoted payload section" invariant is observable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from task_center_runner.audit.daemon_event_normalizer import FORENSIC_RAW_ENV
from task_center_runner.audit.performance_report import (
    build_performance_report,
    render_performance_report_markdown,
)
from task_center_runner.audit.release_gates import (
    evaluate_audit_overhead_gate,
    evaluate_isolated_workspace_gate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _empty_tool_performance() -> dict[str, Any]:
    return {
        "tool_calls_total": 0,
        "tool_errors_total": 0,
        "per_tool": {},
        "slowest_calls": [],
    }


def _tool_call_finished(
    *,
    seq: int,
    tool_name: str,
    workspace_mode: str = "ephemeral",
    total_ms: float = 10.0,
    rollup: dict[str, float] | None = None,
) -> dict[str, Any]:
    rollup = rollup or {
        "queued_ms": 1.0,
        "exec_ms": total_ms - 2.0,
        "capture_ms": 0.5,
        "release_ms": 0.5,
    }
    return {
        "event_type": "tool_call.finished",
        "schema": "sandbox.daemon.audit.pull.v1",
        "lane": "normal",
        "seq": seq,
        "payload": {
            "tool_call": {
                "tool_id": f"t{seq}",
                "tool_name": tool_name,
                "workspace_mode": workspace_mode,
                "total_ms": total_ms,
                "phase_totals_rollup": rollup,
            }
        },
    }


def _isolated_workspace_exited(
    *,
    seq: int,
    handle_id: str = "h1",
    orphan_holder: int = 0,
    orphan_cgroup: int = 0,
    orphan_scratch: int = 0,
    holder_pid_alive: bool = False,
) -> dict[str, Any]:
    return {
        "event_type": "isolated_workspace.exited",
        "schema": "sandbox.daemon.audit.pull.v1",
        "lane": "critical",
        "seq": seq,
        "payload": {
            "isolated_workspace": {
                "workspace_handle_id": handle_id,
                "workspace_mode": "isolated",
                "orphan_holder_count": orphan_holder,
                "orphan_cgroup_count": orphan_cgroup,
                "orphan_scratch_count": orphan_scratch,
                "holder_pid_alive": holder_pid_alive,
            }
        },
    }


def _isolated_workspace_entered(*, seq: int, handle_id: str = "h1") -> dict[str, Any]:
    return {
        "event_type": "isolated_workspace.entered",
        "schema": "sandbox.daemon.audit.pull.v1",
        "lane": "critical",
        "seq": seq,
        "payload": {
            "isolated_workspace": {
                "workspace_handle_id": handle_id,
                "workspace_mode": "isolated",
            }
        },
    }


def _plugin_tool_invoked(
    *,
    seq: int,
    plugin_id: str,
    plugin_kind: str,
    duration_ms: float = 5.0,
) -> dict[str, Any]:
    return {
        "event_type": "plugin.tool_invoked",
        "schema": "sandbox.daemon.audit.pull.v1",
        "lane": "normal",
        "seq": seq,
        "payload": {
            "plugin": {
                "plugin_id": plugin_id,
                "plugin_kind": plugin_kind,
                "duration_ms": duration_ms,
            }
        },
    }


# ---------------------------------------------------------------------------
# Test 1 — MD layout structure (schema-shape, not golden-file diff)
# ---------------------------------------------------------------------------


def test_performance_report_md_layout_structure(tmp_path: Path) -> None:
    """All 13 §-headers present in order; §2 and §5 column shapes match."""
    _write_jsonl(
        tmp_path / "sandbox_events.jsonl",
        [
            _tool_call_finished(seq=1, tool_name="read_file"),
            _plugin_tool_invoked(seq=2, plugin_id="lsp-py", plugin_kind="language_server"),
        ],
    )
    report = build_performance_report(tmp_path, _empty_tool_performance())
    md = render_performance_report_markdown(report)

    headers = [
        "## 1. Summary",
        "## 2. Per-tool timing (foreground, split by workspace_mode)",
        "## 3. Per-tool phase breakdown (top-10 by total_ms)",
        "## 4. Background tool calls",
        "## 5. Plugin activity (generic; per plugin_id × plugin_kind)",
        "## 6. Overlay workspace — ephemeral vs isolated",
        "## 7. LayerStack",
        "## 8. OCC",
        "## 9. Isolated workspace (release gate surface)",
        "## 10. OS resource (process / cgroup)",
        "## 11. Daemon audit pull",
        "## 12. Audit path overhead (release gate)",
        "## 13. Warnings",
    ]
    indices = [md.find(header) for header in headers]
    assert all(idx >= 0 for idx in indices), (
        f"missing headers at positions: {dict(zip(headers, indices, strict=False))}"
    )
    assert indices == sorted(indices), (
        "headers must appear in §1..§13 order; got indices {indices}"
    )

    # §2 column header — strict regex from spec
    expected_section_2_col_regex = (
        r"^\|\s*tool_name\s*\|\s*workspace_mode\s*\|\s*calls\s*\|"
        r".*total_ms p50/95/99\s*\|$"
    )
    assert _line_matches_in_section(md, "## 2.", re.compile(expected_section_2_col_regex)), (
        "§2 column header missing required schema"
    )

    # §5 column header — strict regex from spec
    expected_section_5_col_regex = (
        r"^\|\s*plugin_id\s*\|\s*plugin_kind\s*\|\s*invocations\s*\|"
        r".*peak_resident_bytes\s*\|\s*errors\s*\|$"
    )
    assert _line_matches_in_section(md, "## 5.", re.compile(expected_section_5_col_regex)), (
        "§5 column header missing required schema"
    )


def _line_matches_in_section(md: str, section_header: str, pattern: re.Pattern) -> bool:
    in_section = False
    for line in md.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(section_header)
            continue
        if in_section and pattern.match(line):
            return True
    return False


# ---------------------------------------------------------------------------
# Test 2 — JSON contains every subsystem section key
# ---------------------------------------------------------------------------


def test_performance_report_json_contains_all_subsystem_sections(
    tmp_path: Path,
) -> None:
    _write_jsonl(tmp_path / "sandbox_events.jsonl", [])
    report = build_performance_report(tmp_path, _empty_tool_performance())
    sections = report["sandbox"]["sections"]
    expected_section_keys = {
        "summary",
        "per_tool_timing",
        "per_tool_phase_breakdown",
        "background_tool_calls",
        "plugin_activity",
        "overlay_workspace",
        "layer_stack",
        "occ",
        "isolated_workspace",
        "os_resource",
        "daemon_audit_pull",
        "overhead",
        "warnings",
    }
    assert expected_section_keys.issubset(sections.keys()), (
        f"missing: {expected_section_keys - sections.keys()}"
    )


# ---------------------------------------------------------------------------
# Test 3 — per-tool phase breakdown reflects emitted phases
# ---------------------------------------------------------------------------


def test_per_tool_phase_breakdown_matches_emitted_phases(
    tmp_path: Path,
) -> None:
    """Emit calls with a known phase split; assert the §3 fractions
    reflect the same split."""
    rows = [
        _tool_call_finished(
            seq=1,
            tool_name="edit_file",
            total_ms=100.0,
            rollup={
                "queued_ms": 10.0,
                "exec_ms": 80.0,
                "capture_ms": 5.0,
                "release_ms": 5.0,
            },
        ),
        _tool_call_finished(
            seq=2,
            tool_name="edit_file",
            total_ms=100.0,
            rollup={
                "queued_ms": 10.0,
                "exec_ms": 80.0,
                "capture_ms": 5.0,
                "release_ms": 5.0,
            },
        ),
    ]
    _write_jsonl(tmp_path / "sandbox_events.jsonl", rows)
    report = build_performance_report(tmp_path, _empty_tool_performance())
    rows_section = report["sandbox"]["sections"]["per_tool_phase_breakdown"]["rows"]
    assert rows_section, "expected at least one breakdown row"
    edit_row = next(r for r in rows_section if r["tool_name"] == "edit_file")
    fractions = edit_row["phases_fraction"]
    assert fractions["exec"] == pytest.approx(0.80, abs=0.01)
    assert fractions["queued"] == pytest.approx(0.10, abs=0.01)
    assert fractions["capture"] == pytest.approx(0.05, abs=0.01)
    assert fractions["release"] == pytest.approx(0.05, abs=0.01)
    # mount/publish remain 0 — FU#5 (not recorded yet).
    assert fractions["mount"] == 0.0
    assert fractions["publish"] == 0.0


# ---------------------------------------------------------------------------
# Test 4 — per-tool tables split by workspace_mode
# ---------------------------------------------------------------------------


def test_per_tool_tables_split_by_workspace_mode(tmp_path: Path) -> None:
    rows = [
        _tool_call_finished(
            seq=1, tool_name="edit_file", workspace_mode="ephemeral", total_ms=8.0
        ),
        _tool_call_finished(
            seq=2, tool_name="edit_file", workspace_mode="ephemeral", total_ms=10.0
        ),
        _tool_call_finished(
            seq=3, tool_name="edit_file", workspace_mode="isolated", total_ms=22.0
        ),
    ]
    _write_jsonl(tmp_path / "sandbox_events.jsonl", rows)
    report = build_performance_report(tmp_path, _empty_tool_performance())
    table_rows = report["sandbox"]["sections"]["per_tool_timing"]["rows"]
    keys = {(r["tool_name"], r["workspace_mode"]) for r in table_rows}
    assert ("edit_file", "ephemeral") in keys
    assert ("edit_file", "isolated") in keys
    ephemeral = next(
        r for r in table_rows if r["workspace_mode"] == "ephemeral" and r["tool_name"] == "edit_file"
    )
    isolated = next(
        r for r in table_rows if r["workspace_mode"] == "isolated" and r["tool_name"] == "edit_file"
    )
    assert ephemeral["calls"] == 2
    assert isolated["calls"] == 1


# ---------------------------------------------------------------------------
# Test 5 — overhead methodology recorded in JSON
# ---------------------------------------------------------------------------


def test_overhead_gate_methodology_recorded_in_json(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "sandbox_events.jsonl", [])
    report = build_performance_report(
        tmp_path,
        _empty_tool_performance(),
        overhead_metadata={
            "n_calls": 1500,
            "n_paired_runs": 3,
            "warmup_s": 60.0,
            "bootstrap_resamples": 10000,
            "p95_delta_ci_upper": 2.4,
        },
    )
    methodology = report["sandbox"]["sections"]["overhead"]["methodology"]
    for key in (
        "n_calls",
        "n_paired_runs",
        "warmup_s",
        "bootstrap_resamples",
        "p95_delta_ci_upper",
    ):
        assert key in methodology, f"overhead methodology missing {key}"


# ---------------------------------------------------------------------------
# Test 6 — overhead metrics present and below thresholds (gate verdict)
# ---------------------------------------------------------------------------


def test_overhead_gate_metrics_present_and_below_thresholds(
    tmp_path: Path,
) -> None:
    _write_jsonl(tmp_path / "sandbox_events.jsonl", [])
    overhead_metadata = {
        "daemon_ring_memory_retained_bytes": 1_000_000,
        "daemon_ring_memory_max_bytes": 8_388_608,
        "daemon_cpu_pct_p99": 0.4,
        "runner_cpu_pct_p99": 0.1,
        "tool_latency_p95_delta_ms": 1.0,
        "p95_delta_ci_upper": 1.4,
        "daemon_rss_delta_mib": 8.0,
        "sandbox_disk_delta_bytes": 0,
        "artifact_disk_live_bytes": 1024,
        "artifact_disk_rotated_bytes": 0,
        "n_calls": 2000,
        "n_paired_runs": 3,
        "warmup_s": 60.0,
        "bootstrap_resamples": 10000,
    }
    report = build_performance_report(
        tmp_path,
        _empty_tool_performance(),
        overhead_metadata=overhead_metadata,
    )
    overhead = report["sandbox"]["sections"]["overhead"]
    verdict = overhead["gate"]["verdict"]
    assert verdict["latency_p95_delta_pass"] is True
    assert verdict["runner_cpu_pass"] is True
    assert verdict["daemon_cpu_pass"] is True
    assert verdict["sandbox_disk_pass"] is True
    # release_gates evaluator must agree
    gate_eval = evaluate_audit_overhead_gate(overhead_metadata)
    assert gate_eval["passed"] is True


# ---------------------------------------------------------------------------
# Test 7 — isolated workspace gate fails on a synthetic orphan
# ---------------------------------------------------------------------------


def test_isolated_workspace_gate_fails_on_synthetic_orphan(
    tmp_path: Path,
) -> None:
    rows = [
        _isolated_workspace_entered(seq=1, handle_id="ws-a"),
        _isolated_workspace_exited(
            seq=2,
            handle_id="ws-a",
            orphan_holder=1,
            orphan_scratch=2,
        ),
    ]
    _write_jsonl(tmp_path / "sandbox_events.jsonl", rows)
    report = build_performance_report(tmp_path, _empty_tool_performance())
    warnings = report["sandbox"]["sections"]["warnings"]["rows"]
    assert any(
        w["kind"] == "isolated_workspace.gate_failure" for w in warnings
    ), f"missing isolated_workspace.gate_failure warning; got {warnings}"
    # release_gates evaluator must produce passed=False
    verdict = evaluate_isolated_workspace_gate(rows)
    assert verdict["passed"] is False
    assert verdict["orphan_holder_count"] == 1
    assert verdict["orphan_scratch_count"] == 2


# ---------------------------------------------------------------------------
# Test 8 — gate is evaluable from a one-shot snapshot when puller is off
# ---------------------------------------------------------------------------


def test_isolated_workspace_gate_evaluable_via_snapshot_when_puller_off() -> None:
    """The release-gate harness can call ``api.audit.pull`` directly when
    the runtime puller is disabled; the gate evaluator's verdict is the
    same regardless of which side records the events.
    """
    # Simulate the response from a direct audit_pull RPC with puller off.
    direct_pull_response_events = [
        _isolated_workspace_entered(seq=1, handle_id="ws-x"),
        _isolated_workspace_exited(
            seq=2,
            handle_id="ws-x",
            orphan_holder=0,
            orphan_cgroup=0,
            orphan_scratch=0,
            holder_pid_alive=False,
        ),
    ]
    verdict = evaluate_isolated_workspace_gate(direct_pull_response_events)
    assert verdict["passed"] is True
    assert verdict["open_handle_count"] == 0


# ---------------------------------------------------------------------------
# Test 9 — engine refuses dual-disable when isolated_workspace is on
# ---------------------------------------------------------------------------


def test_engine_refuses_dual_disable_when_isolated_workspace_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from task_center_runner.core.engine import (
        _refuse_dual_disable_when_isolated_workspace_enabled,
    )

    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "false")
    monkeypatch.setenv("EOS_AUDIT_STREAM_FALLBACK", "false")
    monkeypatch.setenv("EOS_ISOLATED_WORKSPACE_ENABLED", "true")

    with pytest.raises(RuntimeError, match="refuses to start"):
        _refuse_dual_disable_when_isolated_workspace_enabled()


def test_engine_starts_when_only_one_audit_path_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: pull off but stream on is fine."""
    from task_center_runner.core.engine import (
        _refuse_dual_disable_when_isolated_workspace_enabled,
    )

    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "false")
    monkeypatch.setenv("EOS_AUDIT_STREAM_FALLBACK", "true")
    monkeypatch.setenv("EOS_ISOLATED_WORKSPACE_ENABLED", "true")
    # Must not raise
    _refuse_dual_disable_when_isolated_workspace_enabled()


def test_engine_starts_when_isolated_workspace_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative case: both audit paths off but isolated workspace also
    off — the gate's invariant doesn't apply."""
    from task_center_runner.core.engine import (
        _refuse_dual_disable_when_isolated_workspace_enabled,
    )

    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "false")
    monkeypatch.setenv("EOS_AUDIT_STREAM_FALLBACK", "false")
    monkeypatch.setenv("EOS_ISOLATED_WORKSPACE_ENABLED", "false")
    _refuse_dual_disable_when_isolated_workspace_enabled()


# ---------------------------------------------------------------------------
# Test 10 — report renders without LSP-specific strings (key-side check)
# ---------------------------------------------------------------------------


def test_report_renders_without_lsp_specific_strings(tmp_path: Path) -> None:
    rows = [
        # LSP plugin invocation is fine as a *value*; we assert §5 keys
        # remain generic.
        _plugin_tool_invoked(
            seq=1, plugin_id="lsp-py", plugin_kind="language_server"
        ),
        _plugin_tool_invoked(seq=2, plugin_id="ruff-d", plugin_kind="formatter"),
    ]
    _write_jsonl(tmp_path / "sandbox_events.jsonl", rows)
    report = build_performance_report(tmp_path, _empty_tool_performance())
    md = render_performance_report_markdown(report)

    # MD header row for §5 must not name "language_server" / "pyright" as
    # a column heading; it's a value, not a key. The strict regex makes
    # this explicit.
    column_header_line = next(
        (line for line in md.splitlines() if line.startswith("| plugin_id |")),
        "",
    )
    assert column_header_line, "could not find §5 column header"
    for forbidden in ("pyright", "language_server", "lsp"):
        assert forbidden not in column_header_line.lower(), (
            f"§5 column header must not name vendor: {column_header_line}"
        )

    # JSON: section keys (the keys of `sections.plugin_activity` and its
    # row dicts) must not include vendor names.
    plugin_activity = report["sandbox"]["sections"]["plugin_activity"]
    for row in plugin_activity["rows"]:
        # The KEYS of each row must be generic.
        for key in row:
            assert key in {
                "plugin_id",
                "plugin_kind",
                "invocations",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "peak_resident_bytes",
                "errors",
            }, f"vendor-named key leaked into §5 row: {key}"


# ---------------------------------------------------------------------------
# Test 11 — report reads payload.<section>, not payload.daemon_event
# ---------------------------------------------------------------------------


def test_report_consumer_reads_promoted_payload_section_not_daemon_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enable forensic raw, corrupt ``payload.daemon_event`` deliberately,
    assert §2 (per-tool timing) is unchanged because the builder reads
    ``payload.tool_call``.
    """
    monkeypatch.setenv(FORENSIC_RAW_ENV, "true")
    # Round-trip the forensic raw env so the normalizer reads it; we
    # construct rows directly because the builder reads the JSONL.
    good_row = _tool_call_finished(
        seq=1, tool_name="read_file", workspace_mode="ephemeral", total_ms=15.0
    )
    corrupted_row = dict(good_row)
    corrupted_row["payload"] = dict(good_row["payload"])
    # Corrupt daemon_event field — must NOT affect the report.
    corrupted_row["payload"]["daemon_event"] = {
        "type": "tool_call.finished",
        "payload": {"tool_call": {"tool_name": "CORRUPTED", "total_ms": 99999.0}},
    }
    _write_jsonl(tmp_path / "sandbox_events.jsonl", [corrupted_row])
    report = build_performance_report(tmp_path, _empty_tool_performance())
    table_rows = report["sandbox"]["sections"]["per_tool_timing"]["rows"]
    names = {r["tool_name"] for r in table_rows}
    assert "read_file" in names, "report must read from payload.tool_call"
    assert "CORRUPTED" not in names, "report must NOT read from payload.daemon_event"
    # And §1 reflects the good data.
    summary = report["sandbox"]["sections"]["summary"]
    assert summary["tools_called"] == 1
    assert summary["duration_total_ms"] == pytest.approx(15.0, abs=0.001)


# ---------------------------------------------------------------------------
# Default-on rollout — opt-out env gate honoured
# ---------------------------------------------------------------------------


def test_recorder_skips_auto_start_when_env_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``EOS_DAEMON_AUDIT_PULL_ENABLED=false`` the recorder MUST NOT
    auto-start a puller even if a sandbox_id is bound (per V3 §Default-on
    rollout)."""
    from task_center_runner.audit.recorder import _daemon_audit_pull_enabled

    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "false")
    assert _daemon_audit_pull_enabled() is False
    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "true")
    assert _daemon_audit_pull_enabled() is True


def test_recorder_auto_start_is_default_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default (env unset) must be ON."""
    from task_center_runner.audit.recorder import _daemon_audit_pull_enabled

    monkeypatch.delenv("EOS_DAEMON_AUDIT_PULL_ENABLED", raising=False)
    assert _daemon_audit_pull_enabled() is True


def test_central_config_path_disables_puller_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env is unset, ``RunnerConfig.daemon_audit_pull.enabled`` must
    govern. This pins the central-config plumbing so the spec's "runner
    config" wording isn't paper-only.
    """
    from task_center_runner.audit import recorder as recorder_module

    monkeypatch.delenv("EOS_DAEMON_AUDIT_PULL_ENABLED", raising=False)

    class _StubRunner:
        class daemon_audit_pull:  # noqa: N801 — emulating Pydantic field path
            enabled = False

    class _StubCentral:
        runner = _StubRunner

    def _stub_get_central_config() -> _StubCentral:
        return _StubCentral()

    # Override the lazy import inside the helper.
    import config as config_module

    monkeypatch.setattr(
        config_module, "get_central_config", _stub_get_central_config
    )
    assert recorder_module._daemon_audit_pull_enabled() is False

    # And env explicitly set wins over central config.
    monkeypatch.setenv("EOS_DAEMON_AUDIT_PULL_ENABLED", "true")
    assert recorder_module._daemon_audit_pull_enabled() is True


__all__: list[str] = []
