"""Task-center pipeline state-machine scenarios.

Drive the orchestrator/dispatcher/episode-manager/mission-handler control
flow with the lightest possible executor action (`preflight` or `fail`).
Failures here mean a regression in `task_center/` proper. See
``docs/wiki/live-e2e-scenario-suite-design.md`` for the full coverage matrix.

Implemented (reference scenarios):
- :class:`AttemptBudgetExhausted`
- :class:`AttemptRetryEvaluatorFailure`
- :class:`DependencyDagMixed`
- :class:`DependencyDagSerial`
- :class:`EpisodicContinuation`
- :class:`GeneratorFailureQuiescence`
- :class:`InitialMission`
"""

from __future__ import annotations

from live_e2e.scenarios.pipeline.attempt_budget_exhausted import (
    AttemptBudgetExhausted,
)
from live_e2e.scenarios.pipeline.attempt_retry_evaluator_failure import (
    AttemptRetryEvaluatorFailure,
)
from live_e2e.scenarios.pipeline.dependency_dag_mixed import (
    DependencyDagMixed,
)
from live_e2e.scenarios.pipeline.dependency_dag_serial import (
    DependencyDagSerial,
)
from live_e2e.scenarios.pipeline.episodic_continuation import (
    EpisodicContinuation,
)
from live_e2e.scenarios.pipeline.generator_failure_quiescence import (
    GeneratorFailureQuiescence,
)
from live_e2e.scenarios.pipeline.initial_mission import InitialMission

__all__ = [
    "AttemptBudgetExhausted",
    "AttemptRetryEvaluatorFailure",
    "DependencyDagMixed",
    "DependencyDagSerial",
    "EpisodicContinuation",
    "GeneratorFailureQuiescence",
    "InitialMission",
]
