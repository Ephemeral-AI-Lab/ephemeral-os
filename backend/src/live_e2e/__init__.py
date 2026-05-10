"""Live E2E testing framework — generic scenario harness.

Lifted from ``benchmarks.sweevo.live_test`` per
``docs/wiki/live-e2e-testing-framework-design.md``. Dataset-agnostic — SWE-EVO
consumers wire through ``benchmarks.sweevo.live_test`` (a thin shim that
provides the SWE-EVO sandbox provisioner + entry prompt builder).

Subpackages:

- ``audit``     — event bus, lifecycle observer, recorder, metrics
- ``hooks``     — Hook protocol + registry + built-in hooks
- ``scenarios`` — Scenario protocol + concrete scenarios
- ``squad``     — mock-agent squad runner, prompt inspector, sandbox probe
- ``tests``     — pytest live e2e tests (PG-backed integration + Daytona-gated)

Top-level modules:

- ``stores``    — :class:`TaskCenterStoreBundle` + ``create_per_test_task_center_stores``
- ``runner``    — ``run_scenario`` orchestration entry point (added in S-3)
- ``fixtures``  — pytest fixtures (audit_dir, stores) (added in S-3)
"""

from __future__ import annotations

from live_e2e.runner import RunReport, run_scenario
from live_e2e.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)

__all__ = [
    "RunReport",
    "TaskCenterStoreBundle",
    "create_per_test_task_center_stores",
    "run_scenario",
]
