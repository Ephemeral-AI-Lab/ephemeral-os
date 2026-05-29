"""Per-tool latency + counts aggregator.

Subscribes to the in-memory ``AuditEventBus`` via :meth:`observe`. Pairs
``TOOL_CALL_STARTED`` with the matching ``TOOL_CALL_COMPLETED`` /
``TOOL_CALL_ERROR`` event by ``(tool_name, tool_use_id)`` to compute latency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median
from typing import Any

from task_center_runner.audit.events import Event, EventType

_PREVIEW_CHARS = 240
_SLOWEST_CALL_LIMIT = 25


def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct`` percentile of ``values`` (0 < pct <= 100).

    Uses nearest-rank — the smallest value v such that at least ``pct``%
    of the values are ≤ v. Empty input yields ``0.0``.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(pct / 100.0 * len(ordered))))
    return float(ordered[min(rank, len(ordered)) - 1])


def _latency_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "min_ms": 0.0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p75_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
            "total_ms": 0.0,
        }
    total = float(sum(values))
    return {
        "min_ms": float(min(values)),
        "mean_ms": total / float(len(values)),
        "p50_ms": float(median(values)),
        "p75_ms": _percentile(values, 75.0),
        "p90_ms": _percentile(values, 90.0),
        "p95_ms": _percentile(values, 95.0),
        "p99_ms": _percentile(values, 99.0),
        "max_ms": float(max(values)),
        "total_ms": total,
    }


def _preview(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= _PREVIEW_CHARS:
        return text
    return text[: _PREVIEW_CHARS - 3] + "..."


def _tool_input_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"keys": [], "preview": _preview(value)}
    return {
        "keys": sorted(str(key) for key in value),
        "preview": _preview(value),
    }


def _float_dict(value: object) -> dict[str, float]:
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


@dataclass(slots=True)
class _ToolStart:
    ts: datetime
    node: dict[str, Any]
    input_keys: list[str]
    input_preview: str | None


class _PerTool:
    """Mutable per-tool counters/latencies."""

    __slots__ = ("count", "errors", "latencies_ms", "samples")

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.latencies_ms: list[float] = []
        self.samples: list[dict[str, Any]] = []


class MetricsAggregator:
    """Aggregate tool-call counts, errors, and latency percentiles."""

    def __init__(self) -> None:
        self._per_tool: dict[str, _PerTool] = {}
        self._open_starts: dict[tuple[str, str, str], _ToolStart] = {}

    def observe(self, event: Event) -> None:
        """Bus subscriber. Updates per-tool counters."""
        if event.type is EventType.TOOL_CALL_STARTED:
            tool_name = str(event.payload.get("tool_name") or "unknown")
            if tool_name:
                tool_input = _tool_input_summary(event.payload.get("tool_input"))
                self._open_starts[self._key(event)] = _ToolStart(
                    ts=event.ts,
                    node=asdict(event.node),
                    input_keys=list(tool_input["keys"]),
                    input_preview=tool_input["preview"],
                )
            return

        if event.type in (EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_ERROR):
            tool_name = str(event.payload.get("tool_name") or "unknown")
            bucket = self._per_tool.setdefault(tool_name, _PerTool())
            bucket.count += 1
            if event.type is EventType.TOOL_CALL_ERROR:
                bucket.errors += 1
            start = self._pop_start(event)
            latency_ms: float | None = None
            if start is not None:
                latency_ms = (event.ts - start.ts).total_seconds() * 1000.0
                bucket.latencies_ms.append(latency_ms)
            bucket.samples.append(self._sample(event, start, latency_ms))

    def snapshot(self) -> dict[str, Any]:
        """Render aggregated metrics as a JSON-friendly dict."""
        per_tool: dict[str, dict[str, Any]] = {}
        tool_calls_total = 0
        tool_errors_total = 0
        for name, bucket in self._per_tool.items():
            tool_calls_total += bucket.count
            tool_errors_total += bucket.errors
            latencies = list(bucket.latencies_ms)
            p50 = float(median(latencies)) if latencies else 0.0
            p95 = _percentile(latencies, 95.0) if latencies else 0.0
            total = float(sum(latencies)) if latencies else 0.0
            per_tool[name] = {
                "count": bucket.count,
                "errors": bucket.errors,
                "latencies_ms": latencies,
                "p50_ms": p50,
                "p95_ms": p95,
                "total_ms": total,
            }
        return {
            "per_tool": per_tool,
            "tool_calls_total": tool_calls_total,
            "tool_errors_total": tool_errors_total,
        }

    def performance_snapshot(self) -> dict[str, Any]:
        """Render detailed metrics for performance report generation."""
        per_tool: dict[str, dict[str, Any]] = {}
        all_samples: list[dict[str, Any]] = []
        tool_calls_total = 0
        tool_errors_total = 0
        for name, bucket in self._per_tool.items():
            tool_calls_total += bucket.count
            tool_errors_total += bucket.errors
            latencies = list(bucket.latencies_ms)
            stats = _latency_stats(latencies)
            samples = list(bucket.samples)
            all_samples.extend(samples)
            per_tool[name] = {
                "count": bucket.count,
                "errors": bucket.errors,
                "error_rate": (
                    float(bucket.errors) / float(bucket.count)
                    if bucket.count
                    else 0.0
                ),
                "latencies_ms": latencies,
                **stats,
                "samples": samples,
                "slowest_calls": self._slowest(samples),
            }
        return {
            "schema": "live_e2e.tool_performance.v1",
            "per_tool": per_tool,
            "tool_calls_total": tool_calls_total,
            "tool_errors_total": tool_errors_total,
            "slowest_calls": self._slowest(all_samples),
            "incomplete_calls": [
                {
                    "tool_name": key[0],
                    "tool_use_id": key[1] or None,
                    "agent_run_id": key[2] or None,
                    "started_ts": start.ts.isoformat(),
                    "node": start.node,
                    "input_keys": start.input_keys,
                    "input_preview": start.input_preview,
                }
                for key, start in self._open_starts.items()
            ],
        }

    @staticmethod
    def _slowest(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            samples,
            key=lambda sample: float(sample.get("duration_ms") or -1.0),
            reverse=True,
        )[:_SLOWEST_CALL_LIMIT]

    def _pop_start(self, event: Event) -> "_ToolStart | None":
        """Match a completion to its open start.

        Prefer the exact ``(tool_name, tool_use_id, agent_run_id)`` key (the old
        MockSquadRunner hand-emitted ``TOOL_CALL_STARTED`` with the tool_use_id).
        The real query loop emits ``TOOL_CALL_STARTED`` from ``execute_tool_once``
        WITHOUT a tool_use_id (only the completion carries it), so fall back to
        the open start for the same ``(tool_name, agent_run_id)`` — the loop
        dispatches an agent's tools sequentially, so at most one is open.
        """
        start = self._open_starts.pop(self._key(event), None)
        if start is not None:
            return start
        fallback_key = (
            str(event.payload.get("tool_name") or "unknown"),
            "",
            str(event.node.agent_run_id or ""),
        )
        return self._open_starts.pop(fallback_key, None)

    @staticmethod
    def _key(event: Event) -> tuple[str, str, str]:
        return (
            str(event.payload.get("tool_name") or "unknown"),
            str(event.payload.get("tool_use_id") or ""),
            str(event.node.agent_run_id or ""),
        )

    @staticmethod
    def _sample(
        event: Event,
        start: _ToolStart | None,
        duration_ms: float | None,
    ) -> dict[str, Any]:
        metadata = event.payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        timings = _float_dict(metadata_dict.get("timings"))
        changed_paths = _string_list(metadata_dict.get("changed_paths"))
        sample: dict[str, Any] = {
            "tool_name": str(event.payload.get("tool_name") or "unknown"),
            "tool_use_id": str(event.payload.get("tool_use_id") or "") or None,
            "agent_name": event.node.agent_name,
            "agent_run_id": event.node.agent_run_id,
            "task_center_run_id": event.node.task_center_run_id,
            "started_ts": start.ts.isoformat() if start is not None else None,
            "completed_ts": event.ts.isoformat(),
            "duration_ms": duration_ms,
            "is_error": event.type is EventType.TOOL_CALL_ERROR,
            "is_terminal": bool(event.payload.get("is_terminal")),
            "status": metadata_dict.get("status"),
            "conflict_reason": metadata_dict.get("conflict_reason"),
            "changed_paths": changed_paths,
            "changed_path_count": len(changed_paths),
            "timings_s": timings,
            "metadata_keys": sorted(str(key) for key in metadata_dict),
            "output_preview": _preview(event.payload.get("output")),
        }
        if start is not None:
            sample["start_node"] = start.node
            sample["input_keys"] = start.input_keys
            sample["input_preview"] = start.input_preview
        return sample


__all__ = ["MetricsAggregator"]
