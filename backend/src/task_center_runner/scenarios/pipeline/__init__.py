"""Task-center pipeline state-machine scenarios.

Drive the orchestrator/dispatcher/iteration-manager/goal-handler control
flow with the lightest possible executor action (`preflight` or `fail`).
Failures here mean a regression in `task_center/` proper. See
``docs/wiki/live-e2e-scenario-suite-design.md`` for the full coverage matrix.

Implemented (reference scenarios):
- :class:`AttemptBudgetExhausted`
- :class:`AttemptRetryEvaluatorFailure`
- :class:`AttemptRetryGeneratorFailure`
- :class:`AttemptRetryPlannerFailure`
- :class:`DependencyBlockedDescendants`
- :class:`DependencyDagDiamond`
- :class:`DependencyDagMixed`
- :class:`DependencyDagParallel`
- :class:`DependencyDagSerial`
- :class:`IterativeContinuation`
- :class:`GeneratorFailureQuiescence`
- :class:`InitialGoal`
- :class:`NestedGoal`
- :class:`NestedGoalFailure`
- :class:`PartialParentPlannerFullOnly`
"""

from __future__ import annotations

from task_center_runner.scenarios.pipeline.attempt_budget_exhausted import (
    AttemptBudgetExhausted,
)
from task_center_runner.scenarios.pipeline.attempt_retry_evaluator_failure import (
    AttemptRetryEvaluatorFailure,
)
from task_center_runner.scenarios.pipeline.attempt_retry_generator_failure import (
    AttemptRetryGeneratorFailure,
)
from task_center_runner.scenarios.pipeline.attempt_retry_planner_failure import (
    AttemptRetryPlannerFailure,
)
from task_center_runner.scenarios.pipeline.dependency_blocked_descendants import (
    DependencyBlockedDescendants,
)
from task_center_runner.scenarios.pipeline.dependency_dag_diamond import (
    DependencyDagDiamond,
)
from task_center_runner.scenarios.pipeline.dependency_dag_mixed import (
    DependencyDagMixed,
)
from task_center_runner.scenarios.pipeline.dependency_dag_parallel import (
    DependencyDagParallel,
)
from task_center_runner.scenarios.pipeline.dependency_dag_serial import (
    DependencyDagSerial,
)
from task_center_runner.scenarios.pipeline.first_three_messages_capture import (
    FirstThreeMessagesCapture,
)
from task_center_runner.scenarios.pipeline.iterative_continuation import (
    IterativeContinuation,
)
from task_center_runner.scenarios.pipeline.generator_failure_quiescence import (
    GeneratorFailureQuiescence,
)
from task_center_runner.scenarios.pipeline.initial_goal import InitialGoal
from task_center_runner.scenarios.pipeline.nested_goal import (
    NestedGoal,
    NestedGoalFailure,
)
from task_center_runner.scenarios.pipeline.partial_parent_planner_full_only import (
    PartialParentPlannerFullOnly,
)

__all__ = [
    "AttemptBudgetExhausted",
    "AttemptRetryEvaluatorFailure",
    "AttemptRetryGeneratorFailure",
    "AttemptRetryPlannerFailure",
    "DependencyBlockedDescendants",
    "DependencyDagDiamond",
    "DependencyDagMixed",
    "DependencyDagParallel",
    "DependencyDagSerial",
    "FirstThreeMessagesCapture",
    "IterativeContinuation",
    "GeneratorFailureQuiescence",
    "InitialGoal",
    "NestedGoal",
    "NestedGoalFailure",
    "PartialParentPlannerFullOnly",
]
