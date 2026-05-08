"""SWE-EVO live e2e test framework — package root.

Subpackages:

- ``audit``  — event bus, lifecycle observer, recorder, metrics
- ``hooks``  — Hook protocol + registry + built-in hooks
- ``scenarios`` — Scenario protocol + concrete scenarios + registry
- ``squad``  — mock-agent squad runner, prompt inspector, sandbox probe
- ``tests``  — pytest live e2e tests against real Daytona

Top-level modules:

- ``stores``    — :class:`TaskCenterStoreBundle` + ``create_in_memory_task_center_stores``
- ``runner``    — ``run_scenario`` orchestration entry point
- ``fixtures``  — pytest fixtures (sweevo_instance, sweevo_sandbox, ...)
"""

from __future__ import annotations

__all__: list[str] = []
