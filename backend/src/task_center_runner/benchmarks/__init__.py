"""``task_center_runner.benchmarks`` — benchmark adapter surface.

Phase 2 adds the Protocols (``BenchmarkInstance``, ``BenchmarkAdapter``); the
SWE-EVO adapter and lifecycle land in Phase 4 under ``benchmarks/sweevo/``.
"""

from __future__ import annotations

from task_center_runner.benchmarks.base import BenchmarkAdapter, BenchmarkInstance

__all__ = ["BenchmarkAdapter", "BenchmarkInstance"]
