"""Tests for agent definition validation and builtin-name reservation."""

from __future__ import annotations

import agents.registry as registry
from types import SimpleNamespace

from agents.registry import get_definition, register_definition
from agents.types import AgentDefinition
from agents.validation import AgentDefinitionValidator
from team.definitions import register_all as _register_team_builtins


if get_definition("team_planner") is None:
    try:
        _register_team_builtins()
    except Exception:
        pass


def test_definition_validation_rejects_reserved_builtin_agent_names():
    validator = AgentDefinitionValidator(tool_registry=None)

    result = validator.validate(  # type: ignore[arg-type]
        SimpleNamespace(
            name="team_planner",
            tools=None,
            effort=None,
        )
    )

    assert result.valid is False
    assert any("reserved for a builtin runtime agent" in err for err in result.errors)


def test_reserved_builtin_agent_names_match_current_team_runtime():
    assert registry.RESERVED_BUILTIN_AGENT_NAMES == {
        "root_planner",
        "team_planner",
        "developer",
        "validator",
        "scout",
        "team_replanner",
    }


def test_register_all_restores_config_backed_reserved_agents():
    register_definition(
        AgentDefinition(
            name="team_planner",
            description="bad override",
            agent_type="subagent",
            source="builtin",
        )
    )

    overridden = get_definition("team_planner")
    assert overridden is not None
    assert overridden.agent_type == "subagent"
    _register_team_builtins()

    planner = get_definition("team_planner")
    assert planner is not None
    assert planner.agent_type == "agent"


def test_tools_csv_split():
    defn = AgentDefinition(name="dev", description="dev", tools="ci_query_symbol, ci_diagnostics")
    assert defn.tools == ["ci_query_symbol", "ci_diagnostics"]


def test_definition_validation_allows_known_tools():
    validator = AgentDefinitionValidator(tool_registry=None)

    result = validator.validate(  # type: ignore[arg-type]
        SimpleNamespace(
            name="custom_agent",
            tools=["ci_query_symbol"],
            effort=None,
        )
    )

    assert result.valid is True
    assert result.errors == []


def test_definition_validation_rejects_unknown_tools():
    validator = AgentDefinitionValidator(tool_registry=None)

    result = validator.validate(  # type: ignore[arg-type]
        SimpleNamespace(
            name="custom_agent",
            tools=["does_not_exist"],
            effort=None,
        )
    )

    assert result.valid is False
    assert "Unknown tool: does_not_exist" in result.errors
