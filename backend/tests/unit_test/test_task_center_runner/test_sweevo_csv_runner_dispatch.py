"""Tests for the selective entry-mock runner factory.

Covers the two dispatch paths:

- ``agent_def.name == "entry_executor"`` → ``submit_execution_handoff``
  is called once with the captured CSV ``goal`` string.
- Any other agent name → the closure forwards the frozen launcher kwarg
  set to ``engine.api.run_ephemeral_agent``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from message.messages import ToolUseBlock
from message.stream_events import (
    AssistantMessageComplete,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from task_center_runner.benchmarks.sweevo import csv_runner as csv_runner_mod
from tools._framework.core.results import ToolResult
from tools._framework.core.runtime import ExecutionMetadata


def _agent_def(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _fake_tool_result() -> ToolResult:
    return ToolResult(
        output="started_delegated_goal",
        metadata={"submission_kind": "goal_start"},
        is_error=False,
        does_terminate=True,
    )


@pytest.mark.asyncio
async def test_factory_returns_callable_runner() -> None:
    factory = csv_runner_mod.build_selective_entry_mock_runner_factory(
        goal="G", repo_dir="/r"
    )
    runner = factory(MagicMock())
    assert callable(runner)


@pytest.mark.asyncio
async def test_entry_executor_calls_submit_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_execute_tool_once(tool_obj, raw_input, _ctx, **_kw):
        captured["tool_obj"] = tool_obj
        captured["raw_input"] = dict(raw_input)
        captured["ctx"] = _ctx
        return _fake_tool_result()

    monkeypatch.setattr(csv_runner_mod, "execute_tool_once", fake_execute_tool_once)

    factory = csv_runner_mod.build_selective_entry_mock_runner_factory(
        goal="MY_GOAL_STRING\nwith newlines", repo_dir="/repo"
    )
    runner = factory(MagicMock())

    result = await runner(
        config=MagicMock(),
        prompt="ignored",
        agent_def=_agent_def("entry_executor"),
        sandbox_id="sbx-99",
        extra_tool_metadata=ExecutionMetadata(),
    )

    assert captured["tool_obj"].name == "submit_execution_handoff"
    assert captured["raw_input"] == {"goal": "MY_GOAL_STRING\nwith newlines"}
    assert result.status == "completed"
    assert result.agent_name == "entry_executor"
    assert result.terminal_result is not None


@pytest.mark.asyncio
async def test_entry_executor_emits_tool_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_tool_once(_tool, _raw, _ctx, **_kw):
        return _fake_tool_result()

    monkeypatch.setattr(csv_runner_mod, "execute_tool_once", fake_execute_tool_once)

    factory = csv_runner_mod.build_selective_entry_mock_runner_factory(
        goal="G", repo_dir="/r"
    )
    runner = factory(MagicMock())

    events: list[Any] = []

    async def on_event(event: Any) -> None:
        events.append(event)

    await runner(
        config=MagicMock(),
        prompt="",
        agent_def=_agent_def("entry_executor"),
        sandbox_id="sbx-1",
        on_event=on_event,
        extra_tool_metadata=ExecutionMetadata(),
    )

    # Find the three stream events we care about and assert shape.
    msg_complete = next(e for e in events if isinstance(e, AssistantMessageComplete))
    started = next(e for e in events if isinstance(e, ToolExecutionStarted))
    completed = next(e for e in events if isinstance(e, ToolExecutionCompleted))

    tool_use = msg_complete.message.content[0]
    assert isinstance(tool_use, ToolUseBlock)
    assert tool_use.name == "submit_execution_handoff"
    assert tool_use.input == {"goal": "G"}

    assert started.tool_name == "submit_execution_handoff"
    assert started.tool_input == {"goal": "G"}
    assert started.tool_id == tool_use.id
    assert completed.tool_id == tool_use.id
    assert completed.tool_name == "submit_execution_handoff"


@pytest.mark.asyncio
async def test_non_entry_falls_through_to_real_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_ephemeral_agent(config, prompt, **kwargs):
        captured["config"] = config
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return SimpleNamespace(status="completed", terminal_result=None)

    # The closure does a lazy ``from engine.api import run_ephemeral_agent``,
    # so monkeypatch on the source module.
    import engine.api as engine_api

    monkeypatch.setattr(engine_api, "run_ephemeral_agent", fake_run_ephemeral_agent)

    raising_execute_tool_once = MagicMock(
        side_effect=AssertionError("execute_tool_once must NOT be called for non-entry agents")
    )
    monkeypatch.setattr(csv_runner_mod, "execute_tool_once", raising_execute_tool_once)

    factory = csv_runner_mod.build_selective_entry_mock_runner_factory(
        goal="G", repo_dir="/r"
    )
    runner = factory(MagicMock())

    config_obj = MagicMock()
    metadata = ExecutionMetadata()
    result = await runner(
        config=config_obj,
        prompt="planner_prompt",
        agent_def=_agent_def("planner"),
        sandbox_id="sbx-2",
        persist_agent_run=True,
        task_id="t-planner",
        on_event=None,
        extra_tool_metadata=metadata,
    )

    assert result.status == "completed"
    assert captured["config"] is config_obj
    assert captured["prompt"] == "planner_prompt"
    forwarded = captured["kwargs"]
    assert forwarded["agent_def"].name == "planner"
    assert forwarded["sandbox_id"] == "sbx-2"
    assert forwarded["persist_agent_run"] is True
    assert forwarded["task_id"] == "t-planner"
    assert forwarded["on_event"] is None
    assert forwarded["extra_tool_metadata"] is metadata
    raising_execute_tool_once.assert_not_called()


@pytest.mark.asyncio
async def test_metadata_overrides_applied_to_tool_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_execute_tool_once(_tool, _raw, ctx, **_kw):
        # Proxy attributes are forwarded by ToolExecutionContextService.__getattr__
        # onto the inner ExecutionMetadata (see tools/_framework/core/context.py).
        captured["sandbox_id"] = ctx.sandbox_id
        captured["agent_name"] = ctx.agent_name
        captured["repo_root"] = ctx.repo_root
        captured["exec_cwd"] = ctx.exec_cwd
        captured["tool_id"] = ctx.get("tool_id", "")
        captured["cwd"] = ctx.cwd
        return _fake_tool_result()

    monkeypatch.setattr(csv_runner_mod, "execute_tool_once", fake_execute_tool_once)

    factory = csv_runner_mod.build_selective_entry_mock_runner_factory(
        goal="G", repo_dir="/the/repo"
    )
    runner = factory(MagicMock())

    metadata = ExecutionMetadata()
    await runner(
        config=MagicMock(),
        prompt="",
        agent_def=_agent_def("entry_executor"),
        sandbox_id="sbx-42",
        extra_tool_metadata=metadata,
    )

    assert captured["sandbox_id"] == "sbx-42"
    assert captured["agent_name"] == "entry_executor"
    assert captured["repo_root"] == "/the/repo"
    assert captured["exec_cwd"] == "/the/repo"
    assert str(captured["tool_id"]).startswith("toolu_")
    assert str(captured["cwd"]) == "/the/repo"
