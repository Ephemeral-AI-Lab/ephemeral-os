"""SWE-EVO benchmark adapter for ``task_center_runner``.

Phase 4b additive surface — Phase 4f wires ``run_sweevo_real_agent`` to
use ``SWEEvoBenchmark`` + ``SweevoLifecycle`` via ``run_pipeline``. Until
then, this package is import-safe but not wired into the runtime.

The underlying SWE-EVO data layer (``backend/src/benchmarks/sweevo/``) stays
unchanged — this package only thins it into the ``BenchmarkAdapter`` and
``SandboxProvisioner`` Protocols.
"""

from __future__ import annotations

from task_center_runner.benchmarks.sweevo.adapter import SWEEvoBenchmark
from task_center_runner.benchmarks.sweevo.lifecycle import SweevoLifecycle
from task_center_runner.benchmarks.sweevo.provisioner import SweevoProvisioner

__all__ = ["SWEEvoBenchmark", "SweevoLifecycle", "SweevoProvisioner"]
