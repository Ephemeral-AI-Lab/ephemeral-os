"""Performance metrics aggregator for the complex_project_build scenario.

Produces the v1 schema described in plan §9. Inputs are the in-memory tool
call metadata captured by the probe (each entry contains
``tool_name``, ``is_error``, ``metadata.timings``); outputs the
``complex_project_build.perf.v1`` JSON payload that lands at
``/ephemeral-os/.metrics/perf.json`` and is parsed by
``backend/scripts/analyze_complex_build_perf.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


PERF_SCHEMA = "complex_project_build.perf.v1"


def aggregate_perf_metrics(
    *,
    run_id: str,
    scenario: str,
    wall_seconds_total: float,
    tool_call_metadata: Sequence[dict[str, Any]],
    phases: Sequence[dict[str, Any]],
    write_count: int,
    edit_count: int,
    read_count: int,
    shell_count: int,
    lsp_counts: dict[str, int],
    api_read_count: int,
    api_edit_count: int,
    api_shell_count: int,
    intentional_conflicts: int,
) -> dict[str, Any]:
    """Aggregate the captured tool-call metadata into the v1 schema."""

    by_tool = _aggregate_by_tool(tool_call_metadata)
    layer_stack = _aggregate_layer_stack(tool_call_metadata)
    overlay = _aggregate_overlay(tool_call_metadata, shell_count=shell_count)
    occ = _aggregate_occ(tool_call_metadata, intentional_conflicts=intentional_conflicts)

    # `total_calls` mirrors the probe/toolkit calls that flow through the mock
    # scenario loop. Direct sandbox.api round-trips are tracked separately as
    # `api_calls.*` and excluded here so plan §7.20 can compare the two counts
    # directly.
    total_calls = (
        write_count
        + edit_count
        + read_count
        + shell_count
        + sum(lsp_counts.values())
    )
    api_calls_total = api_read_count + api_edit_count + api_shell_count

    edit_to_write = (
        float(edit_count) / float(write_count) if write_count else float(edit_count)
    )

    payload: dict[str, Any] = {
        "schema": PERF_SCHEMA,
        "run_id": run_id,
        "scenario": scenario,
        "wall_seconds_total": float(wall_seconds_total),
        "tool_use": {
            "total_calls": total_calls,
            "by_tool": by_tool,
            "edit_to_write_ratio": edit_to_write,
            "errors_total": sum(
                int(entry.get("is_error", False)) for entry in tool_call_metadata
            ),
            "expected_errors_total": int(intentional_conflicts),
            "api_calls_total": api_calls_total,
            "api_read_count": api_read_count,
            "api_edit_count": api_edit_count,
            "api_shell_count": api_shell_count,
        },
        "layer_stack": layer_stack,
        "overlay": overlay,
        "occ": occ,
        "phases": list(phases),
    }
    return payload


# ---------------------------------------------------------------------------
# Per-tool wall-time aggregation
# ---------------------------------------------------------------------------


def _aggregate_by_tool(
    tool_call_metadata: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in tool_call_metadata:
        grouped.setdefault(str(entry.get("tool_name") or "unknown"), []).append(entry)

    aggregated: dict[str, dict[str, Any]] = {}
    for tool_name, entries in grouped.items():
        wall_times = [_extract_wall_seconds(e) for e in entries]
        aggregated[tool_name] = {
            "count": len(entries),
            "errors": sum(1 for e in entries if e.get("is_error")),
            "wall_seconds_total": sum(wall_times),
            "wall_seconds_p50": _percentile(wall_times, 0.5),
            "wall_seconds_p95": _percentile(wall_times, 0.95),
            "wall_seconds_max": max(wall_times) if wall_times else 0.0,
        }
    return aggregated


def _extract_wall_seconds(entry: dict[str, Any]) -> float:
    timings = _timings(entry)
    for key in (
        "wall_seconds",
        "tool.wall_seconds",
        "occ.commit.total_s",
        "command_exec.total_s",
        "lsp.total_s",
    ):
        value = timings.get(key)
        if isinstance(value, (int, float)) and value:
            return float(value)
    return 0.0


# ---------------------------------------------------------------------------
# layer_stack timing aggregation
# ---------------------------------------------------------------------------


def _aggregate_layer_stack(
    tool_call_metadata: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    squash_totals: list[float] = []
    depth_observations: list[float] = []
    for entry in tool_call_metadata:
        timings = _timings(entry)
        squash_total = timings.get("layer_stack.auto_squash.total_s")
        if isinstance(squash_total, (int, float)) and squash_total > 0:
            squash_totals.append(float(squash_total))
        depth_before = timings.get("layer_stack.auto_squash.depth_before")
        if isinstance(depth_before, (int, float)) and depth_before > 0:
            depth_observations.append(float(depth_before))
    return {
        "squash_count": len(squash_totals),
        "squash_total_s": sum(squash_totals),
        "squash_p50_s": _percentile(squash_totals, 0.5),
        "squash_p95_s": _percentile(squash_totals, 0.95),
        "squash_max_s": max(squash_totals) if squash_totals else 0.0,
        "max_depth_before": max(depth_observations) if depth_observations else 0.0,
        "depth_observation_count": len(depth_observations),
    }


# ---------------------------------------------------------------------------
# overlay aggregation
# ---------------------------------------------------------------------------


def _aggregate_overlay(
    tool_call_metadata: Sequence[dict[str, Any]],
    *,
    shell_count: int,
) -> dict[str, Any]:
    capture_times: list[float] = []
    capture_count = 0
    for entry in tool_call_metadata:
        timings = _timings(entry)
        capture = timings.get("command_exec.capture_upperdir_s")
        if isinstance(capture, (int, float)):
            capture_count += 1
            if capture > 0:
                capture_times.append(float(capture))

    return {
        "capture_upperdir_s_total": sum(capture_times),
        "capture_upperdir_count": capture_count,
        "capture_upperdir_p50_s": _percentile(capture_times, 0.5),
        "capture_upperdir_p95_s": _percentile(capture_times, 0.95),
        "capture_upperdir_max_s": max(capture_times) if capture_times else 0.0,
        "shell_calls": int(shell_count),
        "shell_calls_with_capture": capture_count,
    }


# ---------------------------------------------------------------------------
# OCC aggregation
# ---------------------------------------------------------------------------


def _aggregate_occ(
    tool_call_metadata: Sequence[dict[str, Any]],
    *,
    intentional_conflicts: int,
) -> dict[str, Any]:
    commit_totals: list[float] = []
    publish_totals: list[float] = []
    resume_waits: list[float] = []
    conflict_count = 0
    for entry in tool_call_metadata:
        timings = _timings(entry)
        commit_total = timings.get("occ.commit.total_s")
        if isinstance(commit_total, (int, float)) and commit_total > 0:
            commit_totals.append(float(commit_total))
        publish = timings.get("occ.commit.publish_layer_s")
        if isinstance(publish, (int, float)) and publish > 0:
            publish_totals.append(float(publish))
        wait = timings.get("occ.apply.commit_resume_wait_s")
        if isinstance(wait, (int, float)) and wait > 0:
            resume_waits.append(float(wait))
        if entry.get("is_error"):
            metadata = entry.get("metadata") or {}
            if metadata.get("conflict_reason"):
                conflict_count += 1

    return {
        "changeset_count": len(commit_totals),
        "commit_count": len(commit_totals),
        "commit_total_s": sum(commit_totals),
        "commit_p50_s": _percentile(commit_totals, 0.5),
        "commit_p95_s": _percentile(commit_totals, 0.95),
        "commit_max_s": max(commit_totals) if commit_totals else 0.0,
        "publish_layer_total_s": sum(publish_totals),
        "publish_layer_p50_s": _percentile(publish_totals, 0.5),
        "commit_resume_wait_total_s": sum(resume_waits),
        "commit_resume_wait_p95_s": _percentile(resume_waits, 0.95),
        "conflict_count": int(conflict_count),
        "conflict_expected_count": int(intentional_conflicts),
        "conflict_unexpected_count": max(int(conflict_count) - int(intentional_conflicts), 0),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timings(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata") or {}
    timings = metadata.get("timings") or {}
    return timings if isinstance(timings, dict) else {}


def _percentile(values: Iterable[float], p: float) -> float:
    sorted_values = sorted(float(v) for v in values)
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = max(0, min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


__all__ = ["PERF_SCHEMA", "aggregate_perf_metrics"]
