# ruff: noqa
"""Live E2E: production executor subagent background workflow.

Validates the real live stack for:
- launching multiple explorer subagents via run_subagent
- checking in-flight/final background task results
- waiting for background tasks to finish
- cancelling a launched background task
- closing the actual TaskCenter root task after live tool evidence is observed

Run with:
    uv run pytest backend/tests/test_e2e/test_subagent_background_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from typing import Any

import pytest

from engine.testing.eval_agent import EvalAgent, EvalResult, ToolCallResult
from message.stream_events import AssistantTurnComplete
from tests.test_e2e.conftest import create_test_sandbox, delete_test_sandbox
from tests.test_e2e.daytona_exec_io import write_text_via_exec
from tests.test_e2e.helpers import log_result

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required"),
]


def _seed_sandbox(sandbox: dict[str, Any]) -> None:
    from sandbox.testing import get_sandbox_service

    svc = get_sandbox_service()
    sb = svc.get_sandbox_object(sandbox["id"])
    sb.process.exec("mkdir -p /home/daytona/subagent_live", timeout=120)
    write_text_via_exec(
        sb,
        "/home/daytona/subagent_live/alpha.txt",
        "ALPHA_SUBAGENT_OK\nalpha owns the planning notes.\n",
    )
    write_text_via_exec(
        sb,
        "/home/daytona/subagent_live/beta.txt",
        "BETA_SUBAGENT_OK\nbeta owns the validation notes.\n",
    )
    for idx in range(12):
        write_text_via_exec(
            sb,
            f"/home/daytona/subagent_live/cancel_payload_{idx:02d}.txt",
            f"CANCEL_PAYLOAD_{idx:02d}\nThis file exists only to keep exploration busy.\n",
        )


@pytest.fixture(scope="module")
def sandbox():
    from agents.builtins import register_builtin_agents

    register_builtin_agents()
    sb = create_test_sandbox("subagent-bg-live")
    _seed_sandbox(sb)
    yield sb
    delete_test_sandbox(sb["id"])


def _truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


async def _run_executor_with_debug(
    sandbox: dict[str, Any],
    prompt: str,
    label: str,
    *,
    stop_when: Callable[[EvalResult], bool],
) -> EvalResult:
    """Run the production TaskCenter executor and return EvalResult-shaped data."""
    from config.model_config import NoActiveModelError, try_get_active_model_kwargs
    from config.settings import load_settings
    from message.event_printer import MultiAgentEventPrinter
    from server.app_factory import RuntimeConfig
    from task_center.agent_spawn import make_production_spawn
    from task_center.center import TaskCenter

    settings = load_settings()
    EvalAgent._ensure_db_ready(settings)
    db_kwargs = try_get_active_model_kwargs() or {}
    if not db_kwargs:
        raise NoActiveModelError("production executor live test requires an active model")
    model = db_kwargs.get("model")
    if not model:
        raise RuntimeError("Active model registration has no 'model' id")

    runtime_config = RuntimeConfig(cwd=".")

    events: list[Any] = []
    printer = MultiAgentEventPrinter(color=False, sink=lambda msg: print(msg, flush=True))

    task_center: TaskCenter
    closed_by_harness = False

    async def _on_event(event: Any) -> None:
        nonlocal closed_by_harness
        events.append(event)
        printer.emit(event)
        if closed_by_harness:
            return
        interim = _result_from_events(events, start)
        if stop_when(interim):
            task_center.submit_task_completion("t1", f"{label}: live evidence satisfied")
            closed_by_harness = True

    task_center = TaskCenter(
        runtime_config,
        spawn_func=make_production_spawn(runtime_config),
        on_event=_on_event,
    )

    print(f"  [TaskCenter] prompt: {_truncate(prompt, 120)}", flush=True)
    start = time.monotonic()
    try:
        root = await task_center.run_query(prompt, sandbox_id=sandbox["id"])
    except Exception:
        tb = traceback.format_exc()
        logger.error("[%s] production executor traceback:\n%s", label, tb)
        print(f"\n[{label}] production executor traceback:\n{tb}", flush=True)
        raise
    finally:
        printer.flush()

    result = _result_from_events(events, start)
    result.metadata.update({
        "root_task_id": root.id,
        "root_status": root.status.value,
        "root_summary": root.summary or "",
        "closed_by_harness": closed_by_harness,
    })
    print(
        f"  [TaskCenter] done: root={root.id} status={root.status} "
        f"summary={_truncate(root.summary or '', 500)}",
        flush=True,
    )
    log_result(result, label)
    _log_tool_trace(result, label)
    return result


def _result_from_events(events: list[Any], start: float) -> EvalResult:
    tool_calls: list[ToolCallResult] = []
    for event in events:
        if isinstance(event, AssistantTurnComplete):
            for tool_use in event.message.tool_uses:
                tool_calls.append(ToolCallResult(name=tool_use.name, input=tool_use.input))
    return EvalResult(
        events=list(events),
        tool_calls=tool_calls,
        latency_ms=(time.monotonic() - start) * 1000,
    )


def _log_tool_trace(result: EvalResult, label: str) -> None:
    lines = [
        f"\n{'=' * 72}",
        f"[{label}] Tool/background trace",
        f"Tool calls: {result.tool_names}",
        "Background started:",
    ]
    for event in result.background_started():
        lines.append(
            f"  - {event.task_id} {event.tool_name} input={_truncate(str(event.tool_input), 300)}"
        )
    lines.append("Tool completions:")
    for event in result.tools_completed():
        lines.append(
            f"  - {event.tool_name} error={event.is_error} output={_truncate(event.output, 700)}"
        )
    lines.append("Background completed:")
    for event in result.background_completed():
        lines.append(
            f"  - {event.task_id} {event.tool_name} error={event.is_error} "
            f"output={_truncate(event.output, 700)}"
        )
    lines.append(f"{'=' * 72}")
    logger.info("\n".join(lines))
    print("\n".join(lines), flush=True)


def _tool_outputs(result: EvalResult, tool_name: str) -> list[str]:
    return [event.output for event in result.tools_completed() if event.tool_name == tool_name]


def _multi_subagent_evidence_ready(result: EvalResult) -> bool:
    if result.tool_count("run_subagent") < 2:
        return False
    if result.tool_count("wait_background_tasks") < 1:
        return False
    if result.tool_count("check_background_task_result") < 1:
        return False
    wait_outputs = "\n".join(_tool_outputs(result, "wait_background_tasks"))
    return "finished" in wait_outputs and "bg_1" in wait_outputs and "bg_2" in wait_outputs


def _cancel_evidence_ready(result: EvalResult) -> bool:
    if result.tool_count("run_subagent") < 1:
        return False
    if result.tool_count("cancel_background_task") < 1:
        return False
    if result.tool_count("wait_background_tasks") < 1:
        return False
    cancel_outputs = "\n".join(_tool_outputs(result, "cancel_background_task")).lower()
    wait_outputs = "\n".join(_tool_outputs(result, "wait_background_tasks")).lower()
    return (
        "early-stop requested" in cancel_outputs or "cancelled" in cancel_outputs
    ) and ("finished" in wait_outputs or "failed" in wait_outputs)


async def test_executor_launches_multiple_subagents_waits_and_checks(sandbox):
    result = await _run_executor_with_debug(
        sandbox,
        (
            "Complete this directly as the root executor; do not enter plan_for_handoff.\n"
            "Strict tool budget: exactly 2 run_subagent calls, exactly 1 "
            "check_background_task_result before waiting, exactly 1 wait_background_tasks "
            "call, then exactly 1 submit_task_completion call. Never repeat a successful "
            "wait_background_tasks call; [COMPLETED] means proceed to completion.\n"
            "Follow these steps exactly, using tools:\n"
            "1. Launch explorer subagent A with run_subagent. Its prompt must be: "
            "'Use read_file to read /home/daytona/subagent_live/alpha.txt. "
            "Return findings containing the exact marker ALPHA_SUBAGENT_OK.'\n"
            "2. Launch explorer subagent B with run_subagent. Its prompt must be: "
            "'Use read_file to read /home/daytona/subagent_live/beta.txt. "
            "Return findings containing the exact marker BETA_SUBAGENT_OK.'\n"
            "3. Before waiting, call check_background_task_result for the first task id.\n"
            "4. Call wait_background_tasks with timeout 120.\n"
            "5. Final response: include ALPHA_SUBAGENT_OK and BETA_SUBAGENT_OK.\n"
            "6. Call submit_task_completion with a concise summary.\n"
            "Do not use read_file in the parent agent."
        ),
        "multi_subagent_wait_check",
        stop_when=_multi_subagent_evidence_ready,
    )

    assert result.tool_count("run_subagent") >= 2, (
        f"Expected at least two run_subagent calls. Trace: {result.tool_names}"
    )
    assert len([e for e in result.background_started() if e.tool_name == "run_subagent"]) >= 2, (
        f"Expected two run_subagent background starts. Trace: {result.tool_names}"
    )
    assert result.tool_count("wait_background_tasks") >= 1, (
        f"Expected wait_background_tasks. Trace: {result.tool_names}"
    )
    assert result.tool_count("check_background_task_result") >= 1, (
        f"Expected check_background_task_result. Trace: {result.tool_names}"
    )

    wait_outputs = "\n".join(_tool_outputs(result, "wait_background_tasks"))
    assert "finished" in wait_outputs, f"Expected finished status in wait output:\n{wait_outputs}"
    assert "bg_1" in wait_outputs and "bg_2" in wait_outputs, (
        f"Expected both launched subagent ids in wait output:\n{wait_outputs}"
    )
    assert result.metadata["root_status"] == "done", (
        f"Expected root executor task to be closed after live evidence. "
        f"Metadata: {result.metadata}"
    )
    assert not result.has_unrecovered_errors, (
        f"Unexpected unrecovered errors: {[e.output[:500] for e in result.unrecovered_error_events]}"
    )


async def test_executor_can_cancel_launched_subagent_task(sandbox):
    result = await _run_executor_with_debug(
        sandbox,
        (
            "Complete this directly as the root executor; do not enter plan_for_handoff.\n"
            "Strict tool budget: exactly 1 run_subagent call, exactly 1 "
            "cancel_background_task call, exactly 1 wait_background_tasks call, "
            "then exactly 1 submit_task_completion call. Never repeat a successful wait.\n"
            "Follow these steps exactly, using tools:\n"
            "1. Launch one explorer subagent with run_subagent. Its prompt must be: "
            "'Read each file /home/daytona/subagent_live/cancel_payload_00.txt through "
            "/home/daytona/subagent_live/cancel_payload_11.txt individually using "
            "read_file before submitting findings.'\n"
            "2. Immediately cancel that background task using cancel_background_task "
            "with reason 'live subagent cancellation test'. Do not wait before cancelling.\n"
            "3. Call wait_background_tasks with timeout 20 so the cancellation can settle.\n"
            "4. Final response: include CANCEL_SUBAGENT_TEST_DONE.\n"
            "5. Call submit_task_completion with a concise summary.\n"
            "Do not use read_file in the parent agent."
        ),
        "cancel_subagent",
        stop_when=_cancel_evidence_ready,
    )

    assert result.tool_count("run_subagent") >= 1, (
        f"Expected run_subagent launch. Trace: {result.tool_names}"
    )
    assert len([e for e in result.background_started() if e.tool_name == "run_subagent"]) >= 1, (
        f"Expected run_subagent background start. Trace: {result.tool_names}"
    )
    assert result.tool_count("cancel_background_task") >= 1, (
        f"Expected cancel_background_task. Trace: {result.tool_names}"
    )
    assert result.tool_count("wait_background_tasks") >= 1, (
        f"Expected wait_background_tasks after cancel. Trace: {result.tool_names}"
    )

    cancel_outputs = "\n".join(_tool_outputs(result, "cancel_background_task")).lower()
    assert "early-stop requested" in cancel_outputs or "cancelled" in cancel_outputs, (
        f"Cancel tool did not report a successful cancellation:\n{cancel_outputs}"
    )
    wait_outputs = "\n".join(_tool_outputs(result, "wait_background_tasks")).lower()
    assert "finished" in wait_outputs or "failed" in wait_outputs, (
        f"Expected terminal wait output after cancel:\n{wait_outputs}"
    )
    assert result.metadata["root_status"] == "done", (
        f"Expected root executor task to be closed after live evidence. "
        f"Metadata: {result.metadata}"
    )
    assert not result.has_non_cancel_errors, (
        f"Unexpected non-cancel errors: {[e.output[:500] for e in result.non_cancel_error_events]}"
    )
