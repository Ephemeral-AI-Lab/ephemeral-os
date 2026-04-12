from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agents.registry import get_definition
from agents.types import AgentDefinition
from engine.runtime import agent as runtime_agent
from team.builtins import DEVELOPER, TEAM_PLANNER, VALIDATOR, register_all

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def setup_module() -> None:
    register_all()


def test_declared_skills_are_prepended_before_base_prompt(monkeypatch):
    monkeypatch.setattr(
        runtime_agent,
        "_build_declared_skill_preamble",
        lambda *_args, **_kwargs: "# Preloaded Skills\n\nskill body",
    )

    prompt = runtime_agent._build_agent_system_prompt(
        SimpleNamespace(cwd="/tmp"),
        AgentDefinition(name="planner", description="d", system_prompt="base prompt"),
        settings=None,
        latest_user_prompt=None,
    )

    assert prompt.startswith("# Preloaded Skills")
    assert prompt.endswith("base prompt")


def test_team_agent_preambles_surface_scope_and_search_guidance() -> None:
    config = SimpleNamespace(cwd=str(_BACKEND_ROOT))

    developer = runtime_agent._build_declared_skill_preamble(config, get_definition(DEVELOPER))
    assert "daytona_grep" in developer

    validator = runtime_agent._build_declared_skill_preamble(config, get_definition(VALIDATOR))
    assert "daytona_codeact" in validator

    planner = runtime_agent._build_declared_skill_preamble(config, get_definition(TEAM_PLANNER))
