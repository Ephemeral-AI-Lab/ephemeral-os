"""Test runner scenario and benchmark harness.

The generic scenario harness is dataset-agnostic. Mocked-agent scenario tests
live under ``test_runner.tests.mock``. Real-agent tests live under
``test_runner.tests.real_agent``. SWE-EVO Docker-image setup shared by
both suites lives under ``test_runner.environments.sweevo_image``. The
full SWE-EVO benchmark lifecycle lives under
``test_runner.benchmarks.sweevo`` and is invoked through
``python -m test_runner.benchmarks.sweevo --instance-id <id>``.

Subpackages:

- ``audit``     — event bus, lifecycle observer, recorder, metrics
- ``agent.mock`` — mocked-agent runner, prompt inspector, sandbox probe
- ``core``      — run config, pipeline entrypoint, report types, fixtures
- ``scenarios`` — Scenario protocol + concrete scenarios
- ``tests``     — pytest suites split by mock, real-agent, and capacity boundaries

Top-level exports:

- :class:`RunReport` and :func:`run_scenario`
- :class:`TaskStoreBundle` and :func:`create_per_test_task_stores`
"""

from __future__ import annotations

from test_runner.core.runner import RunReport, run_scenario
from test_runner.core.stores import (
    TaskStoreBundle,
    create_per_test_task_stores,
)

__all__ = [
    "RunReport",
    "TaskStoreBundle",
    "create_per_test_task_stores",
    "run_scenario",
]
