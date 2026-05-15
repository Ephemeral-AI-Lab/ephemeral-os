"""Detailed live E2E performance report generation.

Builds a per-run report from the detailed tool metrics kept in memory and the
persisted ``sandbox_events.jsonl`` stream. The report intentionally stays
offline: callers can rebuild it from a run directory without touching Daytona
or TaskCenter state.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

REPORT_SCHEMA = "live_e2e.performance_report.v1"
_SLOWEST_LIMIT = 25

_SANDBOX_FAMILY_BY_EVENT: Mapping[str, str] = {
    "sandbox_conflict_detected": "occ",
    "sandbox_occ_changeset_received": "occ",
    "sandbox_occ_changes_committed": "occ",
    "sandbox_overlay_executed": "overlay",
    "sandbox_layer_stack_lease_acquired": "layer_stack",
    "sandbox_layer_stack_layer_created": "layer_stack",
    "sandbox_layer_stack_layers_squashed": "layer_stack",
    "sandbox_write_committed": "sandbox_tool",
    "sandbox_edit_committed": "sandbox_tool",
    "sandbox_shell_committed": "sandbox_tool",
    "sandbox_batch_edit_applied": "sandbox_tool",
}


def build_performance_report(
    run_dir: Path,
    tool_performance: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the detailed report payload for one live E2E run directory."""
    run_path = Path(run_dir)
    sandbox_events = list(_iter_jsonl(run_path / "sandbox_events.jsonl"))
    sandbox_report = _build_sandbox_report(sandbox_events)
    tool_report = dict(tool_performance)
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "run": _read_json(run_path / "run.json"),
        "artifacts": {
            "run_dir": str(run_path),
            "metrics_json": "metrics.json",
            "sandbox_events_jsonl": "sandbox_events.jsonl",
            "performance_report_json": "performance_report.json",
            "performance_report_md": "performance_report.md",
        },
        "totals": _build_totals(tool_report, sandbox_report),
        "tools": tool_report,
        "sandbox": sandbox_report,
        "hotspots": _build_hotspots(tool_report, sandbox_report),
    }
    report["observations"] = _build_observations(report)
    return report


def write_performance_reports(
    run_dir: Path,
    tool_performance: Mapping[str, Any],
) -> dict[str, Any]:
    """Write ``performance_report.json`` and ``performance_report.md``."""
    report = build_performance_report(run_dir, tool_performance)
    _atomic_write_json(Path(run_dir) / "performance_report.json", report)
    _atomic_write_text(
        Path(run_dir) / "performance_report.md",
        render_performance_report_markdown(report),
    )
    return report


def render_performance_report_markdown(report: Mapping[str, Any]) -> str:
    """Render a concise human-readable companion report."""
    run = _as_mapping(report.get("run"))
    totals = _as_mapping(report.get("totals"))
    tools = _as_mapping(_as_mapping(report.get("tools")).get("per_tool"))
    sandbox = _as_mapping(report.get("sandbox"))
    families = _as_mapping(sandbox.get("families"))
    timing_keys = _as_mapping(sandbox.get("timing_keys"))
    hotspots = _as_mapping(report.get("hotspots"))

    lines = [
        "# Live E2E Performance Report",
        "",
        f"- Schema: `{report.get('schema', '')}`",
        f"- Scenario: `{run.get('scenario_name', '')}`",
        f"- TaskCenter run: `{run.get('task_center_run_id', '')}`",
        f"- Sandbox: `{run.get('sandbox_id', '')}`",
        f"- Status: `{run.get('status', '')}`",
        "",
        "## Totals",
        "",
        f"- Tool calls: {totals.get('tool_calls_total', 0)}",
        f"- Tool errors: {totals.get('tool_errors_total', 0)}",
        f"- Tool latency total: {_fmt_ms(totals.get('tool_latency_total_ms'))}",
        f"- Sandbox events: {totals.get('sandbox_event_count', 0)}",
        f"- Sandbox timed duration total: {_fmt_s(totals.get('sandbox_duration_total_s'))}",
        "",
        "## Tool Latency By Total Time",
        "",
        "| Tool | Calls | Errors | Total | Mean | P50 | P95 | P99 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in _sorted_tool_rows(tools):
        item_map = _as_mapping(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(name),
                    str(item_map.get("count", 0)),
                    str(item_map.get("errors", 0)),
                    _fmt_ms(item_map.get("total_ms")),
                    _fmt_ms(item_map.get("mean_ms")),
                    _fmt_ms(item_map.get("p50_ms")),
                    _fmt_ms(item_map.get("p95_ms")),
                    _fmt_ms(item_map.get("p99_ms")),
                    _fmt_ms(item_map.get("max_ms")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Sandbox Subsystems",
            "",
            "| Family | Events | Timed Total | Mean Event | P50 Event | P95 Event | Max Event |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for family, item in _sorted_family_rows(families):
        item_map = _as_mapping(item)
        duration = _as_mapping(item_map.get("duration_s"))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(family),
                    str(item_map.get("event_count", 0)),
                    _fmt_s(duration.get("total")),
                    _fmt_s(duration.get("mean")),
                    _fmt_s(duration.get("p50")),
                    _fmt_s(duration.get("p95")),
                    _fmt_s(duration.get("max")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Sandbox Timing Keys By Total Time",
            "",
            "| Timing Key | Count | Total | Mean | P50 | P95 | Max |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, item in _sorted_timing_rows(timing_keys)[:30]:
        item_map = _as_mapping(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(key),
                    str(item_map.get("count", 0)),
                    _fmt_s(item_map.get("total")),
                    _fmt_s(item_map.get("mean")),
                    _fmt_s(item_map.get("p50")),
                    _fmt_s(item_map.get("p95")),
                    _fmt_s(item_map.get("max")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Slowest Tool Calls",
            "",
            "| Tool | Duration | Error | Agent Run | Tool Id | Status |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for sample in _as_sequence(hotspots.get("slowest_tool_calls"))[:15]:
        sample_map = _as_mapping(sample)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(sample_map.get("tool_name", "")),
                    _fmt_ms(sample_map.get("duration_ms")),
                    str(sample_map.get("is_error", False)),
                    _short(sample_map.get("agent_run_id")),
                    _short(sample_map.get("tool_id")),
                    str(sample_map.get("status") or ""),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Slowest Sandbox Events",
            "",
            "| Family | Event | Tool | Duration | Status | Changed Paths |",
            "| --- | --- | --- | ---: | --- | ---: |",
        ]
    )
    for sample in _as_sequence(hotspots.get("slowest_sandbox_events"))[:15]:
        sample_map = _as_mapping(sample)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(sample_map.get("family", "")),
                    str(sample_map.get("event_type", "")),
                    str(sample_map.get("tool_name") or ""),
                    _fmt_s(sample_map.get("duration_s_total")),
                    str(sample_map.get("status") or ""),
                    str(sample_map.get("changed_path_count", 0)),
                ]
            )
            + " |"
        )

    observations = [str(item) for item in _as_sequence(report.get("observations"))]
    if observations:
        lines.extend(["", "## Observations", ""])
        lines.extend(f"- {item}" for item in observations)

    lines.append("")
    return "\n".join(lines)


def _build_totals(
    tool_report: Mapping[str, Any],
    sandbox_report: Mapping[str, Any],
) -> dict[str, Any]:
    per_tool = _as_mapping(tool_report.get("per_tool"))
    total_ms = 0.0
    for item in per_tool.values():
        total_ms += float(_as_mapping(item).get("total_ms") or 0.0)
    return {
        "tool_calls_total": int(tool_report.get("tool_calls_total") or 0),
        "tool_errors_total": int(tool_report.get("tool_errors_total") or 0),
        "tool_latency_total_ms": total_ms,
        "sandbox_event_count": int(sandbox_report.get("event_count") or 0),
        "sandbox_duration_total_s": float(
            sandbox_report.get("duration_total_s") or 0.0
        ),
        "incomplete_tool_calls": len(_as_sequence(tool_report.get("incomplete_calls"))),
    }


def _build_sandbox_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    family_events: dict[str, list[dict[str, Any]]] = {}
    timing_values: dict[str, list[float]] = {}
    non_duration_values: dict[str, list[float]] = {}
    detailed_events: list[dict[str, Any]] = []
    event_type_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    conflict_events: list[dict[str, Any]] = []

    for row in rows:
        event = _normalize_sandbox_event(row)
        family_events.setdefault(event["family"], []).append(event)
        detailed_events.append(event)
        event_type_counts.update([event["event_type"]])
        if event["tool_name"]:
            tool_counts.update([event["tool_name"]])
        if event["status"]:
            status_counts.update([event["status"]])
        if event["conflict_reason"] or event["event_type"] == "sandbox_conflict_detected":
            conflict_events.append(event)
        for key, value in _as_mapping(event.get("timings")).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if _looks_like_duration(key):
                timing_values.setdefault(str(key), []).append(number)
            else:
                non_duration_values.setdefault(str(key), []).append(number)

    families = {
        family: _build_family_report(events)
        for family, events in sorted(family_events.items())
    }
    duration_total = sum(float(item["duration_s"]["total"]) for item in families.values())
    return {
        "event_count": len(rows),
        "duration_total_s": duration_total,
        "families": families,
        "timing_keys": {
            key: _stats(values) for key, values in sorted(timing_values.items())
        },
        "non_duration_observations": {
            key: _stats(values) for key, values in sorted(non_duration_values.items())
        },
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "tool_counts": dict(sorted(tool_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "conflict_events": conflict_events,
        "slowest_events": _slowest_sandbox_events(detailed_events),
        "events": detailed_events,
    }


def _normalize_sandbox_event(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = _as_mapping(row.get("payload"))
    node = _as_mapping(row.get("node"))
    timings = _float_mapping(payload.get("timings"))
    event_type = str(row.get("event_type") or "sandbox_unknown")
    duration_total = sum(
        value for key, value in timings.items() if _looks_like_duration(key)
    )
    changed_paths = _string_list(payload.get("changed_paths"))
    return {
        "ts": row.get("ts"),
        "event_type": event_type,
        "family": _SANDBOX_FAMILY_BY_EVENT.get(event_type, "sandbox_other"),
        "tool_name": payload.get("tool_name") or node.get("tool_name"),
        "tool_id": payload.get("tool_id"),
        "agent_name": node.get("agent_name"),
        "agent_run_id": node.get("agent_run_id"),
        "task_center_run_id": node.get("task_center_run_id"),
        "status": payload.get("status"),
        "conflict_reason": payload.get("conflict_reason"),
        "changed_paths": changed_paths,
        "changed_path_count": len(changed_paths),
        "timings": timings,
        "duration_s_total": duration_total,
        "correlation_id": row.get("correlation_id"),
    }


def _build_family_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    timing_values: dict[str, list[float]] = {}
    duration_values: list[float] = []
    changed_paths_total = 0
    conflict_count = 0

    for event in events:
        event_types.update([str(event.get("event_type") or "")])
        tool_name = event.get("tool_name")
        if tool_name:
            tools.update([str(tool_name)])
        status = event.get("status")
        if status:
            statuses.update([str(status)])
        if event.get("conflict_reason") or event.get("event_type") == (
            "sandbox_conflict_detected"
        ):
            conflict_count += 1
        changed_paths_total += int(event.get("changed_path_count") or 0)
        duration_values.append(float(event.get("duration_s_total") or 0.0))
        for key, value in _as_mapping(event.get("timings")).items():
            try:
                timing_values.setdefault(str(key), []).append(float(value))
            except (TypeError, ValueError):
                continue

    return {
        "event_count": len(events),
        "duration_s": _stats(duration_values),
        "event_type_counts": dict(sorted(event_types.items())),
        "tool_counts": dict(sorted(tools.items())),
        "status_counts": dict(sorted(statuses.items())),
        "changed_paths_total": changed_paths_total,
        "conflict_count": conflict_count,
        "timing_keys": {
            key: _stats(values) for key, values in sorted(timing_values.items())
        },
        "slowest_events": _slowest_sandbox_events(events),
    }


def _build_hotspots(
    tool_report: Mapping[str, Any],
    sandbox_report: Mapping[str, Any],
) -> dict[str, Any]:
    per_tool = _as_mapping(tool_report.get("per_tool"))
    slowest_timing_keys = sorted(
        _as_mapping(sandbox_report.get("timing_keys")).items(),
        key=lambda item: float(_as_mapping(item[1]).get("total") or 0.0),
        reverse=True,
    )[:_SLOWEST_LIMIT]
    tool_rank = [
        {
            "tool_name": name,
            "count": _as_mapping(item).get("count", 0),
            "errors": _as_mapping(item).get("errors", 0),
            "total_ms": _as_mapping(item).get("total_ms", 0.0),
            "p95_ms": _as_mapping(item).get("p95_ms", 0.0),
        }
        for name, item in _sorted_tool_rows(per_tool)
    ]
    family_rank = [
        {
            "family": family,
            "event_count": _as_mapping(item).get("event_count", 0),
            "duration_s_total": _as_mapping(
                _as_mapping(item).get("duration_s")
            ).get("total", 0.0),
            "p95_s": _as_mapping(_as_mapping(item).get("duration_s")).get(
                "p95", 0.0
            ),
        }
        for family, item in _sorted_family_rows(
            _as_mapping(sandbox_report.get("families"))
        )
    ]
    return {
        "tool_rank_by_total_ms": tool_rank,
        "sandbox_family_rank_by_total_s": family_rank,
        "slowest_tool_calls": _as_sequence(tool_report.get("slowest_calls")),
        "slowest_sandbox_events": _as_sequence(sandbox_report.get("slowest_events")),
        "slowest_sandbox_timing_keys": [
            {"timing_key": key, **_as_mapping(value)}
            for key, value in slowest_timing_keys
        ],
    }


def _build_observations(report: Mapping[str, Any]) -> list[str]:
    hotspots = _as_mapping(report.get("hotspots"))
    observations: list[str] = []
    tool_rank = _as_sequence(hotspots.get("tool_rank_by_total_ms"))
    if tool_rank:
        first = _as_mapping(tool_rank[0])
        observations.append(
            "Highest cumulative tool latency: "
            f"{first.get('tool_name')} at {_fmt_ms(first.get('total_ms'))} "
            f"across {first.get('count', 0)} call(s)."
        )
    family_rank = _as_sequence(hotspots.get("sandbox_family_rank_by_total_s"))
    if family_rank:
        first = _as_mapping(family_rank[0])
        observations.append(
            "Highest cumulative sandbox subsystem timing: "
            f"{first.get('family')} at {_fmt_s(first.get('duration_s_total'))} "
            f"across {first.get('event_count', 0)} event(s)."
        )
    timing_rank = _as_sequence(hotspots.get("slowest_sandbox_timing_keys"))
    if timing_rank:
        first = _as_mapping(timing_rank[0])
        observations.append(
            "Largest sandbox timing key by total: "
            f"{first.get('timing_key')} at {_fmt_s(first.get('total'))}."
        )
    totals = _as_mapping(report.get("totals"))
    incomplete = int(totals.get("incomplete_tool_calls") or 0)
    if incomplete:
        observations.append(
            f"{incomplete} tool call(s) had a start event without a terminal event."
        )
    return observations


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _stats(values: Iterable[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "total": 0.0,
            "min": 0.0,
            "mean": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    total = float(sum(ordered))
    return {
        "count": len(ordered),
        "total": total,
        "min": ordered[0],
        "mean": total / float(len(ordered)),
        "p50": float(median(ordered)),
        "p75": _percentile(ordered, 75.0),
        "p90": _percentile(ordered, 90.0),
        "p95": _percentile(ordered, 95.0),
        "p99": _percentile(ordered, 99.0),
        "max": ordered[-1],
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    rank = max(1, int(round(pct / 100.0 * len(values))))
    return float(values[min(rank, len(values)) - 1])


def _slowest_sandbox_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda item: float(item.get("duration_s_total") or -1.0),
        reverse=True,
    )[:_SLOWEST_LIMIT]


def _sorted_tool_rows(
    per_tool: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    return sorted(
        per_tool.items(),
        key=lambda item: float(_as_mapping(item[1]).get("total_ms") or 0.0),
        reverse=True,
    )


def _sorted_family_rows(
    families: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    return sorted(
        families.items(),
        key=lambda item: float(
            _as_mapping(_as_mapping(item[1]).get("duration_s")).get("total")
            or 0.0
        ),
        reverse=True,
    )


def _sorted_timing_rows(
    timing_keys: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    return sorted(
        timing_keys.items(),
        key=lambda item: float(_as_mapping(item[1]).get("total") or 0.0),
        reverse=True,
    )


def _looks_like_duration(key: object) -> bool:
    text = str(key)
    return text.endswith("_s") or text.endswith(".total_s") or text.endswith(".s")


def _float_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            result[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_sequence(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _fmt_ms(value: object) -> str:
    try:
        return f"{float(value):.1f} ms"
    except (TypeError, ValueError):
        return "0.0 ms"


def _fmt_s(value: object) -> str:
    try:
        return f"{float(value):.4f} s"
    except (TypeError, ValueError):
        return "0.0000 s"


def _short(value: object, max_len: int = 24) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(data, default=str, ensure_ascii=False, indent=2)
    tmp_path.write_text(encoded + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data, encoding="utf-8")
    os.replace(tmp_path, path)


__all__ = [
    "REPORT_SCHEMA",
    "build_performance_report",
    "render_performance_report_markdown",
    "write_performance_reports",
]
