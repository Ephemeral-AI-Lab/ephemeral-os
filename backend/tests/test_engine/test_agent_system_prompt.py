from __future__ import annotations

from types import SimpleNamespace

from agents.types import AgentDefinition
from engine.runtime import agent as runtime_agent


def test_agent_system_prompt_includes_runtime_base_and_agent_body_only(monkeypatch):
    monkeypatch.setattr(
        runtime_agent,
        "build_runtime_system_prompt",
        lambda *_args, **_kwargs: "runtime base",
    )

    prompt = runtime_agent._build_agent_system_prompt(
        SimpleNamespace(cwd="/tmp"),
        AgentDefinition(name="planner", description="d", system_prompt="base prompt"),
        settings=None,
        latest_user_prompt=None,
    )

    assert prompt.startswith("runtime base")
    assert "base prompt" in prompt
    assert "# Declared Skills" not in prompt
    assert "# Identity" not in prompt
    assert "# Type Constraints" not in prompt
    assert "# Role Boundary" not in prompt
    assert "# Skill Bootstrap" not in prompt


def test_agent_system_prompt_ignores_declared_skills(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_agent,
        "build_runtime_system_prompt",
        lambda *_args, **_kwargs: "",
    )
    agent = AgentDefinition(
        name="minimal",
        description="d",
        system_prompt="agent body",
        skills=["team-planner-playbook"],
        include_skills=True,
    )

    prompt = runtime_agent._build_agent_system_prompt(
        SimpleNamespace(cwd="/tmp"),
        agent,
        settings=None,
        latest_user_prompt=None,
    )

    assert prompt == "agent body"


def test_agent_system_prompt_injects_assigned_target_paths(monkeypatch) -> None:
    """When `target_paths` is supplied at spawn time, the system prompt
    gets an explicit "Assigned target_paths" section with one bullet per
    path. This surfaces the scope to the model on every turn, not just in
    the first user message."""
    monkeypatch.setattr(
        runtime_agent,
        "build_runtime_system_prompt",
        lambda *_args, **_kwargs: "",
    )

    prompt = runtime_agent._build_agent_system_prompt(
        SimpleNamespace(cwd="/tmp"),
        AgentDefinition(name="scout", description="d", system_prompt="scout body"),
        settings=None,
        latest_user_prompt=None,
        target_paths=["dask/dataframe/_compat.py", "dask/dataframe/utils.py"],
    )

    assert "scout body" in prompt
    assert "## Assigned target_paths" in prompt
    assert "- dask/dataframe/_compat.py" in prompt
    assert "- dask/dataframe/utils.py" in prompt


def test_agent_system_prompt_omits_scope_block_when_target_paths_empty(monkeypatch) -> None:
    """Empty/None target_paths → no scope block. Prevents misleading
    'Assigned target_paths: (none)' framing when the caller didn't
    declare any scope."""
    monkeypatch.setattr(
        runtime_agent,
        "build_runtime_system_prompt",
        lambda *_args, **_kwargs: "",
    )

    for empty in (None, []):
        prompt = runtime_agent._build_agent_system_prompt(
            SimpleNamespace(cwd="/tmp"),
            AgentDefinition(name="scout", description="d", system_prompt="scout body"),
            settings=None,
            latest_user_prompt=None,
            target_paths=empty,
        )
        assert "Assigned target_paths" not in prompt
