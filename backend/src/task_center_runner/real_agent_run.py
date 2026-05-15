"""Real-agent runtime assembly for SWE-EVO live-e2e.

Wraps the production ``start_task_center_entry_run(runner=None)`` seam with
the live-e2e audit harness (``AuditEventBus`` + ``AuditRecorder``) so a real
LLM run of one SWE-EVO instance produces the same on-disk audit tree as the
mock-runner scenario path, with the addition of a ``sweevo_result.json``
holding the F2P/P2P verdict.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from task_center import TaskCenterSandboxBridge, start_task_center_entry_run

from benchmarks.sweevo.evaluation import evaluate_sweevo_result
from benchmarks.sweevo.models import SWEEvoInstance, SWEEvoResult, _REPO_DIR
from benchmarks.sweevo.prompt import build_sweevo_user_prompt
from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.audit.events import Event, EventType
from task_center_runner.audit.node_id import NodeId
from task_center_runner.audit.recorder import AuditRecorder, _atomic_write_json
from task_center_runner.audit.stream_bridge import stream_bridge
from task_center_runner.real_agent_bootstrap import bootstrap_real_agent_runtime
from task_center_runner.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)


@dataclass(slots=True)
class RealAgentRunReport:
    """Compact result handed back to the CLI / pytest entrypoints.

    ``sweevo_result`` is always populated — F2P/P2P only when the task center
    reached ``done`` and the wall-clock cap was not hit; otherwise a failure
    sentinel with ``resolved=False`` and ``fix_rate=0.0``.
    """

    instance_id: str
    task_center_run_id: str
    sandbox_id: str
    run_dir: Path
    task_center_status: str | None
    sweevo_result: SWEEvoResult
    aborted_by_timeout: bool = False


def _count_task_outcomes(task_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    total = len(task_rows)
    completed = sum(1 for row in task_rows if row.get("status") == "done")
    failed = sum(1 for row in task_rows if row.get("status") == "failed")
    return total, completed, failed


async def run_sweevo_real_agent(
    *,
    instance: SWEEvoInstance,
    sandbox_id: str,
    audit_dir: Path,
    repo_dir: str = _REPO_DIR,
    stores: TaskCenterStoreBundle | None = None,
    max_duration_s: float = 1800.0,
) -> RealAgentRunReport:
    """Drive one SWE-EVO instance through the real-LLM task-center pipeline.

    Mirrors :func:`task_center_runner.runner.run_scenario` minus mock-runner / hook
    plumbing. Returns once the run drains (or the wall-clock cap fires).
    """
    bootstrap_real_agent_runtime()

    owns_stores = stores is None
    bundle = stores or create_per_test_task_center_stores()

    bus = AuditEventBus()
    self_run_id = uuid.uuid4().hex[:12]
    utc_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        Path(audit_dir)
        / "real_agent"
        / instance.instance_id
        / f"{utc_stamp}_{self_run_id}"
    )

    recorder = AuditRecorder(
        run_dir,
        task_center_run_id="",
        bus=bus,
        scenario_name="real_agent",
        instance_id=instance.instance_id,
        sandbox_id=sandbox_id,
    )
    recorder.start()

    bridge_run_id = ""

    async def _on_agent_event(event: Any) -> None:
        bridge_cb = stream_bridge(bus, task_center_run_id=bridge_run_id)
        await bridge_cb(event)
        agent_run_id = str(getattr(event, "run_id", "") or "")
        if not agent_run_id:
            return
        per_task = recorder.message_recorder_for_agent_run(agent_run_id)
        if per_task is None:
            per_task = recorder.message_recorder_for_task(agent_run_id)
        if per_task is not None:
            per_task.emit(event)

    entry_prompt = build_sweevo_user_prompt(instance, repo_dir=repo_dir)

    from runtime.app_factory import RuntimeConfig

    config = RuntimeConfig(cwd=repo_dir, external_api_client=None)

    started_at = time.perf_counter()
    aborted_by_timeout = False
    tcrid = ""

    try:
        handle = start_task_center_entry_run(
            config=config,
            prompt=entry_prompt,
            sandbox_id=sandbox_id,
            on_agent_event=_on_agent_event,
            task_store=bundle.task_store,
            mission_store=bundle.mission_store,
            episode_store=bundle.episode_store,
            attempt_store=bundle.attempt_store,
            context_packet_store=bundle.context_packet_store,
            runner=None,
            sandbox_bridge=TaskCenterSandboxBridge(
                start_fn=lambda existing_id: {"id": existing_id}
            ),
        )
        tcrid = str(handle.task_center_run_id)
        bridge_run_id = tcrid
        recorder.bind_task_center_run_id(tcrid)
        bus.publish(
            Event(
                type=EventType.RUN_STARTED,
                node=NodeId(task_center_run_id=tcrid),
            )
        )

        try:
            await asyncio.wait_for(handle.launcher.wait_for_idle(), timeout=max_duration_s)
        except asyncio.TimeoutError:
            aborted_by_timeout = True
            # ``handle.launcher._pending`` is the private task set used by
            # ``EphemeralAttemptAgentLauncher`` (agent_launch/launcher.py:61).
            # Cancelling each task here actually stops in-flight LLM API
            # calls; without this the wait_for cancellation only unwinds the
            # awaiter while the launcher tasks keep spending tokens.
            pending = tuple(handle.launcher._pending)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        bus.publish(
            Event(
                type=EventType.RUN_COMPLETED,
                node=NodeId(task_center_run_id=tcrid),
            )
        )

        run_row = bundle.task_store.get_run(tcrid) or {}
        task_center_status = run_row.get("status")
        duration_s = time.perf_counter() - started_at

        task_rows = bundle.task_store.list_tasks_for_run(tcrid)
        task_count, tasks_completed, tasks_failed = _count_task_outcomes(task_rows)

        completed_cleanly = (
            task_center_status == "done" and not aborted_by_timeout
        )
        result = SWEEvoResult(
            plan_id=tcrid,
            instance_id=instance.instance_id,
            status="completed" if completed_cleanly else "failed",
            duration_s=duration_s,
            task_count=task_count,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
        )

        if completed_cleanly:
            result = await evaluate_sweevo_result(instance, result, sandbox_id, repo_dir)
        else:
            result.error = (
                "timeout" if aborted_by_timeout else (task_center_status or "unknown")
            )

        _atomic_write_json(run_dir / "sweevo_result.json", dataclasses.asdict(result))

        return RealAgentRunReport(
            instance_id=instance.instance_id,
            task_center_run_id=tcrid,
            sandbox_id=sandbox_id,
            run_dir=run_dir,
            task_center_status=task_center_status,
            sweevo_result=result,
            aborted_by_timeout=aborted_by_timeout,
        )
    finally:
        recorder.dispose()
        if owns_stores:
            bundle.close()


__all__ = ["RealAgentRunReport", "run_sweevo_real_agent"]
