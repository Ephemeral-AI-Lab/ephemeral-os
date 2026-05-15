"""``run_scenario`` — orchestration entry point for the live e2e framework.

Generic over the dataset: callers pass in the entry-prompt string and the
sandbox id, and the framework wires the AuditEventBus, AuditRecorder,
MockSquadRunner, scenario hooks, and ``start_task_center_entry_run`` into a
single coroutine that returns a :class:`RunReport`.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from task_center import TaskCenterSandboxBridge, start_task_center_entry_run

from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.audit.events import Event, EventType
from task_center_runner.audit.node_id import NodeId
from task_center_runner.audit.recorder import AuditRecorder
from task_center_runner.audit.stream_bridge import stream_bridge
from task_center_runner.hooks.registry import (
    Hook,
    HookResult,
    HookSet,
    MutableMockState,
)
from task_center_runner.scenarios.base import Scenario
from task_center_runner.squad.definitions import registered_mock_agents
from task_center_runner.squad.prompt_inspector import (
    LaunchRecord,
    PromptInspection,
    ToolCallRecord,
)
from task_center_runner.squad.runner import MockSquadRunner
from task_center_runner.squad.sandbox_probe import SandboxCheck
from task_center_runner.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


@dataclass(slots=True)
class RunReport:
    """Result of one :func:`run_scenario` invocation."""

    scenario_name: str
    task_center_run_id: str
    request_id: str
    sandbox_id: str
    instance_id: str
    run_dir: Path
    task_center_status: str | None
    duration_s: float
    events: list[Event] = field(default_factory=list)
    seen_event_types: list[EventType] = field(default_factory=list)
    hook_results: list[HookResult] = field(default_factory=list)
    mutable_state_flags: dict[str, Any] = field(default_factory=dict)
    launches: list[LaunchRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    prompt_inspections: list[PromptInspection] = field(default_factory=list)
    sandbox_checks: list[SandboxCheck] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    graph_summary: dict[str, Any] = field(default_factory=dict)
    entry_prompt_sha256: str = ""
    entry_prompt_length: int = 0
    requirement_ledger: list[dict[str, Any]] = field(default_factory=list)
    package_plan: list[dict[str, Any]] = field(default_factory=list)
    matrix_plan: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed_prompt_inspections(self) -> bool:
        return all(item.passed for item in self.prompt_inspections)

    @property
    def passed_sandbox_checks(self) -> bool:
        return all(item.passed for item in self.sandbox_checks)


def _graph_summary(
    bundle: TaskCenterStoreBundle,
    task_center_run_id: str,
) -> dict[str, Any]:
    missions: list[dict[str, Any]] = []
    for mission in bundle.mission_store.list_for_run(task_center_run_id):
        episodes: list[dict[str, Any]] = []
        for episode in bundle.episode_store.list_for_mission(mission.id):
            attempts: list[dict[str, Any]] = []
            for attempt in bundle.attempt_store.list_for_episode(episode.id):
                task_rows = bundle.task_store.list_tasks_for_attempt(attempt.id)
                attempts.append(
                    {
                        "id": attempt.id,
                        "sequence_no": attempt.attempt_sequence_no,
                        "stage": attempt.stage.value,
                        "status": attempt.status.value,
                        "fail_reason": (
                            attempt.fail_reason.value
                            if attempt.fail_reason is not None
                            else None
                        ),
                        "continuation_goal": attempt.continuation_goal,
                        "task_ids": list(attempt.generator_task_ids),
                        "tasks": task_rows,
                    }
                )
            episodes.append(
                {
                    "id": episode.id,
                    "sequence_no": episode.sequence_no,
                    "creation_reason": episode.creation_reason.value,
                    "status": episode.status.value,
                    "goal": episode.goal,
                    "continuation_goal": episode.continuation_goal,
                    "attempts": attempts,
                }
            )
        missions.append(
            {
                "id": mission.id,
                "status": mission.status.value,
                "requested_by_task_id": mission.requested_by_task_id,
                "final_outcome": mission.final_outcome,
                "episodes": episodes,
            }
        )
    return {"missions": missions}


async def run_scenario(
    scenario: Scenario,
    *,
    sandbox_id: str,
    audit_dir: Path,
    repo_dir: str,
    entry_prompt: str,
    stores: TaskCenterStoreBundle | None = None,
    extra_hooks: Sequence[Hook] = (),
    instance_id: str = "",
) -> RunReport:
    """Run *scenario* end-to-end against ``sandbox_id``.

    Bus, recorder, scenario hooks, and the squad runner are wired here. The
    real :func:`task_center.start_task_center_entry_run` is invoked with
    the per-test PG stores so every ORM commit fires the recorder's listeners.
    """
    owns_stores = stores is None
    bundle = stores or create_per_test_task_center_stores()
    bus = AuditEventBus()
    mutable_state = MutableMockState()
    captured_events: list[Event] = []
    hook_results: list[HookResult] = []
    hook_set = HookSet()
    for hook in scenario.hooks():
        hook_set.register(hook)
    for hook in extra_hooks:
        hook_set.register(hook)

    def _on_event(event: Event) -> None:
        captured_events.append(event)
        mutable_state.seen_events.append(event.type)
        for result in hook_set.fire(event, "post", mutable_state):
            hook_results.append(result)

    bus_unsub = bus.subscribe(_on_event)
    started = time.perf_counter()

    recorder: AuditRecorder | None = None
    handle: Any = None
    recorder_holder: list[AuditRecorder | None] = [None]
    bridge_run_id = ""

    async def _on_agent_event(event: Any) -> None:
        bridge_cb = stream_bridge(bus, task_center_run_id=bridge_run_id)
        await bridge_cb(event)
        rec_obj = recorder_holder[0]
        if rec_obj is None:
            return
        agent_run_id = str(getattr(event, "run_id", "") or "")
        if not agent_run_id:
            return
        per_task = rec_obj.message_recorder_for_agent_run(agent_run_id)
        if per_task is None:
            per_task = rec_obj.message_recorder_for_task(agent_run_id)
        if per_task is not None:
            per_task.emit(event)

    self_run_id = uuid.uuid4().hex[:12]
    run_dir = (
        Path(audit_dir)
        / "scenario_logs"
        / scenario.name
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{self_run_id}"
    )
    recorder = AuditRecorder(
        run_dir,
        task_center_run_id="",  # empty → recorder accepts all rows in this run
        bus=bus,
        scenario_name=scenario.name,
        instance_id=instance_id,
        sandbox_id=sandbox_id,
    )
    recorder.start()
    recorder_holder[0] = recorder

    try:
        with registered_mock_agents():
            squad = MockSquadRunner(
                repo_dir=repo_dir,
                bus=bus,
                task_center_run_id="",
                scenario=scenario,
                mutable_state=mutable_state,
                audit_recorder=recorder,
            )
            handle = start_task_center_entry_run(
                config=SimpleNamespace(cwd=repo_dir),
                prompt=entry_prompt,
                sandbox_id=sandbox_id,
                on_agent_event=_on_agent_event,
                task_store=bundle.task_store,
                mission_store=bundle.mission_store,
                episode_store=bundle.episode_store,
                attempt_store=bundle.attempt_store,
                runner=squad,
                context_packet_store=bundle.context_packet_store,
                sandbox_bridge=TaskCenterSandboxBridge(
                    start_fn=lambda existing_id: {"id": existing_id}
                ),
            )
            tcrid = str(handle.task_center_run_id)
            squad._task_center_run_id = tcrid  # noqa: SLF001 — late binding
            bridge_run_id = tcrid
            recorder.bind_task_center_run_id(tcrid)
            bus.publish(
                Event(
                    type=EventType.RUN_STARTED,
                    node=NodeId(task_center_run_id=tcrid),
                )
            )
            await handle.launcher.wait_for_idle()
            bus.publish(
                Event(
                    type=EventType.RUN_COMPLETED,
                    node=NodeId(task_center_run_id=tcrid),
                )
            )

        run = bundle.task_store.get_run(tcrid) or {}
        duration = time.perf_counter() - started
        report = RunReport(
            scenario_name=scenario.name,
            task_center_run_id=tcrid,
            request_id=str(handle.request_id),
            sandbox_id=str(handle.sandbox_id),
            instance_id=instance_id,
            run_dir=run_dir,
            task_center_status=run.get("status"),
            duration_s=duration,
            events=captured_events,
            seen_event_types=list(mutable_state.seen_events),
            hook_results=hook_results,
            mutable_state_flags=dict(mutable_state.flags),
            launches=list(squad.launches),
            tool_calls=list(squad.tool_calls),
            prompt_inspections=list(squad.prompt_inspections),
            sandbox_checks=list(squad.sandbox_checks),
            metrics=recorder.metrics.snapshot() if recorder is not None else {},
            graph_summary=_graph_summary(bundle, tcrid),
            entry_prompt_sha256=hashlib.sha256(
                entry_prompt.encode("utf-8")
            ).hexdigest(),
            entry_prompt_length=len(entry_prompt),
            requirement_ledger=list(getattr(scenario, "requirement_ledger", [])),
            package_plan=list(getattr(scenario, "package_plan", [])),
            matrix_plan=list(getattr(scenario, "matrix_plan", [])),
        )
        return report
    finally:
        bus_unsub()
        if recorder is not None:
            recorder.dispose()
        if owns_stores:
            bundle.close()


__all__ = ["RunReport", "run_scenario"]
