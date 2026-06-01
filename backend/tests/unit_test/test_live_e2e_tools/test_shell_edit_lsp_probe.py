from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LspExpectation:
    symbol: str
    source_path: str
    source_anchor: str
    definition_path: str
    definition_anchor: str
    min_references: int
    hover_contains: tuple[str, ...]


def _symbol_cursor_offset(line: str, anchor: str, symbol: str) -> int:
    character = line.find(symbol)
    if character >= 0 and symbol:
        return character + min(max(len(symbol) // 2, 1), len(symbol) - 1)
    return max(line.find(anchor), 0)


def _hover_expectations(
    expectations: Sequence[LspExpectation],
) -> tuple[LspExpectation, ...]:
    return tuple(
        expectation
        for expectation in expectations
        if (
            expectation.source_path != expectation.definition_path
            or expectation.source_anchor != expectation.definition_anchor
        )
    )


def test_symbol_cursor_offset_targets_symbol_interior() -> None:
    assert _symbol_cursor_offset("class Schedule:", "class Schedule:", "Schedule") == 10


def test_symbol_cursor_offset_falls_back_to_anchor_start() -> None:
    assert _symbol_cursor_offset("    @dataclass", "@dataclass", "Schedule") == 4


def test_hover_expectations_prefer_use_site_anchors() -> None:
    definition_anchor = LspExpectation(
        symbol="Schedule",
        source_path="scheduler_demo/domain/schedule.py",
        source_anchor="class Schedule:",
        definition_path="scheduler_demo/domain/schedule.py",
        definition_anchor="class Schedule:",
        min_references=2,
        hover_contains=("Schedule",),
    )
    use_anchor = LspExpectation(
        symbol="Task",
        source_path="tests/test_task.py",
        source_anchor='task = Task(task_id="t")',
        definition_path="scheduler_demo/domain/task.py",
        definition_anchor="class Task:",
        min_references=2,
        hover_contains=("Task",),
    )

    assert _hover_expectations((definition_anchor, use_anchor)) == (use_anchor,)


def test_hover_expectations_return_empty_for_definition_only_anchors() -> None:
    definition_anchor = LspExpectation(
        symbol="Schedule",
        source_path="scheduler_demo/domain/schedule.py",
        source_anchor="class Schedule:",
        definition_path="scheduler_demo/domain/schedule.py",
        definition_anchor="class Schedule:",
        min_references=2,
        hover_contains=("Schedule",),
    )

    assert _hover_expectations((definition_anchor,)) == ()
