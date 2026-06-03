#!/usr/bin/env python3
"""Summarize EphemeralOS sandbox live-run performance artifacts."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


TIMING_KEYS = {
    "command_exec.mount_workspace_s",
    "command_exec.run_command_s",
    "command_exec.capture_upperdir_s",
    "command_exec.total_s",
    "api.shell.total_s",
    "layer_stack.materialize_s",
    "layer_stack.prepare_workspace_snapshot.total_s",
    "layer_stack.publish.total_s",
    "layer_stack.transaction.lock_wait_s",
    "layer_stack.transaction.lock_held_s",
    "occ.apply.total_s",
    "occ.serial.queue_wait_s",
    "occ.apply.commit_queue_wait_s",
    "occ.apply.commit_worker_s",
    "api.read.lease_acquire_s",
    "api.write.lease_acquire_s",
    "api.edit.lease_acquire_s",
    "api.write.total_s",
    "api.edit.total_s",
    "lsp.total_s",
    "lsp.session.start_count_delta",
    "lsp.session.refresh_count_delta",
    "lsp.session.remount_count_delta",
    "lsp.session.private_overlay_namespace",
    "lsp.session.has_overlay_handle",
    "resource.audit.collect_s",
}

RESOURCE_KEYS = {
    # Per-operation snapshots: these reflect THIS run's workload only.
    "resource.command_exec.changed_path_count",
    "resource.command_exec.workspace_tree_exists",
    "resource.command_exec.workspace_tree_bytes",
    "resource.command_exec.workspace_tree_file_count",
    "resource.command_exec.workspace_tree_dir_count",
    "resource.command_exec.workspace_tree_entry_count",
    "resource.command_exec.workspace_tree_truncated",
    "resource.command_exec.upperdir_tree_bytes",
    "resource.command_exec.upperdir_tree_file_count",
    "resource.command_exec.upperdir_tree_dir_count",
    "resource.command_exec.upperdir_tree_entry_count",
    "resource.command_exec.upperdir_tree_truncated",
    "resource.command_exec.run_dir_tree_bytes",
    "resource.command_exec.run_dir_tree_file_count",
    "resource.command_exec.run_dir_tree_dir_count",
    "resource.command_exec.run_dir_tree_entry_count",
    "resource.command_exec.run_dir_tree_truncated",
    "resource.command_exec.scratch_filesystem_used_bytes",
    "resource.command_exec.writable_filesystem_used_bytes",
    "resource.command_exec.writable_filesystem_free_bytes",
    "resource.layer_stack.storage_filesystem_used_bytes",
    "resource.layer_stack.storage_filesystem_free_bytes",
    "resource.layer_stack.manifest_depth",
    "resource.layer_stack.manifest_path_count",
    "resource.process.rss_bytes",
    "resource.process.max_rss_bytes",
}

# cgroup counters are MONOTONIC cumulative values measured since the sandbox
# was created. In `sandbox_reuse_mode: reuse` they include every prior test
# session's writes too. Treat the absolute value as "sandbox lifetime" and
# the (last - first) delta as the closest available proxy for "this run".
CGROUP_LIFETIME_KEYS = {
    "resource.cgroup.memory_current_bytes",
    "resource.cgroup.memory_peak_bytes",
    "resource.cgroup.memory_max_bytes",
    "resource.cgroup.cpu_usage_usec",
    "resource.cgroup.cpu_user_usec",
    "resource.cgroup.cpu_system_usec",
    "resource.cgroup.cpu_throttled_usec",
    "resource.cgroup.cpu_nr_periods",
    "resource.cgroup.cpu_nr_throttled",
    "resource.cgroup.cpu_nr_bursts",
    "resource.cgroup.cpu_burst_usec",
    "resource.cgroup.io_rbytes",
    "resource.cgroup.io_wbytes",
    "resource.cgroup.io_rios",
    "resource.cgroup.io_wios",
    "resource.cgroup.io_dbytes",
    "resource.cgroup.io_dios",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    summaries = [summarize_run(path) for path in args.run_dirs]
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


def summarize_run(run_dir: Path) -> dict[str, Any]:
    report = _read_json(run_dir / "performance_report.json")
    run = report.get("run") or _read_json(run_dir / "run.json")
    events = list(_iter_sandbox_events(run_dir, report))
    timing_values: dict[str, list[float]] = defaultdict(list)
    resource_values: dict[str, list[float]] = defaultdict(list)
    cgroup_values: dict[str, list[float]] = defaultdict(list)
    tool_sample_timing_values: dict[str, list[float]] = defaultdict(list)
    tool_sample_resource_values: dict[str, list[float]] = defaultdict(list)
    tool_sample_cgroup_values: dict[str, list[float]] = defaultdict(list)
    for event in events:
        timings = _event_timings(event)
        _collect_timing_maps(timings, timing_values, resource_values, cgroup_values)

    for sample in _iter_tool_samples(report):
        _collect_timing_maps(
            _tool_sample_timings(sample),
            tool_sample_timing_values,
            tool_sample_resource_values,
            tool_sample_cgroup_values,
        )

    tools = _tool_summary(report)
    summary = {
        "run_dir": str(run_dir),
        "run": run,
        "totals": report.get("totals", {}),
        "max_tool_concurrency": _max_tool_concurrency(report),
        "tools": tools,
        "event_log_inventory": _event_log_inventory(run_dir),
        "daemon_ring": _daemon_ring_summary(events, report),
        "v3_sections": _v3_sections_summary(report),
        "timings_s": {key: _stats(values) for key, values in sorted(timing_values.items())},
        "resource_max": {
            key: max(values) for key, values in sorted(resource_values.items()) if values
        },
        "cgroup_lifetime": _cgroup_lifetime(cgroup_values, tool_sample_cgroup_values),
        "cgroup_run_delta": _cgroup_run_delta(cgroup_values, tool_sample_cgroup_values),
        "tool_sample_timings_s": {
            key: _stats(values)
            for key, values in sorted(tool_sample_timing_values.items())
        },
        "tool_sample_resource_max": {
            key: max(values)
            for key, values in sorted(tool_sample_resource_values.items())
            if values
        },
    }
    flags = _flags(summary)
    if flags:
        summary["flags"] = flags
    return summary


def _event_log_inventory(run_dir: Path) -> dict[str, Any]:
    base = run_dir / "sandbox_events.jsonl"
    live_bytes = _file_size(base)
    rotated_files = sorted(run_dir.glob("sandbox_events.jsonl.*.gz"))
    rotated = [{"name": path.name, "bytes": _file_size(path)} for path in rotated_files]
    rotated_bytes = sum(int(item["bytes"]) for item in rotated)
    live_cap = 64 * 1024 * 1024
    retention_files = 8
    rotated_cap = retention_files * 8 * 1024 * 1024
    return {
        "live_bytes": live_bytes,
        "rotated_bytes": rotated_bytes,
        "rotated_file_count": len(rotated),
        "rotated_files": rotated,
        "total_bytes": live_bytes + rotated_bytes,
        "artifact_bound_pass": (
            live_bytes <= live_cap
            and len(rotated) <= retention_files
            and rotated_bytes <= rotated_cap
        ),
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _daemon_ring_summary(
    events: list[dict[str, Any]],
    report: dict[str, Any],
) -> dict[str, Any]:
    pressure_payloads = []
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        if event_type != "daemon.audit_buffer_pressure":
            continue
        payload = event.get("payload")
        daemon = payload.get("daemon") if isinstance(payload, dict) else None
        if isinstance(daemon, dict):
            pressure_payloads.append(daemon)
    daemon_audit_pull = (
        ((report.get("sandbox") or {}).get("sections") or {}).get("daemon_audit_pull")
        or {}
    )
    return {
        "max_buffer_pressure": daemon_audit_pull.get("max_buffer_pressure", 0.0),
        "pressure_event_count": len(pressure_payloads),
        "max_pressure_event_pressure": _max_numeric(pressure_payloads, "pressure"),
        "max_pressure_event_retained_bytes": _max_numeric(
            pressure_payloads, "retained_bytes"
        ),
        "max_pressure_event_retained_events": _max_numeric(
            pressure_payloads, "retained_events"
        ),
    }


def _max_numeric(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return max(values) if values else 0.0


def _v3_sections_summary(report: dict[str, Any]) -> dict[str, Any]:
    sections = ((report.get("sandbox") or {}).get("sections") or {})
    if not isinstance(sections, dict):
        return {"present": []}
    overhead = sections.get("overhead") if isinstance(sections.get("overhead"), dict) else {}
    gate = overhead.get("gate") if isinstance(overhead.get("gate"), dict) else {}
    warnings = sections.get("warnings") if isinstance(sections.get("warnings"), dict) else {}
    return {
        "present": sorted(sections.keys()),
        "summary": sections.get("summary") or {},
        "daemon_audit_pull": sections.get("daemon_audit_pull") or {},
        "overhead_verdict": gate.get("verdict") or {},
        "artifact_inventory": overhead.get("artifact_inventory") or {},
        "warnings": warnings.get("rows") or [],
        "os_resource": sections.get("os_resource") or {},
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_sandbox_events(run_dir: Path, report: dict[str, Any]):
    events_path = run_dir / "sandbox_events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)
        return
    sandbox = report.get("sandbox")
    if isinstance(sandbox, dict):
        for event in sandbox.get("events") or ():
            if isinstance(event, dict):
                yield event


def _event_timings(event: dict[str, Any]) -> dict[str, Any]:
    timings = event.get("timings")
    if isinstance(timings, dict):
        return timings
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("timings"), dict):
        return payload["timings"]
    return {}


def _collect_timing_maps(
    timings: dict[str, Any],
    timing_values: dict[str, list[float]],
    resource_values: dict[str, list[float]],
    cgroup_values: dict[str, list[float]] | None = None,
) -> None:
    for key, value in timings.items():
        if not isinstance(value, (int, float)):
            continue
        if key in TIMING_KEYS or key.startswith("lsp."):
            timing_values[key].append(float(value))
        if key in RESOURCE_KEYS:
            resource_values[key].append(float(value))
        if cgroup_values is not None and key in CGROUP_LIFETIME_KEYS:
            cgroup_values[key].append(float(value))


def _cgroup_lifetime(
    event_values: dict[str, list[float]],
    sample_values: dict[str, list[float]],
) -> dict[str, float]:
    """Final cumulative cgroup counters at end-of-run.

    These are SANDBOX-LIFETIME totals (since cgroup creation), not this run.
    """
    out: dict[str, float] = {}
    for key in sorted(CGROUP_LIFETIME_KEYS):
        values = event_values.get(key) or sample_values.get(key) or []
        if values:
            out[key] = max(values)
    return out


def _cgroup_run_delta(
    event_values: dict[str, list[float]],
    sample_values: dict[str, list[float]],
) -> dict[str, float]:
    """Approximate per-run cgroup delta: last_sample - first_sample.

    Only the monotonic counters (io_*bytes, cpu_usage_usec) are meaningful as
    deltas. memory_* fields are gauges (current/peak); their delta is shown
    for completeness but the lifetime peak is the operationally useful value.
    """
    out: dict[str, float] = {}
    for key in sorted(CGROUP_LIFETIME_KEYS):
        values = event_values.get(key) or sample_values.get(key) or []
        if len(values) >= 2:
            out[key] = max(0.0, values[-1] - values[0])
        elif values:
            out[key] = 0.0
    return out


def _tool_summary(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    per_tool = ((report.get("tools") or {}).get("per_tool") or {})
    out: dict[str, dict[str, Any]] = {}
    for name, payload in sorted(per_tool.items()):
        if not isinstance(payload, dict):
            continue
        out[name] = {
            "count": payload.get("count", 0),
            "errors": payload.get("errors", 0),
            "mean_ms": payload.get("mean_ms"),
            "p95_ms": payload.get("p95_ms"),
            "max_ms": payload.get("max_ms"),
        }
    return out


def _iter_tool_samples(report: dict[str, Any]):
    per_tool = ((report.get("tools") or {}).get("per_tool") or {})
    for payload in per_tool.values():
        if not isinstance(payload, dict):
            continue
        for sample in payload.get("samples") or ():
            if isinstance(sample, dict):
                yield sample


def _tool_sample_timings(sample: dict[str, Any]) -> dict[str, Any]:
    timings = sample.get("timings_s")
    if isinstance(timings, dict):
        return timings
    return {}


def _max_tool_concurrency(report: dict[str, Any]) -> int:
    points: list[tuple[datetime, int]] = []
    per_tool = ((report.get("tools") or {}).get("per_tool") or {})
    for payload in per_tool.values():
        if not isinstance(payload, dict):
            continue
        for sample in payload.get("samples") or ():
            if not isinstance(sample, dict):
                continue
            started = _parse_ts(sample.get("started_ts"))
            completed = _parse_ts(sample.get("completed_ts"))
            if started is None or completed is None:
                continue
            points.append((started, 1))
            points.append((completed, -1))
    active = 0
    peak = 0
    for _ts, delta in sorted(points, key=lambda item: (item[0], -item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stats(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean": mean(ordered),
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
        "positive_count": sum(1 for value in ordered if value > 0),
    }


def _percentile(ordered: list[float], percentile: int) -> float:
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1))))
    return ordered[index]


def _flags(summary: dict[str, Any]) -> list[str]:
    resources = summary.get("resource_max") or {}
    timings = summary.get("timings_s") or {}
    tool_resources = summary.get("tool_sample_resource_max") or {}
    tool_timings = summary.get("tool_sample_timings_s") or {}
    flags: list[str] = []
    if (
        max(
            resources.get("resource.command_exec.workspace_tree_bytes", 0),
            tool_resources.get("resource.command_exec.workspace_tree_bytes", 0),
        )
        > 0
    ):
        flags.append("workspace_tree_bytes_nonzero")
    if (
        max(
            timings.get("layer_stack.materialize_s", {}).get("max", 0),
            tool_timings.get("layer_stack.materialize_s", {}).get("max", 0),
        )
        > 0
    ):
        flags.append("materialized_snapshot_used")
    lsp_positive_count = max(
        timings.get("lsp.session.start_count_delta", {}).get("positive_count", 0),
        tool_timings.get("lsp.session.start_count_delta", {}).get("positive_count", 0),
    )
    if lsp_positive_count > 1:
        flags.append("repeated_lsp_restart")
    totals = summary.get("totals") or {}
    if totals.get("incomplete_tool_calls", 0):
        flags.append("incomplete_tool_calls")
    event_log_inventory = summary.get("event_log_inventory") or {}
    if event_log_inventory.get("artifact_bound_pass") is False:
        flags.append("event_log_artifact_bound_failed")
    for key in (
        "resource.command_exec.run_dir_tree_truncated",
        "resource.command_exec.upperdir_tree_truncated",
        "resource.command_exec.workspace_tree_truncated",
    ):
        if max(resources.get(key, 0), tool_resources.get(key, 0)) > 0:
            flags.append(f"{key}_nonzero")
    v3 = summary.get("v3_sections") or {}
    expected_sections = {
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
    present_sections = set(v3.get("present") or ())
    if present_sections and not expected_sections <= present_sections:
        flags.append("v3_sections_incomplete")
    if not present_sections and summary.get("run"):
        flags.append("v3_sections_missing")
    daemon_audit_pull = v3.get("daemon_audit_pull") or {}
    if daemon_audit_pull.get("dropped_event_count", 0):
        flags.append("audit_dropped_events")
    if daemon_audit_pull.get("lost_before_seq", 0):
        flags.append("audit_lost_events")
    if daemon_audit_pull.get("puller_attached") is False:
        flags.append("audit_puller_not_attached")
    daemon_ring = summary.get("daemon_ring") or {}
    if float(daemon_ring.get("max_buffer_pressure") or 0.0) >= 0.8:
        flags.append("daemon_ring_pressure_high")
    if float(daemon_ring.get("max_pressure_event_retained_bytes") or 0.0) > 0:
        flags.append("daemon_ring_pressure_events_present")
    overhead_verdict = v3.get("overhead_verdict") or {}
    for key in (
        "overhead_pass",
        "isolated_workspace_pass",
        "drop_free_pull_pass",
        "artifact_bound_pass",
    ):
        if key in overhead_verdict and overhead_verdict.get(key) is not True:
            flags.append(f"{key}_false")
    if v3.get("warnings"):
        flags.append("v3_warnings_present")
    return flags


if __name__ == "__main__":
    raise SystemExit(main())
