"""US-018: nested planner keeps unified terminal surface.

Builds a parent request whose harness attempt submitted a partial plan, spawns
a child request, then asserts the planner spawned for the child:

* remains the single ``planner`` agent;
* receives the unified planner terminal. Nested deferral is now enforced by the
  submission prehook rather than by mutating launch terminals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents import (
    list_definitions,
    load_agents_tree,
    register_definition,
    unregister_definition,
)
from task_center._core.primitives import (
    TaskCenterLifecycleConfig,
    generator_task_id,
)
from task_center.agent_launch.composer import AgentEntryComposer
from task_center.context_engine.engine import ContextEngine, ContextEngineDeps
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.attempt.launch import (
    AgentLaunch,
    AttemptDeps,
)
from task_center._core.state import IterationCreationReason


REPO_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "backend" / "src" / "agents").is_dir()
)
AGENTS_ROOT = REPO_ROOT / "backend" / "src" / "agents" / "profile"


class _RecordingLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:  # type: ignore[override]
        self.launches.append(launch)


@pytest.fixture(autouse=True)
def _isolate_global_registries():
    saved_definitions = list_definitions()
    _clear_definitions()
    # Load every agent.md in the repo so launch lookups succeed.
    for definition in load_agents_tree(AGENTS_ROOT):
        register_definition(definition)
    yield
    _clear_definitions()
    for definition in saved_definitions:
        register_definition(definition)


def _clear_definitions() -> None:
    for definition in list_definitions():
        unregister_definition(definition.name)


def _runtime_with_composer(
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


def _seed_partial_plan_caller(
    workflow_store,
    iteration_store,
    attempt_store,
    task_store,
    task_center_run_id,
):
    parent_req = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=None,
        workflow_goal="parent",
    )
    parent_seg = iteration_store.insert(
        workflow_id=parent_req.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="parent seg",
        attempt_budget=2,
    )
    caller_attempt = attempt_store.insert(iteration_id=parent_seg.id, attempt_sequence_no=1)
    attempt_store.set_deferred_goal(
        caller_attempt.id,
        deferred_goal_for_next_iteration="continue here",
    )
    # The caller is a generator task of the caller attempt; its id encodes that
    # attempt so the depth walk (child -> caller attempt -> parent workflow)
    # makes the child planner nested.
    caller_task_id = generator_task_id(caller_attempt.id, "caller")
    task_store.upsert_task(
        task_id=caller_task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message="x",
        status="running",
        outcomes=[],
        needs=[],
    )
    return parent_req, caller_task_id


def test_partial_plan_caller_child_planner_keeps_unified_plan_terminal(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    runtime, launcher = _runtime_with_composer(
        workflow_store, iteration_store, attempt_store, task_store
    )
    _parent_req, caller_task_id = _seed_partial_plan_caller(
        workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
    )

    # Child request spawned by the partial-plan caller task.
    child_req = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=caller_task_id,
        workflow_goal="child",
    )
    child_seg = iteration_store.insert(
        workflow_id=child_req.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="child seg",
        attempt_budget=2,
    )
    child_graph = attempt_store.insert(iteration_id=child_seg.id, attempt_sequence_no=1)
    orchestrator = AttemptOrchestrator(
        attempt=child_graph,
        on_attempt_closed=lambda _id: None,
        runtime=runtime,
    )
    orchestrator.start()

    assert len(launcher.launches) == 1
    launched = launcher.launches[0]

    assert launched.agent_name == "planner"
    assert launched.agent_def is not None
    assert launched.agent_def.terminals == ["submit_planner_outcome"]
    assert launched.task_guidance is not None
    assert "submit_planner_outcome" in launched.task_guidance
