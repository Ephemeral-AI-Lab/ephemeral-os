"""``run_scenario`` — orchestration entry point for the live e2e framework.

Wires the AuditEventBus, AuditRecorder, MockSquadRunner, scenario hooks, and
``start_task_center_entry_run`` into a single coroutine that returns a
:class:`RunReport`.

The legacy mock-execution helper at
``benchmarks.sweevo.mock_agent_execution.run_sweevo_task_center_with_mock_agent_execution``
remains for the legacy unit test; ``run_scenario`` is the new public entry the
pytest live-Daytona fixtures use.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from task_center.api import TaskCenterSandboxBridge, start_task_center_entry_run

from benchmarks.sweevo.dataset import summarize_sweevo_instance
from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from benchmarks.sweevo.live_test.audit.bus import AuditEventBus
from benchmarks.sweevo.live_test.audit.events import Event, EventType
from benchmarks.sweevo.live_test.audit.node_id import NodeId
from benchmarks.sweevo.live_test.audit.recorder import AuditRecorder
from benchmarks.sweevo.live_test.audit.stream_bridge import stream_bridge
from benchmarks.sweevo.live_test.hooks.registry import (
    Hook,
    HookResult,
    HookSet,
    MutableMockState,
)
from benchmarks.sweevo.live_test.scenarios.base import Scenario
from benchmarks.sweevo.live_test.squad.definitions import (
    registered_mock_sweevo_agents,
)
from benchmarks.sweevo.live_test.squad.prompt_inspector import (
    LaunchRecord,
    PromptInspection,
    ToolCallRecord,
)
from benchmarks.sweevo.live_test.squad.runner import MockSquadRunner
from benchmarks.sweevo.live_test.squad.sandbox_probe import SandboxCheck
from benchmarks.sweevo.live_test.stores import (
    TaskCenterStoreBundle,
    create_in_memory_task_center_stores,
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


def _format_short_run_dir(task_center_run_id: str) -> str:
    """Return ``<UTC_iso_compact>_<short_hash>`` per plan §7."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short_hash = task_center_run_id.replace("-", "")[:8] or "00000000"
    return f"{timestamp}_{short_hash}"


async def run_scenario(
    scenario: Scenario,
    *,
    instance: SWEEvoInstance,
    sandbox_id: str,
    audit_dir: Path,
    stores: TaskCenterStoreBundle | None = None,
    repo_dir: str = _REPO_DIR,
    extra_hooks: Sequence[Hook] = (),
    user_prompt: str | None = None,
) -> RunReport:
    """Run *scenario* end-to-end. Returns a :class:`RunReport`.

    Bus, recorder, scenario, hooks, and the squad runner are wired here. The
    real :func:`task_center.api.start_task_center_entry_run` is invoked with
    in-memory stores so every ORM commit fires the recorder's listeners.
    """
    owns_stores = stores is None
    bundle = stores or create_in_memory_task_center_stores()
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
    prompt_text = user_prompt if user_prompt is not None else build_sweevo_user_prompt(
        instance, repo_dir=repo_dir
    )

    recorder: AuditRecorder | None = None
    handle: Any = None
    recorder_holder: list[AuditRecorder | None] = [None]
    bridge_cb = stream_bridge(bus, task_center_run_id="placeholder")

    async def _on_agent_event(event: Any) -> None:
        await bridge_cb(event)
        rec_obj = recorder_holder[0]
        if rec_obj is None:
            return
        agent_run_id = str(getattr(event, "run_id", "") or "")
        if not agent_run_id:
            return
        per_task = rec_obj.message_recorder_for_agent_run(agent_run_id)
        if per_task is not None:
            per_task.emit(event)

    # Construct the recorder BEFORE start_task_center_entry_run so the
    # synchronous initial Mission/Episode/Task commits are captured. The
    # actual task_center_run_id is generated inside start_task_center_entry_run,
    # so we use a self-generated UUID for the run_dir and bind the real id
    # post-fact via :meth:`AuditRecorder.bind_task_center_run_id` for run.json.
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
        instance_id=instance.instance_id,
        sandbox_id=sandbox_id,
    )
    recorder.start()
    recorder_holder[0] = recorder

    try:
        with registered_mock_sweevo_agents():
            squad = MockSquadRunner(
                instance=instance,
                repo_dir=repo_dir,
                bus=bus,
                task_center_run_id="",
                scenario=scenario,
            )
            handle = start_task_center_entry_run(
                config=SimpleNamespace(cwd=repo_dir),
                prompt=prompt_text,
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
            squad._task_center_run_id = tcrid  # noqa: SLF001 — late binding before run
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
            instance_id=instance.instance_id,
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
        )
        # Surface the summary keys for legacy parity helpers.
        report.metrics.setdefault("instance", summarize_sweevo_instance(instance))
        return report
    finally:
        bus_unsub()
        if recorder is not None:
            recorder.dispose()
        if owns_stores:
            bundle.close()


__all__ = ["RunReport", "run_scenario"]
