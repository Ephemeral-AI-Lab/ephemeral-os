"""Tests for ``ModeDefinition`` + ``AgentDefinition.modes``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.types import AgentDefinition, ModeDefinition


def test_default_mode_returns_unique_default() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        terminals=["submit_task_completion"],
    )
    agent = AgentDefinition(name="ex", description="d", modes=[direct])

    assert agent.default_mode is direct


def test_mode_allowed_tools_are_explicit_lists() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=["read", "write"],
        terminals=["submit_task_completion", "submit_plan_handoff"],
    )
    agent = AgentDefinition(name="x", description="d", modes=[direct])

    assert agent.default_mode.allowed_tools == ["read", "write"]
    assert agent.default_mode.terminals == [
        "submit_task_completion",
        "submit_plan_handoff",
    ]


def test_validator_rejects_empty_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(name="x", description="d", modes=[])

    assert "non-empty" in str(exc.value)


def test_validator_rejects_zero_default_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[ModeDefinition(name="a", terminals=["t1"])],
        )

    assert "is_default=True" in str(exc.value)


def test_validator_rejects_multiple_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(name="b", is_default=False, terminals=["t2"]),
            ],
        )

    assert "exactly one default tool surface" in str(exc.value)


def test_validator_rejects_empty_terminals() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[ModeDefinition(name="a", is_default=True, terminals=[])],
        )

    assert "terminal" in str(exc.value)


def test_no_modes_no_tools_synthesizes_empty_default() -> None:
    """Agents that omit ``modes`` get an empty direct surface."""
    agent = AgentDefinition(name="bare", description="d")

    assert agent.default_mode.name == "direct"
    assert agent.default_mode.allowed_tools == []
    assert agent.default_mode.terminals == ["submit_task_completion"]


def test_flat_tools_field_is_rejected() -> None:
    direct = ModeDefinition(name="direct", is_default=True, terminals=["t1"])

    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x",
            description="d",
            modes=[direct],
            tools=["ignored"],  # type: ignore[call-arg]
        )
