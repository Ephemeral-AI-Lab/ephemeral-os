"""TaskCenter public package surface.

External callers import lifecycle types, orchestrators, submissions, and
sandbox helpers from this package root::

    from task_center import (
        AttemptOrchestrator,
        ContextScope,
        start_task_center_run,
    )

Internal modules import from the canonical submodule path (e.g.
``task_center.workflow.state`` for ``Workflow``). The package root is the stable
convenience facade for outside-the-package
callers.

Public names are exposed via ``__getattr__`` so that importing a submodule
(``from task_center.workflow.state import Workflow``) does NOT trigger the
heavy agent-launch / context-engine load chain. The cycle would otherwise
be: db.stores -> task_center root -> agent_launch.composer ->
terminal_routing -> db.stores. Lazy loading keeps the
DTO submodules import-cycle-safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_center.agent_launch.composer import AgentEntryComposer
    from task_center.agent_launch.entry_messages import AgentEntryMessages
    from task_center.attempt.plan_dag import ordered_plan_tasks
    from task_center.attempt.orchestrator import AttemptOrchestrator
    from task_center.attempt.launch import AttemptDeps
    from task_center._core.state import (
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
    from task_center.context_engine.engine import (
        AgentDefinitionValidationError,
    )
    from task_center.context_engine.packet import ContextPacket
    from task_center.context_engine.recipes_registry import RecipeRegistry
    from task_center.context_engine.scope import ContextScope
    from task_center.entry.bootstrap import (
        TaskCenterEntry,
        TaskCenterEntryHandle,
        start_task_center_run,
    )
    from task_center.entry import TaskCenterSandboxProvisioner
    from task_center._core.primitives import TaskCenterInvariantViolation
    from task_center.workflow.starter import WorkflowStarter, StartedWorkflow
    from task_center.submissions import (
        GeneratorSubmission,
        PlannedGeneratorTask,
        PlannedReducerTask,
        PlannerSubmission,
        ReducerSubmission,
    )


_STATE = "task_center._core.state"
_SUBMISSIONS = "task_center.submissions"

# Map: public name → (submodule, name_in_submodule)
_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentDefinitionValidationError": (
        "task_center.context_engine.engine",
        "AgentDefinitionValidationError",
    ),
    "Attempt": (_STATE, "Attempt"),
    "AttemptDeps": ("task_center.attempt.launch", "AttemptDeps"),
    "AttemptFailReason": (_STATE, "AttemptFailReason"),
    "AttemptOrchestrator": (
        "task_center.attempt.orchestrator",
        "AttemptOrchestrator",
    ),
    "AttemptStage": (_STATE, "AttemptStage"),
    "AttemptStatus": (_STATE, "AttemptStatus"),
    "AgentEntryComposer": (
        "task_center.agent_launch.composer",
        "AgentEntryComposer",
    ),
    "AgentEntryMessages": (
        "task_center.agent_launch.entry_messages",
        "AgentEntryMessages",
    ),
    "ContextPacket": ("task_center.context_engine.packet", "ContextPacket"),
    "ContextScope": ("task_center.context_engine.scope", "ContextScope"),
    "Iteration": (_STATE, "Iteration"),
    "IterationCreationReason": (_STATE, "IterationCreationReason"),
    "IterationStatus": (_STATE, "IterationStatus"),
    "GeneratorSubmission": (_SUBMISSIONS, "GeneratorSubmission"),
    "ReducerSubmission": (_SUBMISSIONS, "ReducerSubmission"),
    "Workflow": (_STATE, "Workflow"),
    "WorkflowStarter": ("task_center.workflow.starter", "WorkflowStarter"),
    "WorkflowStatus": (_STATE, "WorkflowStatus"),
    "PlannedGeneratorTask": (_SUBMISSIONS, "PlannedGeneratorTask"),
    "PlannedReducerTask": (_SUBMISSIONS, "PlannedReducerTask"),
    "PlannerSubmission": (_SUBMISSIONS, "PlannerSubmission"),
    "RecipeRegistry": (
        "task_center.context_engine.recipes_registry",
        "RecipeRegistry",
    ),
    "StartedWorkflow": ("task_center.workflow.starter", "StartedWorkflow"),
    "TaskCenterInvariantViolation": (
        "task_center._core.primitives",
        "TaskCenterInvariantViolation",
    ),
    "TaskCenterSandboxProvisioner": (
        "task_center.entry",
        "TaskCenterSandboxProvisioner",
    ),
    "TaskCenterEntry": ("task_center.entry.bootstrap", "TaskCenterEntry"),
    "TaskCenterEntryHandle": (
        "task_center.entry.bootstrap",
        "TaskCenterEntryHandle",
    ),
    "ordered_plan_tasks": (
        "task_center.attempt.plan_dag",
        "ordered_plan_tasks",
    ),
    "start_task_center_run": (
        "task_center.entry.bootstrap",
        "start_task_center_run",
    ),
}


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'task_center' has no attribute {name!r}")
    module_path, attr = target
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
