"""Offline regressions for mock delegated-workflow polling."""

from __future__ import annotations

import json

import pytest

from message.message import ToolResultBlock
from test_runner.agent.mock import scenario_adapter
from test_runner.scenarios.base import ScenarioContext


class _Scenario:
    name = "polling_regression"


def _result(payload: dict[str, object], *, is_error: bool = False) -> list[ToolResultBlock]:
    return [
        ToolResultBlock(
            tool_use_id="toolu_test",
            content=json.dumps(payload),
            is_error=is_error,
        )
    ]


@pytest.mark.asyncio
async def test_root_script_does_not_cancel_after_legacy_short_poll_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scenario_adapter, "_DELEGATED_WORKFLOW_POLL_INTERVAL_S", 0.0)
    ctx = ScenarioContext(
        attempt=None,
        iteration=None,
        workflow=None,
        prompt="Run the delegated workflow.",
        metadata={},
        audit_recorder=None,
    )
    script = scenario_adapter._root_script(_Scenario(), ctx)  # noqa: SLF001

    turn = await script.asend(None)
    assert turn.calls[0].name == "delegate_workflow"

    turn = await script.asend(
        _result({"workflow_id": "workflow-1", "workflow_task_id": "task-1"})
    )
    for _ in range(50):
        assert turn.calls[0].name == "check_workflow_status"
        turn = await script.asend(_result({"status": "open"}))

    assert turn.calls[0].name == "check_workflow_status"

    turn = await script.asend(_result({"status": "succeeded"}))
    assert turn.calls[0].name == "submit_root_outcome"
    assert turn.calls[0].input == {
        "status": "success",
        "outcome": "Root delegated workflow completed.",
    }
