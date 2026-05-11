"""Scenario protocol + scenario registry.

Composite scenarios live at the top level for historical reasons. Focused
scenarios are organized by concern under subpackages — see
``docs/wiki/live-e2e-scenario-suite-design.md`` for the full taxonomy.
"""

from __future__ import annotations

from live_e2e.scenarios.base import Scenario
from live_e2e.scenarios.correctness_testing import CorrectnessTesting
from live_e2e.scenarios.full_case_user_input import FullCaseUserInput
from live_e2e.scenarios.full_stack_adversarial import FullStackAdversarial
from live_e2e.scenarios.pipeline import (
    AttemptBudgetExhausted,
    AttemptRetryEvaluatorFailure,
    DependencyDagMixed,
    DependencyDagSerial,
    EpisodicContinuation,
    GeneratorFailureQuiescence,
    InitialMission,
)
from live_e2e.scenarios.planner_validation import PlannerDuplicateLocalId
from live_e2e.scenarios.sandbox import (
    AutoSquashCommitResume,
    ComplexProjectBuild,
    ComplexProjectBuildShellEditLsp,
    ComplexProjectBuildShellEditLspSmoke,
    ComplexProjectBuildSmoke,
    OccConcurrentConflicts,
)

SCENARIO_REGISTRY: dict[str, type[Scenario]] = {
    # Composite end-to-end scenarios.
    "correctness_testing": CorrectnessTesting,
    "full_case_user_input": FullCaseUserInput,
    "full_stack_adversarial": FullStackAdversarial,
    # Focused pipeline scenarios.
    "pipeline.initial_mission": InitialMission,
    "pipeline.episodic_continuation": EpisodicContinuation,
    "pipeline.attempt_retry_evaluator_failure": AttemptRetryEvaluatorFailure,
    "pipeline.dependency_dag_serial": DependencyDagSerial,
    "pipeline.dependency_dag_mixed": DependencyDagMixed,
    "pipeline.generator_failure_quiescence": GeneratorFailureQuiescence,
    "pipeline.attempt_budget_exhausted": AttemptBudgetExhausted,
    # Focused sandbox scenarios.
    "sandbox.auto_squash_commit_resume": AutoSquashCommitResume,
    "sandbox.complex_project_build": ComplexProjectBuild,
    "sandbox.complex_project_build_shell_edit_lsp": ComplexProjectBuildShellEditLsp,
    "sandbox.complex_project_build_shell_edit_lsp_smoke": (
        ComplexProjectBuildShellEditLspSmoke
    ),
    "sandbox.complex_project_build_smoke": ComplexProjectBuildSmoke,
    "sandbox.occ_concurrent_conflicts": OccConcurrentConflicts,
    # Focused planner-validation scenarios.
    "planner_validation.duplicate_local_id": PlannerDuplicateLocalId,
}

__all__ = [
    "AttemptBudgetExhausted",
    "AttemptRetryEvaluatorFailure",
    "AutoSquashCommitResume",
    "ComplexProjectBuild",
    "ComplexProjectBuildShellEditLsp",
    "ComplexProjectBuildShellEditLspSmoke",
    "ComplexProjectBuildSmoke",
    "CorrectnessTesting",
    "DependencyDagMixed",
    "DependencyDagSerial",
    "EpisodicContinuation",
    "FullCaseUserInput",
    "FullStackAdversarial",
    "GeneratorFailureQuiescence",
    "InitialMission",
    "OccConcurrentConflicts",
    "PlannerDuplicateLocalId",
    "SCENARIO_REGISTRY",
]
