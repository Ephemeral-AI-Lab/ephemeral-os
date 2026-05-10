"""Per-tool latency + counts aggregator.

Subscribes to the in-memory ``AuditEventBus`` via :meth:`observe`. Pairs
``TOOL_CALL_STARTED`` with the matching ``TOOL_CALL_COMPLETED`` /
``TOOL_CALL_ERROR`` event by ``(tool_name, tool_id)`` to compute latency.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from live_e2e.audit.events import Event, EventType


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


class _PerTool:
    """Mutable per-tool counters/latencies."""

    __slots__ = ("count", "errors", "latencies_ms")

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.latencies_ms: list[float] = []


class MetricsAggregator:
    """Aggregate tool-call counts, errors, and latency percentiles."""

    def __init__(self) -> None:
        self._per_tool: dict[str, _PerTool] = {}
        self._open_starts: dict[tuple[str, str], datetime] = {}

    def observe(self, event: Event) -> None:
        """Bus subscriber. Updates per-tool counters."""
        if event.type is EventType.TOOL_CALL_STARTED:
            tool_name = str(event.payload.get("tool_name") or "")
            tool_id = str(event.payload.get("tool_id") or "")
            if tool_name:
                self._open_starts[(tool_name, tool_id)] = event.ts
            return

        if event.type in (EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_ERROR):
            tool_name = str(event.payload.get("tool_name") or "")
            tool_id = str(event.payload.get("tool_id") or "")
            bucket = self._per_tool.setdefault(tool_name, _PerTool())
            bucket.count += 1
            if event.type is EventType.TOOL_CALL_ERROR:
                bucket.errors += 1
            start_ts = self._open_starts.pop((tool_name, tool_id), None)
            if start_ts is not None:
                latency_ms = (event.ts - start_ts).total_seconds() * 1000.0
                bucket.latencies_ms.append(latency_ms)

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


__all__ = ["MetricsAggregator"]
