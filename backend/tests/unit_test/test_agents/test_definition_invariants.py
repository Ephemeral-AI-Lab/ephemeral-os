"""Boot-time invariants enforced by ``AgentDefinition``.

Every agent must declare at least one terminal tool and a positive-integer
``tool_call_limit``. Legacy keys that the refactor deleted must fail
``extra='forbid'`` validation rather than be silently accepted.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents import AgentDefinition


def test_empty_terminals_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x",
            description="y",
            terminals=[],
            tool_call_limit=10,
        )


def test_missing_tool_call_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x",
            description="y",
            terminals=["submit_x"],
        )


def test_legacy_max_tolerance_key_rejected() -> None:
    """The deleted ``max_tolerance_after_max_tool_call`` key must fail loud."""
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate(
            {
                "name": "x",
                "description": "y",
                "terminals": ["submit_x"],
                "tool_call_limit": 10,
                "max_tolerance_after_max_tool_call": 5,
            }
        )


def test_terminals_with_only_whitespace_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentDefinition(
            name="x",
            description="y",
            terminals=["  ", ""],
            tool_call_limit=10,
        )
