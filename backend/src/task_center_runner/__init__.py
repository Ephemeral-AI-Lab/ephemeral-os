"""Live E2E testing framework — generic scenario harness.

Lifted from the former SWE-EVO live-test harness per
``docs/wiki/live-e2e-testing-framework-design.md``. Dataset-agnostic; SWE-EVO
consumers wire through ``task_center_runner.sweevo_adapter``, which provides the SWE-EVO
sandbox provisioner and entry prompt builder.

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
- ``sweevo_adapter`` — SWE-EVO prompt, sandbox, and pytest fixture adapter
"""

from __future__ import annotations

from task_center_runner.runner import RunReport, run_scenario
from task_center_runner.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)

__all__ = [
    "RunReport",
    "TaskCenterStoreBundle",
    "create_per_test_task_center_stores",
    "run_scenario",
]
