"""Orchestrator + stage advancer composer wiring.

Confirms that when ``AttemptDeps.composer`` is set, the orchestrator asks the
composer for the planner agent name and context_message, and that planner
terminals are restricted when the launch is nested (its caller attempt is itself
inside another workflow — depth > 1 via ``Workflow.parent_task_id``).
"""

from __future__ import annotations


import pytest

from agents import (
    AgentDefinition,
    AgentRole,
    list_definitions,
    register_definition,
    unregister_definition,
)
from task_center._core.primitives import (
    TaskCenterLifecycleConfig,
    generator_task_id,
)
from task_center.agent_launch.composer import AgentEntryComposer
from task_center.context_engine.engine import ContextEngine, ContextEngineDeps
from task_center.context_engine.recipes import register_builtin_recipes
from task_center.context_engine.recipes_registry import RecipeRegistry
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.attempt.launch import (
    AgentLaunch,
    AttemptDeps,
)
from task_center._core.state import IterationCreationReason


class _RecordingLauncher:
    """Captures launches without actually starting any agent run."""

    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:  # type: ignore[override]
        self.launches.append(launch)


@pytest.fixture(autouse=True)
def _isolate_global_registries():
    saved_recipes = dict(RecipeRegistry._registry)
    saved_definitions = list_definitions()
    RecipeRegistry.clear()
    _clear_definitions()
    register_builtin_recipes()
    yield
    RecipeRegistry.clear()
    _clear_definitions()
    RecipeRegistry._registry.update(saved_recipes)
    for definition in saved_definitions:
        register_definition(definition)


def _clear_definitions() -> None:
    for definition in list_definitions():
        unregister_definition(definition.name)


@pytest.fixture
def composer_runtime(
    workflow_store, iteration_store, attempt_store, task_store
) -> tuple[AttemptDeps, _RecordingLauncher]:
    launcher = _RecordingLauncher()
    deps = ContextEngineDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
    )
    composer = AgentEntryComposer.default(ContextEngine(deps))
    runtime = AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=AttemptOrchestratorRegistry(),
        iteration_coordinators=None,
        lifecycle_config=TaskCenterLifecycleConfig(),
        composer=composer,
    )
    return runtime, launcher


def _real_planner_router():
    """Reuse the planner profile's real terminal_routing rule (no drift)."""
    from pathlib import Path

    import agents as _agents_pkg
    from agents.definition.loader import load_agents_tree

    profiles = load_agents_tree(Path(_agents_pkg.__file__).parent / "profile")
    return next(p for p in profiles if p.name == "planner").terminal_router


def _register_planner_agents() -> None:
    planner = AgentDefinition(
        name="planner",
        description="planner",
        role=AgentRole.PLANNER,
        context_recipe="planner",
        terminals=["submit_plan_closes_goal", "submit_plan_defers_goal"],
        tool_call_limit=10,
        system_prompt="PLANNER",
    )
    # Fabricated definition bypasses the loader, so attach the routing callable
    # the loader would normally resolve from ``terminal_routing:`` frontmatter.
    planner._terminal_router = _real_planner_router()
    register_definition(planner)


def _seed_workflow_iteration_attempt(
    workflow_store,
    iteration_store,
    attempt_store,
    task_center_run_id,
    *,
    parent_task_id: str | None,
):
    workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=parent_task_id,
        workflow_goal="overall",
    )
    iteration = iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="seg goal",
        attempt_budget=2,
    )
    attempt = attempt_store.insert(
        iteration_id=iteration.id, attempt_sequence_no=1
    )
    return workflow, iteration, attempt


def test_planner_launched_via_composer_uses_base_when_top_level(
    composer_runtime,
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
):
    runtime, launcher = composer_runtime
    _register_planner_agents()
    # Top-level workflow: parent task is the synthetic root bootstrap (encodes
    # no attempt), so depth == 1 and the planner is NOT nested.
    _seed_workflow_iteration_attempt(
        workflow_store,
        iteration_store,
        attempt_store,
        task_center_run_id,
        parent_task_id=f"{task_center_run_id}:root",
    )
    attempt = attempt_store.list_for_iteration(
        iteration_store.list_for_workflow(
            workflow_store.list_for_run(task_center_run_id)[0].id
        )[0].id
    )[0]
    orchestrator = AttemptOrchestrator(
        attempt=attempt, on_attempt_closed=lambda _id: None, runtime=runtime
    )
    orchestrator.start()
    assert len(launcher.launches) == 1
    launched = launcher.launches[0]
    assert launched.agent_name == "planner"
    assert launched.agent_def is not None
    assert launched.agent_def.system_prompt == "PLANNER"
    assert launched.agent_def.terminals == [
        "submit_plan_closes_goal",
        "submit_plan_defers_goal",
    ]
    assert launched.context_packet_id is None  # no packet store wired
    assert '<iteration iteration_no="1" position="current">' in launched.context
    assert "<iteration_goal>" in launched.context


def test_planner_terminals_restricted_when_nested_in_outer_workflow(
    composer_runtime,
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
):
    runtime, launcher = composer_runtime
    _register_planner_agents()
    # Outer workflow with an attempt whose generator task spawns a child
    # workflow. The child planner is therefore nested (depth > 1).
    _outer_wf, _outer_seg, outer_attempt = _seed_workflow_iteration_attempt(
        workflow_store,
        iteration_store,
        attempt_store,
        task_center_run_id,
        parent_task_id=f"{task_center_run_id}:root",
    )
    spawning_generator_id = generator_task_id(outer_attempt.id, "g")

    child_workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=spawning_generator_id,
        workflow_goal="child",
    )
    child_seg = iteration_store.insert(
        workflow_id=child_workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="child seg",
        attempt_budget=2,
    )
    child_attempt = attempt_store.insert(
        iteration_id=child_seg.id, attempt_sequence_no=1
    )
    orchestrator = AttemptOrchestrator(
        attempt=child_attempt,
        on_attempt_closed=lambda _id: None,
        runtime=runtime,
    )
    orchestrator.start()
    assert len(launcher.launches) == 1
    launched = launcher.launches[0]
    assert launched.agent_name == "planner"
    assert launched.agent_def is not None
    assert launched.agent_def.system_prompt == "PLANNER"
    assert launched.agent_def.terminals == ["submit_plan_closes_goal"]
