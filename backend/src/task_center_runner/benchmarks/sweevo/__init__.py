"""SWE-EVO benchmark adapter for ``task_center_runner``.

Wires ``benchmarks.sweevo`` data into ``run_pipeline`` via:

- :class:`SweevoLifecycle` — the ``LifecycleHooks`` implementation that
  invokes F2P/P2P evaluation in ``after_run`` and writes
  ``sweevo_result.json``.
- :class:`SweevoProvisioner` — the ``SandboxProvisioner`` that seeds an
  externally-created Daytona sandbox at the SWE-EVO base commit.
"""

from __future__ import annotations

from task_center_runner.benchmarks.sweevo.lifecycle import SweevoLifecycle
from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner

__all__ = ["SweevoLifecycle", "SweevoProvisioner"]
