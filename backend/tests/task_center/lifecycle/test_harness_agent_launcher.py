"""Production harness agent launcher tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agents.registry import get_definition, register_definition, unregister_definition
from agents.types import AgentDefinition
from engine.runtime.lifecycle import EphemeralRunResult
from server.app_factory import RuntimeConfig
from task_center.attempt import AttemptFailReason, AttemptStatus
from task_center.attempt.launcher import EphemeralAttemptAgentLauncher
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.attempt.runtime import AgentLaunch, AttemptRuntime
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.episode import EpisodeCreationReason
from task_center.task import HarnessTaskRole, HarnessTaskStatus, planner_task_id


@pytest.mark.asyncio
async def test_launcher_passes_metadata_and_routes_planner_exhaustion(
    mission_store,
    episode_store,
    attempt_store,
    task_store,
    task_center_run_id,
    tmp_path,
    composer,
) -> None:
    previous = get_definition("planner")
    register_definition(
        AgentDefinition(
            name="planner",
            description="test planner",
            role="planner",
            context_recipe="planner_v1",
            terminals=["submit_full_plan", "submit_partial_plan"],
        )
    )
    captured: list[dict[str, object]] = []

    async def fake_runner(*args, **kwargs):
        del args
        captured.append(kwargs)
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name="planner",
            event_count=0,
        )

    runtime_ref: AttemptRuntime | None = None
    launcher = EphemeralAttemptAgentLauncher(
        config=RuntimeConfig(cwd=str(tmp_path)),
        runtime=lambda: runtime_ref,
        runner=fake_runner,
    )
    runtime = AttemptRuntime(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=AttemptOrchestratorRegistry(),
        manager_registry=EpisodeManagerRegistry(),
        composer=composer,
    )
    runtime_ref = runtime

    request = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="entry",
        goal="plan this",
    )
    episode = episode_store.insert(
        mission_id=request.id,
        sequence_no=1,
        creation_reason=EpisodeCreationReason.INITIAL,
        goal="plan this",
        attempt_budget=1,
    )
    mission_store.append_episode_id(request.id, episode.id)
    attempt = attempt_store.insert(episode_id=episode.id, attempt_sequence_no=1)
    episode_store.append_attempt_id(episode.id, attempt.id)

    closed: list[str] = []
    orchestrator = AttemptOrchestrator(
        attempt=attempt,
        on_attempt_closed=closed.append,
        runtime=runtime,
    )

    try:
        orchestrator.start()
        await launcher.wait_for_idle()
    finally:
        if previous is None:
            unregister_definition("planner")
        else:
            register_definition(previous)

    assert len(captured) == 1
    metadata = captured[0]["extra_tool_metadata"]
    assert metadata.task_center_task_id == planner_task_id(attempt.id)
    assert metadata.task_center_attempt_id == attempt.id
    assert metadata.attempt_runtime is runtime

    planner_task = task_store.get_task(planner_task_id(attempt.id))
    latest_graph = attempt_store.get(attempt.id)
    assert planner_task is not None
    assert planner_task["status"] == HarnessTaskStatus.FAILED.value
    assert latest_graph is not None
    assert latest_graph.status == AttemptStatus.FAILED
    assert latest_graph.fail_reason == AttemptFailReason.PLANNER_FAILED
    assert closed == [attempt.id]


@dataclass
class _SpyEntryController:
    """Minimal duck-typed stand-in for ``EntryTaskController``.

    The launcher only needs ``apply_run_exhausted`` on the entry-mode path;
    spinning up a real controller (with stores, request handler, registry)
    would test the controller's downstream effects, not the launcher's
    routing decision. This spy isolates *which* sink the launcher picks.
    """

    task_id: str
    exhaustion_summaries: list[str] = field(default_factory=list)

    def apply_run_exhausted(self, *, summary: str) -> None:
        self.exhaustion_summaries.append(summary)


@pytest.mark.asyncio
async def test_launcher_routes_entry_mode_exhaustion_through_controller(
    task_store,
    mission_store,
    episode_store,
    attempt_store,
    task_center_run_id,
    tmp_path,
) -> None:
    """Entry-mode exhaustion lands on the controller, not the orchestrator."""
    entry_task_id = f"{task_center_run_id}:entry"
    previous = get_definition("entry_executor")
    register_definition(
        AgentDefinition(
            name="entry_executor",
            description="test entry executor",
            role="generator",
            context_recipe="entry_executor_v1",
        )
    )
    task_store.upsert_task(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        role=HarnessTaskRole.GENERATOR.value,
        agent_name="entry_executor",
        task_input="entry input",
        status=HarnessTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason="entry_executor",
    )

    async def fake_runner(*args, **kwargs):
        del args, kwargs
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name="entry_executor",
            event_count=0,
        )

    runtime_ref: AttemptRuntime | None = None
    launcher = EphemeralAttemptAgentLauncher(
        config=RuntimeConfig(cwd=str(tmp_path)),
        runtime=lambda: runtime_ref,
        runner=fake_runner,
    )
    spy = _SpyEntryController(task_id=entry_task_id)
    runtime = AttemptRuntime(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=AttemptOrchestratorRegistry(),
        manager_registry=EpisodeManagerRegistry(),
        entry_task_controller=spy,  # type: ignore[arg-type]
    )
    runtime_ref = runtime

    try:
        launcher.launch(
            AgentLaunch(
                task_id=entry_task_id,
                task_center_run_id=task_center_run_id,
                attempt_id=None,
                role=HarnessTaskRole.GENERATOR,
                agent_name="entry_executor",
                task_input="entry input",
                needs=(),
            )
        )
        await launcher.wait_for_idle()
    finally:
        if previous is None:
            unregister_definition("entry_executor")
        else:
            register_definition(previous)

    assert spy.exhaustion_summaries == [
        "Agent run ended without a terminal submission."
    ]


@pytest.mark.asyncio
async def test_launcher_marks_entry_task_failed_when_no_controller_wired(
    task_store,
    mission_store,
    episode_store,
    attempt_store,
    task_center_run_id,
    tmp_path,
) -> None:
    """No entry controller in entry mode → task is force-failed, not left running."""
    entry_task_id = f"{task_center_run_id}:entry"
    previous = get_definition("entry_executor")
    register_definition(
        AgentDefinition(
            name="entry_executor",
            description="test entry executor",
            role="generator",
            context_recipe="entry_executor_v1",
        )
    )
    task_store.upsert_task(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        role=HarnessTaskRole.GENERATOR.value,
        agent_name="entry_executor",
        task_input="entry input",
        status=HarnessTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason="entry_executor",
    )

    async def fake_runner(*args, **kwargs):
        del args, kwargs
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name="entry_executor",
            event_count=0,
        )

    runtime_ref: AttemptRuntime | None = None
    launcher = EphemeralAttemptAgentLauncher(
        config=RuntimeConfig(cwd=str(tmp_path)),
        runtime=lambda: runtime_ref,
        runner=fake_runner,
    )
    runtime = AttemptRuntime(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=AttemptOrchestratorRegistry(),
        manager_registry=EpisodeManagerRegistry(),
    )
    runtime_ref = runtime

    try:
        launcher.launch(
            AgentLaunch(
                task_id=entry_task_id,
                task_center_run_id=task_center_run_id,
                attempt_id=None,
                role=HarnessTaskRole.GENERATOR,
                agent_name="entry_executor",
                task_input="entry input",
                needs=(),
            )
        )
        await launcher.wait_for_idle()
    finally:
        if previous is None:
            unregister_definition("entry_executor")
        else:
            register_definition(previous)

    final_task = task_store.get_task(entry_task_id)
    assert final_task is not None
    assert final_task["status"] == HarnessTaskStatus.FAILED.value
