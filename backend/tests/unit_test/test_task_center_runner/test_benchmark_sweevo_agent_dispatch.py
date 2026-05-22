"""Tests for the SWE-EVO benchmark runner factory."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from task_center_runner.benchmarks.sweevo import agent_runner as agent_runner_mod
from tools._framework.core.runtime import ExecutionMetadata


def _agent_def(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_factory_returns_callable_runner() -> None:
    factory = agent_runner_mod.build_benchmark_sweevo_delegate_factory(repo_dir="/r")
    runner = factory(MagicMock())
    assert callable(runner)


@pytest.mark.asyncio
async def test_runner_delegates_launch_to_real_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_ephemeral_agent(config, prompt, **kwargs):
        captured["config"] = config
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return SimpleNamespace(status="completed", terminal_result=None)

    import engine.api as engine_api

    monkeypatch.setattr(engine_api, "run_ephemeral_agent", fake_run_ephemeral_agent)

    factory = agent_runner_mod.build_benchmark_sweevo_delegate_factory(repo_dir="/r")
    runner = factory(MagicMock())

    config_obj = MagicMock()
    metadata = ExecutionMetadata()
    initial_messages = [MagicMock()]
    result = await runner(
        config=config_obj,
        prompt="planner_prompt",
        agent_def=_agent_def("planner"),
        sandbox_id="sbx-2",
        persist_agent_run=True,
        task_id="t-planner",
        on_event=None,
        extra_tool_metadata=metadata,
        initial_messages=initial_messages,
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
    assert forwarded["initial_messages"] is initial_messages
    forwarded_metadata = forwarded["extra_tool_metadata"]
    assert forwarded_metadata is not metadata
    assert forwarded_metadata.repo_root == "/r"
    assert forwarded_metadata.exec_cwd == "/r"
    assert forwarded_metadata.sandbox_id == "sbx-2"
    assert forwarded_metadata.agent_name == "planner"
