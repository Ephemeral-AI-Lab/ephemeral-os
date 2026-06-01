"""Orchestrator + stage advancer composer wiring.

Confirms that when ``AttemptDeps.composer`` is set, the orchestrator asks the
composer for the planner agent name and instruction, and that planner
terminal selection stays stable; nested deferral policy is enforced by the
``submit_planner_outcome`` prehook rather than mutating launch terminals.
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
from workflow._core.primitives import (
    WorkflowLifecycleConfig,
    generator_task_id,
)
from workflow.agent_launch.composer import AgentEntryComposer
from workflow.context_engine.engine import ContextEngine, ContextEngineDeps
from workflow.attempt.orchestrator import AttemptOrchestrator
from workflow.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from workflow.attempt.launch import (
    AgentLaunch,
    AttemptDeps,
)
from workflow._core.state import IterationCreationReason


class _RecordingLauncher:
    """Captures launches without actually starting any agent run."""

    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:  # type: ignore[override]
        self.launches.append(launch)


@pytest.fixture(autouse=True)
def _isolate_global_registries():
    saved_definitions = list_definitions()
    _clear_definitions()
    yield
    _clear_definitions()
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
        lifecycle_config=WorkflowLifecycleConfig(),
        composer=composer,
    )
    return runtime, launcher


def _register_planner_agents() -> None:
    register_definition(
        AgentDefinition(
            name="planner",
            description="planner",
            role=AgentRole.PLANNER,
            context_recipe="planner",
            terminals=["submit_planner_outcome"],
            tool_call_limit=10,
            system_prompt="PLANNER",
        )
    )


def _seed_workflow_iteration_attempt(
    workflow_store,
    iteration_store,
    attempt_store,
    request_id,
    *,
    parent_task_id: str | None,
):
    workflow = workflow_store.insert(
        request_id=request_id,
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
    attempt = attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    return workflow, iteration, attempt


def test_planner_launched_via_composer_uses_base_when_top_level(
    composer_runtime,
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    request_id,
):
    runtime, launcher = composer_runtime
    _register_planner_agents()
    # Top-level workflow: parent task is the synthetic root bootstrap (encodes
    # no attempt), so depth == 1 and the planner is NOT nested.
    _seed_workflow_iteration_attempt(
        workflow_store,
        iteration_store,
        attempt_store,
        request_id,
        parent_task_id=f"{request_id}:root",
    )
    attempt = attempt_store.list_for_iteration(
        iteration_store.list_for_workflow(workflow_store.list_for_request(request_id)[0].id)[
            0
        ].id
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
    assert launched.agent_def.terminals == ["submit_planner_outcome"]
    assert '<current_iteration sequence="1">' in launched.context
    assert "<goal>" in launched.context


def test_nested_planner_keeps_unified_plan_terminal(
    composer_runtime,
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    request_id,
):
    runtime, launcher = composer_runtime
    _register_planner_agents()
    # Outer workflow with an attempt whose generator task spawns a child
    # workflow. The child planner is therefore nested (depth > 1).
    _outer_wf, _outer_seg, outer_attempt = _seed_workflow_iteration_attempt(
        workflow_store,
        iteration_store,
        attempt_store,
        request_id,
        parent_task_id=f"{request_id}:root",
    )
    spawning_generator_id = generator_task_id(outer_attempt.id, "g")

    child_workflow = workflow_store.insert(
        request_id=request_id,
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
    child_attempt = attempt_store.insert(iteration_id=child_seg.id, attempt_sequence_no=1)
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
    assert launched.agent_def.terminals == ["submit_planner_outcome"]
