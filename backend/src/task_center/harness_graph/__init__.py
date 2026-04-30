"""Harness graph lifecycle exports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from task_center.harness_graph.factory import (
        make_harness_graph_orchestrator_factory,
    )
    from task_center.harness_graph.graph import (
        HarnessGraph,
        HarnessGraphFailReason,
        HarnessGraphStage,
        HarnessGraphStatus,
    )
    from task_center.harness_graph.orchestrator import HarnessGraphOrchestrator
    from task_center.harness_graph.orchestrator_registry import (
        HarnessGraphOrchestratorRegistry,
    )
    from task_center.harness_graph.runtime import (
        HarnessAgentLaunch,
        HarnessAgentLauncher,
        HarnessGraphRuntime,
    )
    from task_center.harness_graph.task_graph import (
        all_generators_done,
        all_generators_quiescent,
        any_generator_failed_or_blocked,
        assert_generator_deps_exist,
        blocked_descendant_ids,
        dependency_task_ids,
        generator_status_map,
        ordered_generator_tasks,
        ready_pending_generator_ids,
    )

_EXPORT_MODULES = {
    "HarnessAgentLaunch": "task_center.harness_graph.runtime",
    "HarnessAgentLauncher": "task_center.harness_graph.runtime",
    "HarnessGraph": "task_center.harness_graph.graph",
    "HarnessGraphFailReason": "task_center.harness_graph.graph",
    "HarnessGraphOrchestrator": "task_center.harness_graph.orchestrator",
    "HarnessGraphOrchestratorRegistry": (
        "task_center.harness_graph.orchestrator_registry"
    ),
    "HarnessGraphRuntime": "task_center.harness_graph.runtime",
    "HarnessGraphStage": "task_center.harness_graph.graph",
    "HarnessGraphStatus": "task_center.harness_graph.graph",
    "all_generators_done": "task_center.harness_graph.task_graph",
    "all_generators_quiescent": "task_center.harness_graph.task_graph",
    "any_generator_failed_or_blocked": "task_center.harness_graph.task_graph",
    "assert_generator_deps_exist": "task_center.harness_graph.task_graph",
    "blocked_descendant_ids": "task_center.harness_graph.task_graph",
    "dependency_task_ids": "task_center.harness_graph.task_graph",
    "generator_status_map": "task_center.harness_graph.task_graph",
    "make_harness_graph_orchestrator_factory": "task_center.harness_graph.factory",
    "ordered_generator_tasks": "task_center.harness_graph.task_graph",
    "ready_pending_generator_ids": "task_center.harness_graph.task_graph",
}

__all__ = [
    "HarnessAgentLaunch",
    "HarnessAgentLauncher",
    "HarnessGraph",
    "HarnessGraphFailReason",
    "HarnessGraphOrchestrator",
    "HarnessGraphOrchestratorRegistry",
    "HarnessGraphRuntime",
    "HarnessGraphStage",
    "HarnessGraphStatus",
    "all_generators_done",
    "all_generators_quiescent",
    "any_generator_failed_or_blocked",
    "assert_generator_deps_exist",
    "blocked_descendant_ids",
    "dependency_task_ids",
    "generator_status_map",
    "make_harness_graph_orchestrator_factory",
    "ordered_generator_tasks",
    "ready_pending_generator_ids",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
