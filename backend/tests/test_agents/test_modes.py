"""Tests for ``ModeDefinition`` + ``AgentDefinition.modes`` (US-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.types import AgentDefinition, ModeDefinition


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_default_mode_returns_unique_default() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        terminals=["submit_task_completion"],
    )
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["read"],
        terminals=["submit_plan"],
        entry_tool="enter_plan",
        briefing="plan briefing",
    )
    a = AgentDefinition(name="ex", description="d", modes=[direct, plan])
    assert a.default_mode is direct


def test_modes_by_name_indexes_each_mode() -> None:
    direct = ModeDefinition(name="direct", is_default=True, terminals=["t1"])
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["a"],
        terminals=["t2"],
        entry_tool="e1",
        briefing="b",
    )
    a = AgentDefinition(name="x", description="d", modes=[direct, plan])
    assert a.modes_by_name == {"direct": direct, "plan": plan}


def test_mode_allowed_tools_are_explicit_lists() -> None:
    direct = ModeDefinition(
        name="direct",
        is_default=True,
        allowed_tools=["read", "write"],
        terminals=["submit_task_completion"],
    )
    plan = ModeDefinition(
        name="plan",
        allowed_tools=["read", "grep"],
        terminals=["submit_plan_handoff"],
        entry_tool="enter_plan_for_handoff",
        briefing="brief",
    )
    a = AgentDefinition(name="x", description="d", modes=[direct, plan])
    assert a.default_mode.allowed_tools == ["read", "write"]
    assert a.modes_by_name["plan"].allowed_tools == ["read", "grep"]


# --------------------------------------------------------------------------- #
# Validators (rules 1-7 from the spec)                                        #
# --------------------------------------------------------------------------- #


def test_validator_rejects_empty_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(name="x", description="d", modes=[])
    assert "non-empty" in str(exc.value)


def test_validator_rejects_zero_default_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(
                    name="a", is_default=False, terminals=["t1"], entry_tool="e", briefing="b"
                )
            ],
        )
    assert "is_default=True" in str(exc.value)


def test_validator_rejects_two_default_modes() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(name="b", is_default=True, terminals=["t2"]),
            ],
        )
    assert "is_default=True" in str(exc.value)


def test_validator_rejects_default_mode_with_entry_tool() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(
                    name="a", is_default=True, terminals=["t1"], entry_tool="oops"
                )
            ],
        )
    assert "entry_tool=None" in str(exc.value)


def test_validator_rejects_default_mode_with_briefing() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(
                    name="a", is_default=True, terminals=["t1"], briefing="oops"
                )
            ],
        )
    assert "briefing=None" in str(exc.value)


def test_validator_rejects_secondary_mode_missing_entry_tool() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(name="b", is_default=False, terminals=["t2"]),
            ],
        )
    assert "entry_tool" in str(exc.value)


def test_validator_rejects_secondary_mode_missing_briefing() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(
                    name="b",
                    is_default=False,
                    terminals=["t2"],
                    entry_tool="e1",
                ),
            ],
        )
    assert "briefing" in str(exc.value)


def test_validator_rejects_empty_terminals_in_any_mode() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[ModeDefinition(name="a", is_default=True, terminals=[])],
        )
    assert "terminal" in str(exc.value)


def test_validator_rejects_duplicate_mode_names() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(
                    name="a",
                    is_default=False,
                    terminals=["t2"],
                    entry_tool="e1",
                    briefing="b",
                ),
            ],
        )
    assert "Duplicate mode" in str(exc.value)


def test_validator_rejects_duplicate_entry_tools() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentDefinition(
            name="x",
            description="d",
            modes=[
                ModeDefinition(name="a", is_default=True, terminals=["t1"]),
                ModeDefinition(
                    name="b",
                    is_default=False,
                    terminals=["t2"],
                    entry_tool="e1",
                    briefing="b1",
                ),
                ModeDefinition(
                    name="c",
                    is_default=False,
                    terminals=["t3"],
                    entry_tool="e1",
                    briefing="b2",
                ),
            ],
        )
    assert "Duplicate entry_tool" in str(exc.value)


# --------------------------------------------------------------------------- #
# Default mode synthesis                                                      #
# --------------------------------------------------------------------------- #


def test_no_modes_no_tools_synthesizes_empty_default() -> None:
    """Agents that omit ``modes`` get an empty direct phase."""
    a = AgentDefinition(name="bare", description="d")
    assert a.default_mode.name == "direct"
    assert a.default_mode.allowed_tools == []
    assert a.default_mode.terminals == ["submit_task_completion"]


def test_flat_tools_field_is_rejected() -> None:
    direct = ModeDefinition(name="direct", is_default=True, terminals=["t1"])
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x",
            description="d",
            modes=[direct],
            tools=["ignored"],  # type: ignore[call-arg]
        )
