"""Task-center pipeline state-machine scenarios.

Drive the orchestrator/task-dispatcher/iteration-coordinator/workflow-lifecycle control
flow with the lightest possible executor action (`preflight` or `fail`).
Failures here mean a regression in `request/` proper.

Implemented (reference scenarios):
- :class:`AttemptBudgetExhausted`
- :class:`AttemptRetryReducerFailure`
- :class:`AttemptRetryGeneratorFailure`
- :class:`AttemptRetryPlannerFailure`
- :class:`DependencyBlockedDescendants`
- :class:`DependencyDagDiamond`
- :class:`DependencyDagMixed`
- :class:`DependencyDagParallel`
- :class:`DependencyDagSerial`
- :class:`IterativeDeferral`
- :class:`GeneratorFailureQuiescence`
- :class:`InitialWorkflow`
- :class:`NestedWorkflow`
- :class:`NestedWorkflowFailure`
- :class:`DeferredParentPlannerUnifiedTerminal`
"""

from __future__ import annotations

from test_runner.scenarios.pipeline.attempt_budget_exhausted import (
    AttemptBudgetExhausted,
)
from test_runner.scenarios.pipeline.attempt_retry_reducer_failure import (
    AttemptRetryReducerFailure,
)
from test_runner.scenarios.pipeline.attempt_retry_generator_failure import (
    AttemptRetryGeneratorFailure,
)
from test_runner.scenarios.pipeline.attempt_retry_planner_failure import (
    AttemptRetryPlannerFailure,
)
from test_runner.scenarios.pipeline.dependency_blocked_descendants import (
    DependencyBlockedDescendants,
)
from test_runner.scenarios.pipeline.dependency_dag_diamond import (
    DependencyDagDiamond,
)
from test_runner.scenarios.pipeline.dependency_dag_mixed import (
    DependencyDagMixed,
)
from test_runner.scenarios.pipeline.dependency_dag_parallel import (
    DependencyDagParallel,
)
from test_runner.scenarios.pipeline.dependency_dag_serial import (
    DependencyDagSerial,
)
from test_runner.scenarios.pipeline.initial_messages_capture import (
    InitialMessagesCapture,
)
from test_runner.scenarios.pipeline.iterative_deferral import (
    IterativeDeferral,
)
from test_runner.scenarios.pipeline.generator_failure_quiescence import (
    GeneratorFailureQuiescence,
)
from test_runner.scenarios.pipeline.initial_workflow import InitialWorkflow
from test_runner.scenarios.pipeline.nested_workflow import (
    NestedWorkflow,
    NestedWorkflowFailure,
)
from test_runner.scenarios.pipeline.deferred_parent_planner_unified_terminal import (
    DeferredParentPlannerUnifiedTerminal,
)

__all__ = [
    "AttemptBudgetExhausted",
    "AttemptRetryReducerFailure",
    "AttemptRetryGeneratorFailure",
    "AttemptRetryPlannerFailure",
    "DependencyBlockedDescendants",
    "DependencyDagDiamond",
    "DependencyDagMixed",
    "DependencyDagParallel",
    "DependencyDagSerial",
    "InitialMessagesCapture",
    "IterativeDeferral",
    "GeneratorFailureQuiescence",
    "InitialWorkflow",
    "NestedWorkflow",
    "NestedWorkflowFailure",
    "DeferredParentPlannerUnifiedTerminal",
]
