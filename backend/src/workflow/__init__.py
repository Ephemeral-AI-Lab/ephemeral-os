"""TaskCenter public package surface.

External callers import lifecycle types, orchestrators, submissions, and
sandbox helpers from this package root::

    from workflow import (
        AttemptOrchestrator,
        ContextScope,
        start_task_center_run,
    )

Internal modules import from the canonical submodule path (e.g.
``workflow.state`` for ``Workflow``). The package root is the stable
convenience facade for outside-the-package
callers.

Public names are exposed via ``__getattr__`` so that importing a submodule
(``from workflow.state import Workflow``) does NOT trigger the
heavy agent-launch / context-engine load chain. The cycle would otherwise
be: db.stores -> task_center root -> agent_launch.composer ->
context_engine -> db.stores. Lazy loading keeps the
DTO submodules import-cycle-safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow.agent_launch.composer import AgentEntryComposer
    from workflow.agent_launch.entry_messages import AgentEntryMessages
    from workflow.attempt.plan_dag import ordered_plan_tasks
    from workflow.attempt.orchestrator import AttemptOrchestrator
    from workflow.attempt.launch import AttemptDeps
    from workflow._core.state import (
        Attempt,
        AttemptFailReason,
        AttemptStage,
        AttemptStatus,
        Iteration,
        IterationCreationReason,
        IterationStatus,
        Workflow,
        WorkflowStatus,
    )
    from workflow.context_engine.engine import (
        AgentDefinitionValidationError,
    )
    from workflow.context_engine.scope import ContextScope
    from runtime.entry import (
        TaskCenterEntry,
        TaskCenterEntryHandle,
        start_task_center_run,
    )
    from runtime.sandbox_provisioning import TaskCenterSandboxProvisioner
    from workflow._core.primitives import TaskCenterInvariantViolation
    from workflow.starter import WorkflowStarter, StartedWorkflow
    from workflow.submissions import (
        GeneratorSubmission,
        PlannerSubmission,
        ReducerSubmission,
    )


_STATE = "workflow._core.state"
_SUBMISSIONS = "workflow.submissions"

# Map: public name → (submodule, name_in_submodule)
_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentDefinitionValidationError": (
        "workflow.context_engine.engine",
        "AgentDefinitionValidationError",
    ),
    "Attempt": (_STATE, "Attempt"),
    "AttemptDeps": ("workflow.attempt.launch", "AttemptDeps"),
    "AttemptFailReason": (_STATE, "AttemptFailReason"),
    "AttemptOrchestrator": (
        "workflow.attempt.orchestrator",
        "AttemptOrchestrator",
    ),
    "AttemptStage": (_STATE, "AttemptStage"),
    "AttemptStatus": (_STATE, "AttemptStatus"),
    "AgentEntryComposer": (
        "workflow.agent_launch.composer",
        "AgentEntryComposer",
    ),
    "AgentEntryMessages": (
        "workflow.agent_launch.entry_messages",
        "AgentEntryMessages",
    ),
    "ContextScope": ("workflow.context_engine.scope", "ContextScope"),
    "Iteration": (_STATE, "Iteration"),
    "IterationCreationReason": (_STATE, "IterationCreationReason"),
    "IterationStatus": (_STATE, "IterationStatus"),
    "GeneratorSubmission": (_SUBMISSIONS, "GeneratorSubmission"),
    "ReducerSubmission": (_SUBMISSIONS, "ReducerSubmission"),
    "Workflow": (_STATE, "Workflow"),
    "WorkflowStarter": ("workflow.starter", "WorkflowStarter"),
    "WorkflowStatus": (_STATE, "WorkflowStatus"),
    "PlannerSubmission": (_SUBMISSIONS, "PlannerSubmission"),
    "StartedWorkflow": ("workflow.starter", "StartedWorkflow"),
    "TaskCenterInvariantViolation": (
        "workflow._core.primitives",
        "TaskCenterInvariantViolation",
    ),
    "TaskCenterSandboxProvisioner": (
        "runtime.sandbox_provisioning",
        "TaskCenterSandboxProvisioner",
    ),
    "TaskCenterEntry": ("runtime.entry", "TaskCenterEntry"),
    "TaskCenterEntryHandle": (
        "runtime.entry",
        "TaskCenterEntryHandle",
    ),
    "ordered_plan_tasks": (
        "workflow.attempt.plan_dag",
        "ordered_plan_tasks",
    ),
    "start_task_center_run": (
        "runtime.entry",
        "start_task_center_run",
    ),
}


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'workflow' has no attribute {name!r}")
    module_path, attr = target
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
