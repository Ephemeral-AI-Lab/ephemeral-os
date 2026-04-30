"""Helper and explorer submission tool tests."""

from __future__ import annotations

import pytest

from agents import register_definition, unregister_definition
from agents.types import AgentDefinition
from engine.runtime.lifecycle import EphemeralRunResult
from tools.core.context import ToolExecutionContextService
from tools.core.results import ToolResult
from tools.core.runtime import ExecutionMetadata
from tools.core.tool_execution import execute_tool_once
from tools.submission.helper_agent.advisor import (
    ask_advisor,
    submit_advisor_feedback,
)
from tools.submission.helper_agent.resolver import (
    ask_resolver,
    submit_resolver_result,
)
from tools.submission.subagent.explorer import submit_exploration_result

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


def _context(*, role: str = "", agent_type: str = "agent") -> ToolExecutionContextService:
    metadata = ExecutionMetadata(runtime_config=object())
    if role:
        metadata["role"] = role
    if agent_type:
        metadata["agent_type"] = agent_type
    return ToolExecutionContextService(cwd="/tmp", services=metadata)


async def test_submit_advisor_feedback_metadata_contains_verdict() -> None:
    result = await execute_tool_once(
        submit_advisor_feedback,
        {"verdict": "revise", "summary": "tighten scope", "risks": ["risk"]},
        _context(role="advisor"),
        emit=_noop_emit,
    )

    assert not result.is_error
    assert result.metadata["helper_role"] == "advisor"
    assert result.metadata["verdict"] == "revise"


async def test_submit_resolver_result_metadata_drives_unresolved_count() -> None:
    result = await execute_tool_once(
        submit_resolver_result,
        {
            "resolved": False,
            "summary": "partially fixed",
            "changed_files": ["a.py"],
            "remaining_issues": ["still broken"],
        },
        _context(role="resolver"),
        emit=_noop_emit,
    )

    assert not result.is_error
    assert result.metadata["resolver"]["resolved"] is False
    assert result.metadata["changed_files"] == ["a.py"]


async def test_submit_exploration_result_returns_subagent_findings() -> None:
    result = await execute_tool_once(
        submit_exploration_result,
        {
            "summary": "found it",
            "findings": ["finding"],
            "references": ["file.py"],
        },
        _context(role="explorer", agent_type="subagent"),
        emit=_noop_emit,
    )

    assert not result.is_error
    assert result.metadata["subagent_role"] == "explorer"
    assert result.metadata["findings"] == ["finding"]


async def test_helper_role_gate_blocks_wrong_helper_terminal_role() -> None:
    result = await execute_tool_once(
        submit_resolver_result,
        {
            "resolved": True,
            "summary": "done",
            "changed_files": [],
            "remaining_issues": [],
        },
        _context(role="advisor"),
        emit=_noop_emit,
    )

    assert result.is_error
    assert "resolver runs" in result.output


async def test_ask_advisor_runs_advisor_and_returns_terminal_feedback(monkeypatch) -> None:
    register_definition(
        AgentDefinition(
            name="advisor",
            description="advisor",
            role="advisor",
            terminals=["submit_advisor_feedback"],
        )
    )
    seen: dict[str, object] = {}

    async def _fake_run(*args, **kwargs):
        seen["agent_def"] = kwargs["agent_def"].name
        seen["prompt"] = args[1]
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="approved",
                metadata={"helper_role": "advisor", "verdict": "approve"},
            ),
            agent_name="advisor",
            event_count=1,
        )

    monkeypatch.setattr("engine.runtime.lifecycle.run_ephemeral_agent", _fake_run)
    try:
        result = await execute_tool_once(
            ask_advisor,
            {
                "tool_name": "submit_full_plan",
                "tool_payloads": [{"task": "a"}],
                "prompt": "review this",
            },
            _context(role="planner"),
            emit=_noop_emit,
        )
    finally:
        unregister_definition("advisor")

    assert not result.is_error
    assert result.output == "approved"
    assert result.metadata["verdict"] == "approve"
    assert seen["agent_def"] == "advisor"


async def test_ask_resolver_runs_resolver_and_returns_terminal_result(monkeypatch) -> None:
    register_definition(
        AgentDefinition(
            name="resolver",
            description="resolver",
            role="resolver",
            terminals=["submit_resolver_result"],
        )
    )

    async def _fake_run(*args, **kwargs):
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=ToolResult(
                output="resolved",
                metadata={
                    "helper_role": "resolver",
                    "resolver": {"resolved": True, "remaining_issues": []},
                },
            ),
            agent_name="resolver",
            event_count=1,
        )

    monkeypatch.setattr("engine.runtime.lifecycle.run_ephemeral_agent", _fake_run)
    try:
        result = await execute_tool_once(
            ask_resolver,
            {"issues_to_resolve": ["fix bug"], "issue_context": "context"},
            _context(role="verifier"),
            emit=_noop_emit,
        )
    finally:
        unregister_definition("resolver")

    assert not result.is_error
    assert result.metadata["resolver"]["resolved"] is True
