"""TaskCenter entry sandbox auto-create tests."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from agents import registry as agents_registry
from agents.types import AgentDefinition
from engine.runtime.lifecycle import EphemeralRunResult
from server.app_factory import RuntimeConfig
from task_center.entry import start_task_center_entry_run


@pytest.fixture
def register_entry_agents(isolated_agent_registries) -> None:
    agents_registry.register_definition(
        AgentDefinition(
            name="entry_executor",
            description="test entry executor",
            role="executor",
            context_recipe="entry_executor_v1",
            terminals=["submit_execution_success", "submit_execution_failure"],
        )
    )
    agents_registry.register_definition(
        AgentDefinition(
            name="planner",
            description="test planner",
            role="planner",
            context_recipe="planner_v1",
            terminals=["submit_full_plan", "submit_partial_plan"],
        )
    )


@pytest.mark.asyncio
async def test_entry_autocreates_sandbox_and_runs_setup(
    monkeypatch: pytest.MonkeyPatch,
    register_entry_agents,
    mission_store,
    episode_store,
    attempt_store,
    task_store,
    context_packet_store,
    tmp_path,
) -> None:
    from sandbox.api import status as sb_status
    from sandbox.providers import registry as provider_registry
    from sandbox.providers.registry import set_default_provider

    monkeypatch.setattr(provider_registry, "_ADAPTERS", {}, raising=False)
    monkeypatch.setattr(provider_registry, "_DEFAULT", None, raising=False)
    monkeypatch.setattr(provider_registry, "_LOCK", threading.Lock(), raising=False)

    provider = MagicMock(name="provider")
    provider.create.return_value = {
        "id": "sb-auto",
        "state": "started",
        "project_dir": "/workspace/demo",
    }
    set_default_provider(provider)

    setup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sb_status,
        "setup_after_create",
        lambda sandbox_id, project_dir: setup_calls.append(
            (sandbox_id, project_dir)
        ),
    )

    captured: list[dict[str, object]] = []

    async def fake_runner(*args, **kwargs):
        del args
        captured.append(kwargs)
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name=kwargs["agent_def"].name,
            event_count=0,
        )

    entry = start_task_center_entry_run(
        config=RuntimeConfig(cwd=str(tmp_path)),
        prompt="needs sandbox",
        sandbox_id=None,
        on_agent_event=None,
        task_store=task_store,
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        context_packet_store=context_packet_store,
        runner=fake_runner,
    )
    await entry.launcher.wait_for_idle()

    persisted_request = task_store.get_request(entry.request_id)
    assert entry.binding.sandbox_id == "sb-auto"
    assert entry.binding.owned_by_task_center is True
    assert persisted_request is not None
    assert persisted_request["sandbox_id"] == "sb-auto"
    assert captured[0]["sandbox_id"] == "sb-auto"
    assert setup_calls == [("sb-auto", "/workspace/demo")]

    provider.create.assert_called_once()
    create_kwargs = provider.create.call_args.kwargs
    assert create_kwargs["name"].startswith("task-center-")
    assert create_kwargs["labels"] == {
        "origin": "task_center",
        "task_center_run_id": entry.task_center_run_id,
    }


@pytest.mark.asyncio
async def test_entry_prepares_explicit_sandbox_without_create(
    monkeypatch: pytest.MonkeyPatch,
    register_entry_agents,
    mission_store,
    episode_store,
    attempt_store,
    task_store,
    context_packet_store,
    tmp_path,
) -> None:
    from sandbox.api import status as sb_status
    from sandbox.providers import registry as provider_registry
    from sandbox.providers.registry import register_adapter

    monkeypatch.setattr(provider_registry, "_ADAPTERS", {}, raising=False)
    monkeypatch.setattr(provider_registry, "_DEFAULT", None, raising=False)
    monkeypatch.setattr(provider_registry, "_LOCK", threading.Lock(), raising=False)

    provider = MagicMock(name="provider")
    provider.start.return_value = {
        "id": "sb-explicit",
        "state": "started",
        "project_dir": "/workspace/explicit",
    }
    register_adapter("sb-explicit", provider)

    setup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sb_status,
        "setup_after_start",
        lambda sandbox_id, project_dir: setup_calls.append(
            (sandbox_id, project_dir)
        ),
    )

    captured: list[dict[str, object]] = []

    async def fake_runner(*args, **kwargs):
        del args
        captured.append(kwargs)
        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=None,
            agent_name=kwargs["agent_def"].name,
            event_count=0,
        )

    entry = start_task_center_entry_run(
        config=RuntimeConfig(cwd=str(tmp_path)),
        prompt="use caller sandbox",
        sandbox_id="sb-explicit",
        on_agent_event=None,
        task_store=task_store,
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        context_packet_store=context_packet_store,
        runner=fake_runner,
    )
    await entry.launcher.wait_for_idle()

    persisted_request = task_store.get_request(entry.request_id)
    assert entry.binding.sandbox_id == "sb-explicit"
    assert entry.binding.owned_by_task_center is False
    assert persisted_request is not None
    assert persisted_request["sandbox_id"] == "sb-explicit"
    assert captured[0]["sandbox_id"] == "sb-explicit"
    provider.create.assert_not_called()
    provider.start.assert_called_once_with("sb-explicit")
    assert setup_calls == [("sb-explicit", "/workspace/explicit")]
