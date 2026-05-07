"""Public TaskCenter API surface for callers outside ``task_center``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.attempt import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task_center.config import HarnessLifecycleConfig
from task_center.context_engine.errors import (
    AgentDefinitionValidationError,
    ContextEngineError,
    MissingContextRecipeError,
    RecipeScopeError,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.scope import ContextScope
from task_center.episode.closure_report import (
    AttemptedPlanEntry,
    AttemptPlanFailed,
    EpisodeClosureReport,
    SuccessContinue,
    TerminalSuccess,
)
from task_center.episode.episode import (
    Episode,
    EpisodeCreationReason,
    EpisodeStatus,
)
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.mission.mission import (
    Mission,
    MissionCloseReport,
    MissionStatus,
)
from task_center.task import (
    TERMINAL_GENERATOR_STATUSES,
    EvaluatorSubmission,
    GeneratorSubmission,
    HarnessTaskRole,
    HarnessTaskStatus,
    PlannedGeneratorTask,
    PlannerFailureSubmission,
    PlannerSubmission,
    evaluator_task_id,
    generator_task_id,
    planner_task_id,
)
from task_center.agent_launch.predicates import (
    PredicateRegistry,
    ResolverContext,
    register_builtin_predicates,
)
from task_center.agent_launch.resolver import (
    AgentResolver,
    AgentSelection,
    RuleBasedAgentResolver,
)
from task_center.attempt.generator_dag import ordered_generator_tasks
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from task_center.attempt.runtime import (
    AgentLaunch,
    AttemptAgentLauncher,
    AttemptRuntime,
)
from task_center.context_engine.composer import ContextComposer, LaunchBundle
from task_center.context_engine.engine import ContextEngine, ContextEngineDeps
from task_center.context_engine.recipes import register_builtin_recipes
from task_center.context_engine.recipes_registry import (
    ContextRecipe,
    RecipeRegistry,
)
from task_center.context_engine.renderer import (
    MarkdownPromptRenderer,
    PromptRenderer,
)
from task_center.entry_task_controller import EntryTaskController
from task_center.episode.manager import EpisodeManager, OrchestratorFactory
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.mission.close_report_delivery import MissionCloseReportRouter
from task_center.mission.handler import CloseReportSink, MissionHandler
from task_center.mission.starter import MissionStarter, StartedMission
from task_center.sandbox_bridge import (
    TaskCenterSandboxBinding,
    TaskCenterSandboxBridge,
)

if TYPE_CHECKING:
    from task_center.entry import (
        ENTRY_AGENT_NAME,
        ENTRY_SPAWN_REASON,
        TaskCenterEntryCoordinator,
        TaskCenterEntryHandle,
        start_task_center_entry_run,
    )

_ENTRY_EXPORTS = {
    "ENTRY_AGENT_NAME",
    "ENTRY_SPAWN_REASON",
    "TaskCenterEntryCoordinator",
    "TaskCenterEntryHandle",
    "start_task_center_entry_run",
}


def __getattr__(name: str) -> object:
    if name in _ENTRY_EXPORTS:
        from task_center import entry

        value = getattr(entry, name)
        globals()[name] = value
        return value
    raise AttributeError(name)


__all__ = [
    "ENTRY_AGENT_NAME",
    "ENTRY_SPAWN_REASON",
    "TERMINAL_GENERATOR_STATUSES",
    "AgentDefinitionValidationError",
    "AgentLaunch",
    "AgentResolver",
    "AgentSelection",
    "Attempt",
    "AttemptAgentLauncher",
    "AttemptFailReason",
    "AttemptOrchestrator",
    "AttemptOrchestratorRegistry",
    "AttemptPlanFailed",
    "AttemptRuntime",
    "AttemptStage",
    "AttemptStatus",
    "AttemptedPlanEntry",
    "CloseReportSink",
    "ContextBlock",
    "ContextBlockKind",
    "ContextComposer",
    "ContextEngine",
    "ContextEngineDeps",
    "ContextEngineError",
    "ContextPacket",
    "ContextPriority",
    "ContextRecipe",
    "ContextRefs",
    "ContextScope",
    "EntryTaskController",
    "Episode",
    "EpisodeClosureReport",
    "EpisodeCreationReason",
    "EpisodeManager",
    "EpisodeManagerRegistry",
    "EpisodeStatus",
    "EvaluatorSubmission",
    "GeneratorSubmission",
    "HarnessLifecycleConfig",
    "HarnessTaskRole",
    "HarnessTaskStatus",
    "LaunchBundle",
    "MarkdownPromptRenderer",
    "MissingContextRecipeError",
    "Mission",
    "MissionCloseReport",
    "MissionCloseReportRouter",
    "MissionHandler",
    "MissionStarter",
    "MissionStatus",
    "OrchestratorFactory",
    "PlannedGeneratorTask",
    "PlannerFailureSubmission",
    "PlannerSubmission",
    "PredicateRegistry",
    "PromptRenderer",
    "RecipeRegistry",
    "RecipeScopeError",
    "ResolverContext",
    "RuleBasedAgentResolver",
    "StartedMission",
    "SuccessContinue",
    "TaskCenterEntryCoordinator",
    "TaskCenterEntryHandle",
    "TaskCenterInvariantViolation",
    "TaskCenterSandboxBinding",
    "TaskCenterSandboxBridge",
    "TerminalSuccess",
    "evaluator_task_id",
    "generator_task_id",
    "ordered_generator_tasks",
    "planner_task_id",
    "register_builtin_predicates",
    "register_builtin_recipes",
    "start_task_center_entry_run",
]
