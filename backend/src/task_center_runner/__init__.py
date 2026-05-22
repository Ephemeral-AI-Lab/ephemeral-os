"""Task-center-runner scenario and benchmark harness.

The generic scenario harness is dataset-agnostic. Mocked-agent scenario tests
live under ``task_center_runner.tests.mock``. Real-agent tests live under
``task_center_runner.tests.real_agent``. SWE-EVO Docker-image setup shared by
both suites lives under ``task_center_runner.environments.sweevo_image``. The
full SWE-EVO benchmark lifecycle lives under
``task_center_runner.benchmarks.sweevo`` and is invoked through
``python -m benchmarks.sweevo --instance-id <id>``.

Subpackages:

- ``audit``     — event bus, lifecycle observer, recorder, metrics
- ``hooks``     — Hook protocol + registry + built-in hooks
- ``scenarios`` — Scenario protocol + concrete scenarios
- ``squad``     — mock-agent squad runner, prompt inspector, sandbox probe
- ``tests``     — pytest suites split by mock, real-agent, and capacity boundaries

Top-level modules:

- ``stores``    — :class:`TaskCenterStoreBundle` + ``create_per_test_task_center_stores``
- ``runner``    — ``run_scenario`` orchestration entry point (added in S-3)
- ``fixtures``  — pytest fixtures (audit_dir, stores) (added in S-3)
- ``environments`` — external environment fixtures such as the SWE-EVO image
"""

from __future__ import annotations

from task_center_runner.core.runner import RunReport, run_scenario
from task_center_runner.core.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)

__all__ = [
    "RunReport",
    "TaskCenterStoreBundle",
    "create_per_test_task_center_stores",
    "run_scenario",
]
