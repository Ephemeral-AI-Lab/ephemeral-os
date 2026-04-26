# ruff: noqa
"""Live E2E: EvalAgent subagent background workflow.

Validates the real live stack for:
- launching multiple explorer subagents via run_subagent
- checking in-flight/final background task results
- waiting for background tasks to finish
- cancelling a launched background task

Run with:
    uv run pytest backend/tests/test_e2e/test_subagent_background_live.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

import pytest

from engine.testing.eval_agent import EvalAgent, EvalResult
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox
from tests.test_e2e.daytona_exec_io import write_text_via_exec
from tests.test_e2e.helpers import log_result

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required"),
]


AGENT_PROMPT = """\
You are subagent-background-live-eval, a test harness agent with a Daytona sandbox.

Rules:
- Use tools for every operational step.
- Use run_subagent with agent_name="explorer" when asked to delegate exploration.
- run_subagent is asynchronous and returns a background task id immediately.
- Copy exact task_id values from tool results into check_background_task_result,
  wait_background_tasks, and cancel_background_task.
- Do not read delegated files yourself when the prompt asks you to use subagents.
- Keep final text brief and include the marker strings you observed.
"""


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


async def _invoke_with_debug(agent: EvalAgent, prompt: str, label: str) -> EvalResult:
    """Run EvalAgent with live event output and explicit traceback logging."""
    try:
        result = await agent.invoke(prompt, verbose=True)
    except Exception:
        tb = traceback.format_exc()
        logger.error("[%s] EvalAgent invocation traceback:\n%s", label, tb)
        print(f"\n[{label}] EvalAgent invocation traceback:\n{tb}", flush=True)
        raise

    log_result(result, label)
    _log_tool_trace(result, label)
    return result


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


def _json_outputs(result: EvalResult, tool_name: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in result.tools_completed():
        if event.tool_name != tool_name:
            continue
        try:
            payload = json.loads(event.output)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _tool_outputs(result: EvalResult, tool_name: str) -> list[str]:
    return [event.output for event in result.tools_completed() if event.tool_name == tool_name]


async def test_eval_agent_launches_multiple_subagents_waits_and_checks(sandbox):
    agent = create_eval_agent(
        system_prompt=AGENT_PROMPT,
        sandbox_id=sandbox["id"],
        tool_call_limit=40,
    )

    result = await _invoke_with_debug(
        agent,
        (
            "Follow these steps exactly, using tools:\n"
            "1. Launch explorer subagent A with run_subagent. Its prompt must be: "
            "'Use daytona_read_file to read /home/daytona/subagent_live/alpha.txt. "
            "Return findings containing the exact marker ALPHA_SUBAGENT_OK.'\n"
            "2. Launch explorer subagent B with run_subagent. Its prompt must be: "
            "'Use daytona_read_file to read /home/daytona/subagent_live/beta.txt. "
            "Return findings containing the exact marker BETA_SUBAGENT_OK.'\n"
            "3. Before waiting, call check_background_task_result for the first task id.\n"
            "4. Call wait_background_tasks with timeout 120.\n"
            "5. Call check_background_task_result for both task ids after the wait.\n"
            "6. Final response: include ALPHA_SUBAGENT_OK and BETA_SUBAGENT_OK.\n"
            "Do not use daytona_read_file in the parent agent."
        ),
        "multi_subagent_wait_check",
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
    assert result.tool_count("check_background_task_result") >= 2, (
        f"Expected check_background_task_result for task results. Trace: {result.tool_names}"
    )

    checked = _json_outputs(result, "check_background_task_result")
    checked_text = "\n".join(json.dumps(payload, sort_keys=True) for payload in checked)
    assert "ALPHA_SUBAGENT_OK" in checked_text, (
        f"Missing alpha marker from checked background results. Payloads:\n{checked_text}"
    )
    assert "BETA_SUBAGENT_OK" in checked_text, (
        f"Missing beta marker from checked background results. Payloads:\n{checked_text}"
    )
    assert any(payload.get("status") == "finished" for payload in checked), (
        f"Expected at least one finished checked payload. Payloads:\n{checked_text}"
    )

    wait_outputs = "\n".join(_tool_outputs(result, "wait_background_tasks"))
    assert "finished" in wait_outputs, f"Expected finished status in wait output:\n{wait_outputs}"
    assert not result.has_unrecovered_errors, (
        f"Unexpected unrecovered errors: {[e.output[:500] for e in result.unrecovered_error_events]}"
    )


async def test_eval_agent_can_cancel_launched_subagent_task(sandbox):
    agent = create_eval_agent(
        system_prompt=AGENT_PROMPT,
        sandbox_id=sandbox["id"],
        tool_call_limit=30,
    )

    result = await _invoke_with_debug(
        agent,
        (
            "Follow these steps exactly, using tools:\n"
            "1. Launch one explorer subagent with run_subagent. Its prompt must be: "
            "'Read each file /home/daytona/subagent_live/cancel_payload_00.txt through "
            "/home/daytona/subagent_live/cancel_payload_11.txt individually using "
            "daytona_read_file before submitting findings.'\n"
            "2. Immediately cancel that background task using cancel_background_task "
            "with reason 'live subagent cancellation test'. Do not wait before cancelling.\n"
            "3. Call wait_background_tasks with timeout 20 so the cancellation can settle.\n"
            "4. Call check_background_task_result for the cancelled task id.\n"
            "5. Final response: include CANCEL_SUBAGENT_TEST_DONE.\n"
            "Do not use daytona_read_file in the parent agent."
        ),
        "cancel_subagent",
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
    assert result.tool_count("check_background_task_result") >= 1, (
        f"Expected check_background_task_result after cancel. Trace: {result.tool_names}"
    )

    cancel_outputs = "\n".join(_tool_outputs(result, "cancel_background_task")).lower()
    assert "early-stop requested" in cancel_outputs or "cancelled" in cancel_outputs, (
        f"Cancel tool did not report a successful cancellation:\n{cancel_outputs}"
    )

    checked = _json_outputs(result, "check_background_task_result")
    checked_text = "\n".join(json.dumps(payload, sort_keys=True) for payload in checked)
    assert any(payload.get("status") in {"failed", "finished"} for payload in checked), (
        f"Expected terminal checked payload after cancel. Payloads:\n{checked_text}"
    )
    assert not result.has_non_cancel_errors, (
        f"Unexpected non-cancel errors: {[e.output[:500] for e in result.non_cancel_error_events]}"
    )
