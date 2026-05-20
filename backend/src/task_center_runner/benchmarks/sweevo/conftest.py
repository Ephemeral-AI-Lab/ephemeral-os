"""pytest conftest for the SWE-EVO benchmarker live-e2e test.

Re-exports the SWE-EVO fixtures defined in ``fixtures.py`` and pulls in
``audit_dir``/``stores`` from ``task_center_runner.core.fixtures``.
"""

from __future__ import annotations

from task_center_runner.benchmarks.sweevo.fixtures import (  # noqa: F401
    sweevo_instance,
    sweevo_sandbox,
    workspace,
)

pytest_plugins = ["task_center_runner.core.fixtures"]
