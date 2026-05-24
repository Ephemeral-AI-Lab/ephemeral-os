"""Shared artifact assertions for 3.1 layer-stack/OCC/overlay live tests."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from task_center_runner.audit.events import EventType


def load_performance_report(run_dir: Path) -> Mapping[str, Any]:
    perf_path = run_dir / "performance_report.json"
    assert perf_path.exists(), f"missing performance report: {perf_path}"
    perf = json.loads(perf_path.read_text(encoding="utf-8"))
    assert perf["schema"] == "task_center_runner.performance_report.v2"
    return mapping(perf)


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def mapping(value: object) -> Mapping[str, Any]:
    assert isinstance(value, dict), value
    return value


def assert_timing_keys_present(
    perf: Mapping[str, Any],
    keys: Sequence[str],
) -> None:
    timing_keys = mapping(mapping(perf["sandbox"])["timing_keys"])
    missing = [key for key in keys if key not in timing_keys]
    assert not missing, f"missing sandbox timing keys: {missing}"
    empty = [
        key
        for key in keys
        if int(mapping(timing_keys[key]).get("count") or 0) <= 0
    ]
    assert not empty, f"sandbox timing keys have no samples: {empty}"


def assert_resource_key_max(
    perf: Mapping[str, Any],
    key: str,
    expected: float,
) -> None:
    resources = mapping(mapping(perf["sandbox"])["resource_keys"])
    assert key in resources, f"missing resource key: {key}"
    actual = float(mapping(resources[key]).get("max") or 0.0)
    assert actual == expected, f"{key} max={actual}, expected {expected}"


def assert_o1_workspace_resource_snapshots(events_path: Path) -> None:
    max_workspace_bytes = max_resource_snapshot(events_path, "workspace_tree_bytes")
    max_workspace_exists = max_resource_snapshot(events_path, "workspace_tree_exists")
    assert max_workspace_bytes == 0.0, (
        "O(1) overlay regression: "
        f"workspace_tree_bytes max={max_workspace_bytes}"
    )
    assert max_workspace_exists == 0.0, (
        "O(1) overlay regression: "
        f"workspace_tree_exists max={max_workspace_exists}"
    )


def max_resource_snapshot(events_path: Path, suffix: str) -> float:
    key = f"resource.command_exec.{suffix}"
    max_value = 0.0
    for resources in iter_resource_snapshots(events_path):
        max_value = max(max_value, float(resources.get(key, 0.0) or 0.0))
    return max_value


def iter_resource_snapshots(events_path: Path) -> Iterable[Mapping[str, Any]]:
    assert events_path.exists(), f"missing sandbox events log: {events_path}"
    for row in jsonl_rows(events_path):
        if row.get("event_type") != EventType.SANDBOX_RESOURCE_SNAPSHOT.value:
            continue
        payload = mapping(row.get("payload") or {})
        resources = payload.get("resources") or payload.get("timings") or payload
        yield mapping(resources)


__all__ = [
    "assert_o1_workspace_resource_snapshots",
    "assert_resource_key_max",
    "assert_timing_keys_present",
    "iter_resource_snapshots",
    "jsonl_rows",
    "load_performance_report",
    "mapping",
    "max_resource_snapshot",
]
