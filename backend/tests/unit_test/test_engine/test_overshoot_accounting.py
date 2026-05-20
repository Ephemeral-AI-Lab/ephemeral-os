"""Tests for QueryContext overshoot properties (Phase 1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from engine.query.context import QueryContext


def _ctx(
    *,
    limit: int | None = 10,
    used: int = 0,
    tolerance: int | None = 5,
    text_turns: int = 0,
) -> QueryContext:
    return QueryContext(
        api_client=MagicMock(),
        tool_registry=MagicMock(),
        cwd=Path("."),
        model="m",
        system_prompt="p",
        max_tokens=1,
        tool_call_limit=limit,
        tool_calls_used=used,
        max_tolerance_after_max_tool_call=tolerance,
        text_only_no_terminal_turns=text_turns,
    )


def test_tool_overshoot_zero_below_limit() -> None:
    assert _ctx(limit=10, used=5).tool_overshoot == 0


def test_tool_overshoot_zero_at_limit() -> None:
    assert _ctx(limit=10, used=10).tool_overshoot == 0


def test_tool_overshoot_positive_past_limit() -> None:
    assert _ctx(limit=10, used=15).tool_overshoot == 5


def test_tool_overshoot_zero_without_limit() -> None:
    assert _ctx(limit=None, used=999).tool_overshoot == 0


def test_overshoot_units_sums_tool_and_text_paths() -> None:
    # 11 calls past limit=10 → tool_overshoot=1; 2 text-only turns.
    # overshoot_units = 1 + 2 = 3.
    assert _ctx(limit=10, used=11, text_turns=2).overshoot_units == 3


def test_overshoot_units_text_only_contributes_when_below_limit() -> None:
    # Calls below limit do not contribute, but text turns still do.
    assert _ctx(limit=10, used=5, text_turns=3).overshoot_units == 3


def test_overshoot_units_zero_without_limit_or_text() -> None:
    assert _ctx(limit=None, used=999, text_turns=0).overshoot_units == 0
